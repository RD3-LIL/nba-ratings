#!/usr/bin/env python3
"""Build distributions.html: a histogram plus a smooth KDE line for
every player stat, with hover/tap to see players in a bin and a
search box that marks one player on the distribution.

Reads data/players_2026_27_eval.json, data/sharp_2026_27.json
and data/buck_2026_27.json. BUCK is qualified only (min 2 games,
40% of team minutes), like the BUCK page Top 25, so its Top 5
is not small-sample players. Values are kept to 3 decimals in
the page data, so BUCK does not collapse into false ties.
The search marker uses the same 1st to 99th percentile chart
range as the bars, not the full min/max range.
Static page, vanilla JS only, no external libraries.
"""
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def player_slug(name, athlete_id, team):
    norm = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in norm if not unicodedata.combining(c))
    name_s = "".join(c if c.isalnum() else "-" for c in ascii_name.lower())
    name_s = "-".join(p for p in name_s.split("-") if p)
    team_s = "".join(c if c.isalnum() else "-" for c in team.lower())
    team_s = "-".join(p for p in team_s.split("-") if p)
    return f"{name_s}-{athlete_id}-{team_s}"


STATS = [
    ("RPR", "RPR", 2, False),
    ("PPG", "PPG", 1, False),
    ("RPG", "RPG", 1, False),
    ("APG", "APG", 1, False),
    ("ORtg", "ORtg (full)", 1, False),
    ("TS%", "TS%", 1, True),
    ("SHARP", "SHARP", 2, False),
    ("FG%", "FG%", 1, True),
    ("3P%", "3P%", 1, True),
    ("FT%", "FT%", 1, True),
    ("eFG%", "eFG%", 1, True),
    ("BUCK", "BUCK", 3, False),
]

TIPS = {
    "RPR": "Our single-number rating for how much a player helps his team, where an average player scores about 0 and a star scores about +3 to +7.",
    "PPG": "Points scored per game.",
    "RPG": "Rebounds grabbed per game.",
    "APG": "Assists per game.",
    "ORtg": "Points the player produces per 100 possessions he uses, with credit for assists.",
    "TS%": "Points scored per shooting attempt, counting twos, threes, and free throws together.",
    "SHARP": "Extra points per game from threes and free throws above league average.",
    "FG%": "Share of all field goal attempts made.",
    "3P%": "Share of three point attempts made.",
    "FT%": "Share of free throw attempts made.",
    "eFG%": "Field goal percentage adjusted so a three counts more than a two, since it is worth more points.",
    "BUCK": "RPR per $1M of average salary over the years left on the deal, qualified players only (min 2 games, 40% of team minutes). 0 is an average player, positive is above average per dollar, negative is below average however cheap. See the BUCK page for the formula and the traps.",
}


def build_players():
    eval_doc = json.loads((DATA / "players_2026_27_eval.json").read_text())
    sharp_doc = json.loads((DATA / "sharp_2026_27.json").read_text())
    sharp = {p["athlete_id"]: p["sharp"] for p in sharp_doc["players"]}
    buck_doc = json.loads((DATA / "buck_2026_27.json").read_text())
    buck = {(p["athlete_id"], p["team"]): p.get("buck_full", p["buck"])
            for p in buck_doc["players"]}
    out = []
    for p in eval_doc["players"]:
        ev = p.get("eval", {})
        rt = p.get("ratings", {})
        sh = p.get("shooting", {})
        pg = p.get("per_game", {})
        vals = {
            "RPR": rt.get("rpr"),
            "PPG": pg.get("pts"),
            "RPG": pg.get("reb"),
            "APG": pg.get("ast"),
            "ORtg": ev.get("ortg_full"),
            "TS%": (sh.get("ts_pct") * 100) if sh.get("ts_pct") is not None else None,
            "SHARP": sharp.get(p["athlete_id"]),
            "FG%": (sh.get("fg_pct") * 100) if sh.get("fg_pct") is not None else None,
            "3P%": (sh.get("fg3_pct") * 100) if sh.get("fg3_pct") is not None else None,
            "FT%": (sh.get("ft_pct") * 100) if sh.get("ft_pct") is not None else None,
            "eFG%": (sh.get("efg_pct") * 100) if sh.get("efg_pct") is not None else None,
            "BUCK": buck.get((p["athlete_id"], p["team"])),
        }
        vals = {k: (round(v, 3) if v is not None else None) for k, v in vals.items()}
        gp = p["totals"]["gp"]
        min_pct = rt.get("min_pct")
        out.append({
            "name": p["name"],
            "team": p["team"],
            "slug": player_slug(p["name"], p["athlete_id"], p["team"]),
            "gp": gp,
            "min_pct": round(min_pct, 1) if min_pct is not None else None,
            "q": bool(gp >= 2 and min_pct is not None and min_pct >= 40),
            "v": vals,
        })
    return out


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stat distributions | NBA Ratings</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; color: #1f2328; max-width: 920px; margin: 0 auto; padding: 16px; line-height: 1.5; }
a { color: #0969da; }
h1 { font-size: 28px; margin: 8px 0 4px; }
.note { color: #656d76; font-size: 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.chips button { padding: 6px 12px; border: 1px solid #d0d7de; border-radius: 999px; background: #fff; font-size: 14px; cursor: pointer; }
.chips button.on { background: #0969da; color: #fff; border-color: #0969da; }
.searchrow { display: flex; gap: 8px; margin: 12px 0; flex-wrap: wrap; }
.searchrow input { flex: 1; min-width: 220px; padding: 8px 10px; font-size: 15px; border: 1px solid #d0d7de; border-radius: 6px; }
.searchrow button { padding: 8px 14px; font-size: 14px; border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa; cursor: pointer; }
#suggest { border: 1px solid #d0d7de; border-radius: 6px; margin: -6px 0 12px; display: none; }
#suggest div { padding: 8px 10px; cursor: pointer; border-bottom: 1px solid #eaeef2; }
#suggest div:last-child { border-bottom: none; }
#suggest div:hover { background: #f6f8fa; }
#chart { width: 100%; height: auto; }
#chart rect.bar { fill: #38bdf8; }
#chart rect.bar.hot { fill: #1d4ed8; }
#chart path.kde { fill: none; stroke: #1d4ed8; stroke-width: 2.5; }
#chart line.mean { stroke: #656d76; stroke-dasharray: 5 4; stroke-width: 1.5; }
#chart line.marker { stroke: #f59e0b; stroke-width: 2.5; }
#chart text { font-size: 12px; fill: #656d76; }
#chart text.axislabel { font-size: 13px; fill: #1f2328; font-weight: 600; }
#binpanel { border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; margin: 12px 0; }
#binpanel h3 { margin: 0 0 8px; font-size: 16px; }
#binpanel ul { margin: 8px 0 0; padding-left: 20px; }
#binpanel li { margin: 2px 0; }
#markerinfo { font-weight: 600; margin: 8px 0; }
.summary { color: #656d76; font-size: 14px; }
.top5 li { margin: 2px 0; }
</style>
</head>
<body>
<p><a href="index.html">&larr; NBA Ratings</a> &middot; <a href="buck.html">BUCK</a> &middot; <a href="best-mvp.html">Best MVP</a> &middot; <a href="players/index.html">All players</a></p>
<h1>Stat distributions</h1>
<p class="note">Pick a stat and the chart builds its distribution from every player with a value for that stat. The bars are the histogram, the smooth blue line follows the shape of the data, and the grey dashed line is the league average. Hover or tap a bar to see the players in that range. Search a name to mark where that player sits.</p>
<div class="chips" id="chips"></div>
<p class="note" id="tip"></p>
<div class="searchrow">
<input id="search" type="text" placeholder="Search a player, for example Stephen Curry" autocomplete="off">
<button id="clearbtn" type="button">Clear marker</button>
</div>
<div id="suggest"></div>
<div id="markerinfo"></div>
<svg id="chart" viewBox="0 0 720 380" role="img" aria-label="Distribution chart"></svg>
<div id="binpanel"><h3>Players in this range</h3><p class="note">Hover or tap a bar to see the players in that range.</p></div>
<p class="summary" id="summary"></p>
<h2>Top 5</h2>
<ol class="top5" id="top5"></ol>
<p class="note">Data through November 19, 2026 (226 games). SHARP has 211 qualified players (5+ games, 20+ minutes per game); BUCK has 183 qualified players (min 2 games, 40% of team minutes) out of 406 with a matched salary; every other stat uses all 475 players with a value.</p>
<script>
const PLAYERS = __DATA_JSON__;
const STATS = __STATS_JSON__;
const TIPS = __TIPS_JSON__;
let curStat = "PPG";
let curBins = null;
let curVals = null;
let curLo = null, curHi = null;
let marker = null;
const W = 720, H = 380, ML = 52, MR = 16, MT = 28, MB = 48;
const PW = W - ML - MR, PH = H - MT - MB;
const svgNS = "http://www.w3.org/2000/svg";
function fmt(stat, v) {
  const s = STATS.find(x => x.key === stat);
  return s.pct ? v.toFixed(1) + "%" : v.toFixed(s.dec);
}
function el(tag, attrs) {
  const e = document.createElementNS(svgNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function quantile(sorted, q) {
  const i = (sorted.length - 1) * q;
  const lo = Math.floor(i), hi = Math.ceil(i);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}
function kde(values, x, h) {
  let s = 0;
  for (const v of values) {
    const z = (x - v) / h;
    s += Math.exp(-0.5 * z * z);
  }
  return s / (values.length * h * Math.sqrt(2 * Math.PI));
}
function build(stat) {
  curStat = stat;
  document.querySelectorAll("#chips button").forEach(b => b.classList.toggle("on", b.dataset.stat === stat));
  document.getElementById("tip").textContent = TIPS[stat] || "";
  const rows = PLAYERS.map(p => ({ p, v: p.v[stat] })).filter(r => r.v !== null && r.v !== undefined && (stat !== "BUCK" || r.p.q));
  rows.sort((a, b) => a.v - b.v);
  curVals = rows;
  const vals = rows.map(r => r.v);
  const n = vals.length;
  const mean = vals.reduce((a, b) => a + b, 0) / n;
  const median = quantile(vals, 0.5);
  const sd = Math.sqrt(vals.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n);
  const iqr = quantile(vals, 0.75) - quantile(vals, 0.25);
  // Chart range is capped at the 1st and 99th percentiles so a handful of
  // tiny-sample outliers do not squash the bulk of players into a spike.
  // Summary, top 5, and ranks still use all players.
  const pLo = quantile(vals, 0.01), pHi = quantile(vals, 0.99);
  const outside = vals.filter(v => v < pLo || v > pHi).length;
  const pad = (pHi - pLo) * 0.02 || 1;
  const lo = vals[0] >= 0 ? Math.max(0, pLo - pad) : pLo - pad;
  const hi = pHi + pad;
  curLo = lo; curHi = hi;
  const NB = 20, bw = (hi - lo) / NB;
  const bins = Array.from({ length: NB }, (_, i) => ({ lo: lo + i * bw, hi: lo + (i + 1) * bw, count: 0, rows: [] }));
  for (const r of rows) {
    let i = Math.floor((r.v - lo) / bw);
    if (i < 0) i = 0; if (i >= NB) i = NB - 1;
    bins[i].count++;
    bins[i].rows.push(r);
  }
  bins.forEach(b => b.rows.sort((a, b) => b.v - a.v));
  curBins = bins;
  let h = 0.9 * Math.min(sd, iqr / 1.34 || sd) * Math.pow(n, -0.2);
  if (!h || h <= 0) h = bw;
  const xs = [];
  for (let i = 0; i <= 120; i++) xs.push(lo + (hi - lo) * i / 120);
  const dens = xs.map(x => kde(vals, x, h));
  const kdeMax = Math.max(...dens) * n * bw;
  const countMax = Math.max(...bins.map(b => b.count));
  const yMax = Math.max(countMax, kdeMax) * 1.08;
  const X = v => ML + (v - lo) / (hi - lo) * PW;
  const Y = c => MT + PH - c / yMax * PH;
  const svg = document.getElementById("chart");
  svg.innerHTML = "";
  const title = el("text", { x: ML, y: 16, "class": "axislabel" });
  title.textContent = stat + " distribution, " + n + " players";
  svg.appendChild(title);
  bins.forEach((b, i) => {
    const r = el("rect", { "class": "bar", x: X(b.lo) + 1, y: Y(b.count), width: Math.max(0, X(b.hi) - X(b.lo) - 2), height: MT + PH - Y(b.count) });
    r.addEventListener("mouseover", () => showBin(i));
    r.addEventListener("click", () => showBin(i));
    svg.appendChild(r);
  });
  let d = "";
  xs.forEach((x, i) => { d += (i === 0 ? "M" : "L") + X(x).toFixed(1) + "," + Y(dens[i] * n * bw).toFixed(1) + " "; });
  svg.appendChild(el("path", { "class": "kde", d }));
  const meanLine = el("line", { "class": "mean", x1: X(mean), y1: MT, x2: X(mean), y2: MT + PH });
  svg.appendChild(meanLine);
  const meanLab = el("text", { x: Math.min(X(mean) + 4, W - 110), y: MT + 12 });
  meanLab.textContent = "avg " + fmt(stat, mean);
  svg.appendChild(meanLab);
  for (let t = 0; t <= 4; t++) {
    const v = lo + (hi - lo) * t / 4;
    const lab = el("text", { x: X(v), y: MT + PH + 18, "text-anchor": "middle" });
    lab.textContent = fmt(stat, v);
    svg.appendChild(lab);
  }
  for (let t = 0; t <= 2; t++) {
    const c = yMax / 1.08 * t / 2;
    const lab = el("text", { x: ML - 6, y: Y(c) + 4, "text-anchor": "end" });
    lab.textContent = Math.round(c);
    svg.appendChild(lab);
  }
  const xlab = el("text", { x: ML + PW / 2, y: H - 6, "text-anchor": "middle", "class": "axislabel" });
  xlab.textContent = stat;
  svg.appendChild(xlab);
  const ylab = el("text", { x: 14, y: MT + PH / 2, "text-anchor": "middle", transform: "rotate(-90 14 " + (MT + PH / 2) + ")" });
  ylab.textContent = "players";
  svg.appendChild(ylab);
  document.getElementById("summary").textContent =
    "Players: " + n + "  \u00b7  Average: " + fmt(stat, mean) + "  \u00b7  Median: " + fmt(stat, median) +
    "  \u00b7  Lowest: " + fmt(stat, vals[0]) + "  \u00b7  Highest: " + fmt(stat, vals[n - 1]);
  if (outside) document.getElementById("summary").textContent +=
    "  \u00b7  Chart shows the 1st to 99th percentile (" + fmt(stat, pLo) + " to " + fmt(stat, pHi) + "); " + outside + " players outside that range are grouped in the end bins.";
  const top = [...rows].sort((a, b) => b.v - a.v).slice(0, 5);
  document.getElementById("top5").innerHTML = top.map(r =>
    '<li><a href="players/' + r.p.slug + '.html">' + r.p.name + '</a> (' + r.p.team + ') ' + fmt(stat, r.v) + '</li>').join("");
  document.getElementById("binpanel").innerHTML = '<h3>Players in this range</h3><p class="note">Hover or tap a bar to see the players in that range.</p>';
  if (marker) drawMarker();
}
function showBin(i) {
  document.querySelectorAll("#chart rect.bar").forEach((r, j) => r.classList.toggle("hot", j === i));
  const b = curBins[i];
  const rows = b.rows;
  let html = "<h3>" + fmt(curStat, b.lo) + " to " + fmt(curStat, b.hi) + " (" + rows.length + " player" + (rows.length === 1 ? "" : "s") + ")</h3><ul>";
  for (const r of rows) {
    const extra = curStat === "BUCK" ? ', ' + r.p.gp + ' GP, ' + r.p.min_pct + '% min' : '';
    html += '<li><a href="players/' + r.p.slug + '.html">' + r.p.name + '</a> (' + r.p.team + ') ' + fmt(curStat, r.v) + extra + '</li>';
  }
  html += "</ul>";
  document.getElementById("binpanel").innerHTML = html;
}
function drawMarker() {
  const svg = document.getElementById("chart");
  svg.querySelectorAll(".marker, .markerdot, .markerlab").forEach(e => e.remove());
  const pl = PLAYERS.find(p => p.slug === marker);
  const name = pl ? pl.name : marker;
  const row = curVals.find(r => r.p.slug === marker);
  const info = document.getElementById("markerinfo");
  if (!row) {
    info.textContent = name + " has no value for " + curStat + ".";
    return;
  }
  const rank = [...curVals].sort((a, b) => b.v - a.v).findIndex(r => r.p.slug === marker) + 1;
  info.textContent = name + ": " + fmt(curStat, row.v) + ", #" + rank + " of " + curVals.length + " in the NBA";
  // Marker uses the same capped chart range as build(), not the
  // full min/max range, or it lands left of where the chart puts
  // the same value, worst for BUCK where p99 is well below max.
  const lo = curLo, hi = curHi;
  const X = v => ML + (v - lo) / (hi - lo) * PW;
  const kx = Math.min(Math.max(X(row.v), ML), ML + PW);
  const line = el("line", { "class": "marker", x1: kx, y1: MT, x2: kx, y2: MT + PH });
  svg.appendChild(line);
  const dot = el("circle", { "class": "markerdot", cx: kx, cy: MT + 8, r: 5, fill: "#f59e0b" });
  svg.appendChild(dot);
  const nearRight = kx > ML + PW - 220;
  const lab = el("text", { "class": "markerlab", x: nearRight ? kx - 8 : kx + 8, y: MT + 26, fill: "#92400e", "text-anchor": nearRight ? "end" : "start" });
  lab.textContent = name;
  svg.appendChild(lab);
}
const chips = document.getElementById("chips");
STATS.forEach(s => {
  const b = document.createElement("button");
  b.textContent = s.label;
  b.dataset.stat = s.key;
  b.addEventListener("click", () => build(s.key));
  chips.appendChild(b);
});
const search = document.getElementById("search");
const suggest = document.getElementById("suggest");
search.addEventListener("input", () => {
  const q = search.value.trim().toLowerCase();
  if (q.length < 2) { suggest.style.display = "none"; return; }
  const hits = PLAYERS.filter(p => p.name.toLowerCase().includes(q)).slice(0, 8);
  if (!hits.length) { suggest.style.display = "none"; return; }
  suggest.innerHTML = "";
  hits.forEach(p => {
    const d = document.createElement("div");
    d.textContent = p.name + " (" + p.team + ")";
    d.addEventListener("click", () => {
      search.value = p.name;
      suggest.style.display = "none";
      marker = p.slug;
      drawMarker();
    });
    suggest.appendChild(d);
  });
  suggest.style.display = "block";
});
search.addEventListener("keydown", e => {
  if (e.key === "Enter") {
    const first = suggest.querySelector("div");
    if (first) first.click();
  }
});
document.getElementById("clearbtn").addEventListener("click", () => {
  marker = null;
  search.value = "";
  document.getElementById("markerinfo").textContent = "";
  build(curStat);
});
build("PPG");
</script>
</body>
</html>
"""


def main():
    players = build_players()
    stats_json = json.dumps([
        {"key": k, "label": lab, "dec": dec, "pct": pct}
        for k, lab, dec, pct in STATS
    ])
    html = (HTML
            .replace("__DATA_JSON__", json.dumps(players, separators=(",", ":")))
            .replace("__STATS_JSON__", stats_json)
            .replace("__TIPS_JSON__", json.dumps(TIPS)))
    (ROOT / "distributions.html").write_text(html)
    counts = {}
    for k, _, _, _ in STATS:
        counts[k] = sum(1 for p in players if p["v"][k] is not None)
    print(f"Wrote distributions.html, {len(html)} bytes, {len(players)} players")
    print("counts:", counts)


if __name__ == "__main__":
    main()
