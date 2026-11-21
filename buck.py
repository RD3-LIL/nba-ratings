#!/usr/bin/env python3
"""BUCK: best bang for your buck, v1, fixed after fresh-eyes review.

Output over input, in the site's own terms:

    BUCK = RPR v1.2 / average salary over the remaining contract ($M)

RPR v1.2 is the output: a per-game value above an average player,
already scaled by minutes, so 0 is average, +3 to +7 is a star,
and a negative number is below average. The input is not just
this season's salary. It is the average yearly salary over the
years left on the deal, including this season:

    avg_salary = (salary 2026-27 + future listed salaries)
                 / (1 + years_left)
    years_left = seasons after 2026-27 with a listed salary

That is how years left enter the formula, with no invented
weight. A cheap year before a big extension is not a cheap
player: Wembanyama is $16.87M this season but $44.86M a year
on average over the six years listed, so his BUCK falls from
0.41 this season to 0.16 on the contract. A flat cheap deal
stays cheap: Ajay Mitchell is $2.85M this season and $2.85M
a year on average, with one year left after this one.

BUCK_NOW = RPR / salary 2026-27 ($M) is kept alongside, so a
reader can see the difference between this season's bargain
and the contract bargain. BUCK_TOTAL = RPR_value / avg salary
($M), where RPR_value = RPR x GP / 82, is a total-value
companion: same per-game BUCK, but weighted by games played.

Why no shift on RPR: RPR is centered on average, so adding
a constant (say +3.5 to make every value positive) would
reward cheap mediocrity. Tested on this data, (RPR + 3.5) /
this-season salary puts Meleek Thomas, RPR -0.14 on $1.36M,
at the top of the qualified list, ahead of every
above-average player. With the average-salary denominator,
the same +3.5 puts Meleek 5th, behind Cam Spencer, Moussa
Diabate, Matisse Thybulle and Mario Hezonja, so the
conclusion, do not shift, still holds, but the evidence
depends on the denominator, and it is named here. With
the raw RPR as the numerator, a below-average player
gets a negative BUCK however cheap he is, which is the
honest reading: he is below average, the cheap price does
not make him good value, it makes him cheap.

Why no control multiplier on top of the average: years left
already change the input through the average, and a further
multiplier (say 5% or 10% a year) would be an invented
weight with no calibration behind it. Sorting ties are
broken by years left, then RPR, so at the same BUCK a
longer cheap deal ranks ahead of an expiring one, without
pretending the bonus is measured. That tie-break is
practically vacuous, because ranking is on full-precision
BUCK and ties are rare: the average does not reward
control, it only punishes pay rises, and the page says
so instead of claiming otherwise.

Contract transparency: future years are taken as listed,
even if one is an option that will be declined or
exercised, and the guaranteed total is shown next to the
total remaining, so a reader can see the gap. On this run
106 players have total remaining more than $10M above
guaranteed, including Wembanyama ($269.17M total,
$211.75M guaranteed, $57.42M gap, exactly his 2031-32
salary) and Jokic ($62.84M gap) and Lillard ($104.17M
gap). A cliff flag marks a player whose 2027-28 salary
is more than twice his 2026-27 salary.

Floors and coverage: the Top 25 tables use the same floor
as the leaderboards, min 2 games and 40% of team minutes,
so a five-game cameo cannot top them. The distribution
uses qualified players only (183 on the Nov 21 run), for
the same reason. Players with no matched salary are
excluded, never guessed, and the qualified ones among
them are named on the page: Quenton Jackson, RPR +0.69,
46% of team minutes, 15 games, would rank about 2nd or
3rd on a minimum salary.

Ranking is on full-precision BUCK, BUCK_NOW and
BUCK_TOTAL, display is rounded to 3 decimals. Ranks are
competition ranks (1, 2, 2, 4).

Known gaps, same as Salaries in the README: the
Basketball-Reference contracts table lists yearly amounts
and a guaranteed total, but not options, guarantees per
year, trade kickers, or no-trade clauses. Future years are
taken as listed, even if one is an option that will be
declined or exercised, so the average can move when the
contract structure changes. Salaries are refreshed weekly
at most, like rosters.

Reads data/players_2026_27_eval.json and
data/salaries_2026_27.json. Writes data/buck_2026_27.json
and .csv, plus buck.html at the repo root. Stdlib only.
No network. Validation: exits 1 if matched salaries fall
below 380, or if the known BUCK checks for Curry,
Wembanyama and Ajay Mitchell fail.

Usage:
    python3 buck.py
"""
import csv
import html
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"
REPO = "https://github.com/RD3-LIL/nba-ratings"
MIN_MATCHED = 380
KNOWN_BUCK = {
    "Stephen Curry": 0.053,
    "Victor Wembanyama": 0.156,
    "Ajay Mitchell": 0.192,
}


def player_slug(name, athlete_id, team):
    import re
    import unicodedata
    norm = unicodedata.normalize("NFKD", name)
    ascii_name = norm.encode("ascii", "ignore").decode("ascii")
    s = ascii_name.lower().replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    team_s = re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")
    return f"{s}-{athlete_id}-{team_s}"


def data_through():
    games = json.loads((DATA / "games_2026_27.json").read_text())
    played = [g for g in games
              if not (g["home_score"] == 0 and g["away_score"] == 0)
              and not g.get("stats_missing")]
    last = max(g["date"] for g in played) if played else None
    disp = datetime.strptime(last, "%Y%m%d").strftime("%B %-d, %Y") \
        if last else "unknown"
    return disp, len(played)


def _money_m(value):
    if value is None:
        return "-"
    return f"${value / 1e6:.2f}M"


def _rank(rows, key_full, rank_key):
    ordered = sorted(rows, key=lambda r: (-r[key_full], -r["years_left"],
                                          -r["rpr_full"]))
    prev_val, prev_rank = None, 0
    for i, r in enumerate(ordered):
        rank = prev_rank if r[key_full] == prev_val else i + 1
        r[rank_key] = rank
        prev_val, prev_rank = r[key_full], rank


def build_rows():
    eval_doc = json.loads((DATA / "players_2026_27_eval.json").read_text())
    sal_doc = json.loads((DATA / "salaries_2026_27.json").read_text())
    sal_by_id = {(p["athlete_id"], p["team"]): p
                 for p in sal_doc["players"]}
    rows = []
    missing = []
    for p in eval_doc["players"]:
        rpr = (p.get("ratings") or {}).get("rpr")
        if rpr is None:
            continue
        gp = p["totals"]["gp"]
        min_pct = (p.get("ratings") or {}).get("min_pct")
        qualified = bool(gp >= 2 and min_pct is not None and min_pct >= 40)
        s = sal_by_id.get((p["athlete_id"], p["team"]))
        if s is None or s.get("salary_2026_27") is None:
            if qualified:
                missing.append({
                    "athlete_id": p["athlete_id"],
                    "name": p["name"], "team": p["team"],
                    "gp": gp,
                    "min_pct": round(min_pct, 1) if min_pct is not None else None,
                    "rpr": round(rpr, 2),
                })
            continue
        salaries = s.get("salaries") or {}
        future = [v for k, v in sorted(salaries.items())
                  if k > "2026-27" and v is not None]
        years_left = len(future)
        total = s["salary_2026_27"] + sum(future)
        avg = total / (1 + years_left)
        sal_m = s["salary_2026_27"] / 1e6
        avg_m = avg / 1e6
        next_sal = salaries.get("2027-28")
        guaranteed = s.get("guaranteed")
        gap = (total - guaranteed) if guaranteed is not None else None
        cliff = bool(next_sal is not None
                     and next_sal > 2 * s["salary_2026_27"])
        rpr_value = (p.get("ratings") or {}).get("rpr_value")
        if rpr_value is None:
            rpr_value = rpr * gp / 82.0
        buck_full = rpr / avg_m
        buck_now_full = rpr / sal_m
        buck_total_full = rpr_value / avg_m
        rows.append({
            "athlete_id": p["athlete_id"],
            "name": p["name"],
            "team": p["team"],
            "slug": player_slug(p["name"], p["athlete_id"], p["team"]),
            "gp": gp,
            "mpg": round(p["per_game"]["min"], 1),
            "min_pct": (round(min_pct, 1) if min_pct is not None else None),
            "rpr": round(rpr, 2),
            "rpr_full": rpr,
            "rpr_value": round(rpr_value, 3),
            "salary_2026_27": s["salary_2026_27"],
            "salary_2027_28": next_sal,
            "salary_m": round(sal_m, 2),
            "years_left": years_left,
            "total_remaining": total,
            "avg_salary": int(round(avg)),
            "avg_salary_m": round(avg_m, 2),
            "guaranteed": guaranteed,
            "guaranteed_gap": gap,
            "cliff": cliff,
            "buck": round(buck_full, 3),
            "buck_full": buck_full,
            "buck_now": round(buck_now_full, 3),
            "buck_now_full": buck_now_full,
            "buck_total": round(buck_total_full, 3),
            "buck_total_full": buck_total_full,
            "qualified": qualified,
            "salaries": {k: v for k, v in sorted(salaries.items())
                         if v is not None},
        })
    # Ranks on full precision, among all players and qualified only.
    for pool, key_full, rank_key in (
            (rows, "buck_full", "buck_rank"),
            ([r for r in rows if r["qualified"]], "buck_full", "buck_rank_qualified"),
            ([r for r in rows if r["qualified"]], "buck_now_full", "buck_now_rank_qualified"),
            ([r for r in rows if r["qualified"]], "buck_total_full", "buck_total_rank_qualified")):
        _rank(pool, key_full, rank_key)
    for r in rows:
        for k in ("buck_rank_qualified", "buck_now_rank_qualified",
                  "buck_total_rank_qualified"):
            r.setdefault(k, None)
    missing.sort(key=lambda m: -m["rpr"])
    return rows, sal_doc, missing


def _cell(value, display):
    if value is None:
        return '<td data-v="">-</td>'
    if isinstance(value, (int, float)):
        return f'<td data-v="{value}">{display}</td>'
    return f'<td data-v="{html.escape(str(value).lower())}">{display}</td>'


def _row(r, rank_key):
    rank = r.get(rank_key)
    cells = [
        _cell(rank, str(rank) if rank is not None else "-"),
        f'<td data-v="{html.escape(r["name"].lower())}"><a href="players/{r["slug"]}.html">{html.escape(r["name"])}</a></td>',
        _cell(r["team"], html.escape(r["team"])),
        _cell(r["gp"], str(r["gp"])),
        _cell(r["rpr_full"], f"{r['rpr']:+.2f}"),
        _cell(r["salary_2026_27"], _money_m(r["salary_2026_27"])),
        _cell(r["salary_2027_28"], _money_m(r["salary_2027_28"]) + (" *" if r["cliff"] else "")),
        _cell(r["avg_salary"], _money_m(r["avg_salary"])),
        _cell(r["total_remaining"], _money_m(r["total_remaining"])),
        _cell(r["guaranteed"], _money_m(r["guaranteed"])),
        _cell(r["years_left"], str(r["years_left"])),
        _cell(r["buck_full"], f"<strong>{r['buck']:+.3f}</strong>"),
        _cell(r["buck_now_full"], f"{r['buck_now']:+.3f}"),
        _cell(r["buck_total_full"], f"{r['buck_total']:+.3f}"),
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _table(rows, title, rank_key, table_id=None):
    id_attr = f' id="{table_id}"' if table_id else ""
    out = [f"<h2>{html.escape(title)}</h2>",
           f'<div class="table-scroll"><table class="sortable"{id_attr}><thead><tr>'
           "<th data-t=\"num\">Rk</th><th data-t=\"text\">Player</th>"
           "<th data-t=\"text\">Team</th><th data-t=\"num\">GP</th>"
           "<th data-t=\"num\">RPR</th><th data-t=\"num\">Salary 26-27</th>"
           "<th data-t=\"num\">Next 27-28</th><th data-t=\"num\">Avg salary</th>"
           "<th data-t=\"num\">Total left</th><th data-t=\"num\">Guaranteed</th>"
           "<th data-t=\"num\">Yrs left</th><th data-t=\"num\">BUCK</th>"
           "<th data-t=\"num\">BUCK now</th><th data-t=\"num\">BUCK total</th>"
           "</tr></thead><tbody>"]
    out.extend(_row(r, rank_key) for r in rows)
    out.append("</tbody></table></div>")
    return "\n".join(out)


def build_page(rows, through, n_games, sal_doc, missing):
    qualified = [r for r in rows if r["qualified"]]
    qualified.sort(key=lambda r: (-r["buck_full"], -r["years_left"], -r["rpr_full"]))
    top25 = qualified[:25]
    top25_now = sorted(qualified, key=lambda r: (-r["buck_now_full"], -r["years_left"], -r["rpr_full"]))[:25]
    top10_total = sorted(qualified, key=lambda r: (-r["buck_total_full"], -r["years_left"], -r["rpr_full"]))[:10]
    worst10 = qualified[-10:][::-1]
    best = top25[0] if top25 else None
    ajay = next((r for r in qualified if r["name"] == "Ajay Mitchell"), None)
    wemby = next((r for r in rows if r["name"] == "Victor Wembanyama"), None)
    queta = next((r for r in rows if r["name"] == "Neemias Queta"), None)
    gap_count = sum(1 for r in rows
                    if r["guaranteed_gap"] is not None and r["guaranteed_gap"] > 10_000_000)
    examples = []
    if wemby:
        examples.append(
            f"Victor Wembanyama is ${wemby['salary_m']:.2f}M this season "
            f"(BUCK now {wemby['buck_now']:+.3f}, #{wemby['buck_rank']} "
            f"on this season alone) but ${wemby['avg_salary_m']:.2f}M a "
            f"year on average over the {1 + wemby['years_left']} years "
            f"listed, so his BUCK is {wemby['buck']:+.3f}. His total "
            f"remaining is {_money_m(wemby['total_remaining'])} against "
            f"{_money_m(wemby['guaranteed'])} guaranteed, a "
            f"{_money_m(wemby['guaranteed_gap'])} gap, exactly his "
            f"2031-32 salary, which is included in the average as "
            f"listed and may never be paid as listed.")
    if queta:
        examples.append(
            f"Neemias Queta is ${queta['salary_m']:.2f}M this season "
            f"(BUCK now {queta['buck_now']:+.3f}) but "
            f"${queta['avg_salary_m']:.2f}M a year on average, so his "
            f"BUCK is {queta['buck']:+.3f}. The cheap year is ending.")
    if ajay:
        examples.append(
            f"Ajay Mitchell is ${ajay['salary_m']:.2f}M this season and "
            f"${ajay['avg_salary_m']:.2f}M a year on average, with "
            f"{ajay['years_left']} year left after this one, RPR "
            f"{ajay['rpr']:+.2f} in {ajay['gp']} games at "
            f"{ajay['mpg']} minutes a game, so his BUCK is "
            f"{ajay['buck']:+.3f}, #{ajay['buck_rank_qualified']} "
            f"among qualified players.")
    missing_html = ""
    if missing:
        items = "; ".join(
            f"{html.escape(m['name'])} ({html.escape(m['team'])}, RPR "
            f"{m['rpr']:+.2f}, {m['gp']} games, {m['min_pct']}% of team "
            f"minutes)" for m in missing)
        missing_html = (
            "<h2>No salary, would otherwise qualify</h2>"
            "<p>These players meet the Top 25 floor but have no "
            "matched salary, so they get no BUCK, never a guessed "
            "one. On a minimum salary Quenton Jackson would rank "
            "about 2nd or 3rd, so the Top 25 is not complete at "
            "the cheap end.</p><p>" + items + "</p>")
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BUCK | NBA Ratings</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; color: #1f2328; max-width: 960px; margin: 0 auto; padding: 16px; line-height: 1.5; }}
a {{ color: #0969da; }}
h1 {{ font-size: 28px; margin: 8px 0 4px; }}
h2 {{ border-bottom: 1px solid #d0d7de; padding-bottom: 6px; margin-top: 36px; }}
.note {{ color: #656d76; font-size: 14px; }}
.warn {{ background: #fff8c5; border: 1px solid #d4a72c; border-radius: 6px; padding: 10px 12px; }}
.table-scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ border-bottom: 1px solid #eaeef2; padding: 6px 8px; text-align: left; white-space: nowrap; }}
th {{ cursor: pointer; }}
td:nth-child(n+4) {{ text-align: right; font-variant-numeric: tabular-nums; }}
.filter {{ width: 280px; padding: 8px 10px; font-size: 15px; border: 1px solid #d0d7de; border-radius: 6px; }}
</style>
</head>
<body>
<p><a href="index.html">&larr; NBA Ratings</a> &middot; <a href="distributions.html">Distributions</a> &middot; <a href="players/index.html">All players</a></p>
<h1>BUCK: best bang for your buck</h1>
<p class="note">Data through {through} ({n_games} games). {len(rows)} players with a salary and an RPR, {sal_doc.get("matched")} matched of {sal_doc.get("player_count")} in the box scores, {len(qualified)} qualified for the Top 25 below.</p>
<p><strong>BUCK = RPR &divide; average salary over the remaining contract, in $M.</strong> RPR v1.2 is the output, a per-game value above an average player, already scaled by minutes, so 0 is average and +3 to +7 is a star. The input is the average yearly salary over the years left on the deal, including this season, not just this season's salary. BUCK now, shown for comparison, is RPR &divide; this season's salary only. BUCK total is RPR_value &divide; average salary, where RPR_value is RPR times GP / 82, so it rewards games played.</p>
<p>That is how years left enter the formula, with no invented weight. A cheap year before a big extension is not a cheap player, and a flat cheap deal stays cheap. {' '.join(examples)}</p>
<p class="warn"><strong>Read it as value, not as a pick.</strong> A negative BUCK is a below-average player, however cheap he is: cheap does not make a below-average player good value. The top of this list is role players on small deals, not stars, because salary varies about 40x across the league while RPR varies about 10 points. A star can be worth every dollar at a low BUCK and still be the player you want. Early-season RPR is noisy, and the future salaries are taken as listed in the Basketball-Reference contracts table, which does not label options, so the average can move when a contract option is declined or exercised. Some salaries look surprisingly low, and they are as listed, not a parse error: LeBron James is $3,876,529 for Philadelphia in 2026-27, and 32 players are listed at exactly $2,449,421. Both were checked against the cached Basketball-Reference page on Nov 21, 2026, so BUCK uses them as listed. {gap_count} players have total remaining more than $10M above guaranteed. A * next to a 2027-28 salary means that salary is more than twice this season's salary, a cliff. {len(missing)} qualified players have no matched salary and are listed below, never guessed.</p>
<p>Top 25 uses the same floor as the leaderboards: min 2 games and 40% of team minutes. Click a column header to sort any table on this page, and use the filter box to search the full qualified table by player or team name. Full data in <a href="data/buck_2026_27.csv">buck_2026_27.csv</a>. Ranks are on full-precision BUCK, display is rounded to 3 decimals.</p>
{f"<p><strong>Best BUCK among qualified players: {html.escape(best['name'])} ({html.escape(best['team'])}, RPR {best['rpr']:+.2f}, ${best['salary_m']:.2f}M this season, ${best['avg_salary_m']:.2f}M a year on average, {best['years_left']} years left, BUCK {best['buck']:+.3f}).</strong></p>" if best else ""}
{_table(top25, "Top 25 by BUCK", "buck_rank_qualified")}
{_table(top25_now, "Top 25 by BUCK now (this season only)", "buck_now_rank_qualified")}
<p class="note">BUCK now is the trade-today view: this season's salary drives cap matching. Wemby is 3rd here and 14th on BUCK, Queta 4th here and 22nd on BUCK, Ajay 9th on both.</p>
{_table(top10_total, "Top 10 by BUCK total (games played)", "buck_total_rank_qualified")}
<p class="note">BUCK total is RPR_value &divide; average salary, RPR_value is RPR times GP / 82. It rewards a player who has actually played the games, without replacing the per-game BUCK.</p>
<p class="note">Below-average production on a real salary. These are the worst value deals on this season's numbers, not necessarily the worst players.</p>
{_table(qualified[-10:][::-1], "Lowest 10 by BUCK", "buck_rank_qualified")}
{missing_html}
<input class="filter" type="search" placeholder="Filter players..." id="q">
{_table(qualified, "All qualified players by BUCK", "buck_rank_qualified", table_id="all")}
<p class="note">Ranks are competition ranks (1, 2, 2, 4) among qualified players. Players with no matched salary are excluded, never guessed. A player traded mid-season has one BUCK row per team stint, on the same contract, so Nique Clifford appears twice. Salaries: Basketball-Reference contracts page, fetched {sal_doc.get("fetch_date")}. RPR: v1.2, see the <a href="rpr-research.html">RPR research</a> page. The average salary does not reward years of control, it only punishes pay rises: at the same average price, a locked-in deal and an expiring deal get the same BUCK, and the tie-break by years left almost never fires. That is a limit of this version, not a feature.</p>
<footer><p>Data: ESPN public scoreboard and box scores, Basketball-Reference contracts. Code and full CSVs: <a href="{REPO}">RD3-LIL/nba-ratings on GitHub</a>.</p></footer>
<script>
document.getElementById("q").addEventListener("input", function (e) {{
  var q = e.target.value.trim().toLowerCase();
  document.querySelectorAll("#all tbody tr").forEach(function (r) {{
    r.style.display = (!q || r.textContent.toLowerCase().indexOf(q) !== -1) ? "" : "none";
  }});
}});
document.querySelectorAll("table.sortable").forEach(function (tbl) {{
  var ths = tbl.querySelectorAll("th");
  ths.forEach(function (th, idx) {{
    th.addEventListener("click", function () {{
      var tbody = tbl.querySelector("tbody");
      var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
      var asc = th.dataset.asc !== "1";
      rows.sort(function (a, b) {{
        var av = a.children[idx].dataset.v, bv = b.children[idx].dataset.v;
        var an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
        return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
      }});
      rows.forEach(function (r) {{ tbody.appendChild(r); }});
      ths.forEach(function (t) {{ delete t.dataset.asc; }});
      th.dataset.asc = asc ? "1" : "0";
    }});
  }});
}});
</script>
</body>
</html>
"""
    return body


def validate(rows, sal_doc, through):
    matched = sal_doc.get("matched")
    if matched is None or matched < MIN_MATCHED:
        raise SystemExit(f"BUCK validation failed: matched salaries {matched}, need at least {MIN_MATCHED}")
    by_name = {r["name"]: r for r in rows}
    for name, expected in KNOWN_BUCK.items():
        r = by_name.get(name)
        if r is None or abs(r["buck"] - expected) > 0.002:
            got = r["buck"] if r else None
            raise SystemExit(f"BUCK validation failed: {name} BUCK expected {expected}, got {got}")
    print(f"BUCK validation passed: matched {matched}, Curry {KNOWN_BUCK['Stephen Curry']}, Wembanyama {KNOWN_BUCK['Victor Wembanyama']}, Ajay Mitchell {KNOWN_BUCK['Ajay Mitchell']}, data through {through}")


def main():
    rows, sal_doc, missing = build_rows()
    through, n_games = data_through()
    validate(rows, sal_doc, through)
    qualified = [r for r in rows if r["qualified"]]
    payload = {
        "season": SEASON,
        "source": "basketball-reference.com/contracts/players.html",
        "fetch_date": sal_doc.get("fetch_date"),
        "data_through": through,
        "games_included": n_games,
        "player_count": len(rows),
        "qualified_count": len(qualified),
        "matched": sal_doc.get("matched"),
        "missing_qualified": missing,
        "method": (
            "BUCK = RPR v1.2 / average salary over the remaining "
            "contract ($M), including this season. years_left is the "
            "count of future seasons with a listed salary. "
            "BUCK_NOW = RPR / salary 2026-27 ($M). "
            "BUCK_TOTAL = RPR_value / average salary ($M), "
            "RPR_value = RPR x GP / 82. No shift on RPR: "
            "0 is average, negative is below average. No invented "
            "control multiplier: years left enter only through the "
            "average, ties broken by years left then RPR, and the "
            "average does not reward control, it only punishes "
            "pay rises. Ranking is on full-precision BUCK, display "
            "rounded to 3 decimals. Future salaries are taken as "
            "listed; options are not labelled in the source. "
            "Unmatched players are excluded, never guessed."
        ),
        "players": rows,
    }
    (DATA / "buck_2026_27.json").write_text(json.dumps(payload, indent=2))
    fields = ["athlete_id", "name", "team", "gp", "mpg", "min_pct",
              "rpr", "rpr_value", "salary_2026_27", "salary_2027_28",
              "salary_m", "years_left", "total_remaining", "avg_salary",
              "avg_salary_m", "guaranteed", "guaranteed_gap", "cliff",
              "buck", "buck_now", "buck_total", "buck_rank",
              "buck_rank_qualified", "buck_now_rank_qualified",
              "buck_total_rank_qualified", "qualified"]
    with open(DATA / "buck_2026_27.csv", "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for r in rows:
            out = {k: ("" if r.get(k) is None else r.get(k)) for k in fields}
            out["cliff"] = 1 if r.get("cliff") else 0
            w.writerow(out)
    page = build_page(rows, through, n_games, sal_doc, missing)
    (HERE / "buck.html").write_text(page)
    print(f"Wrote data/buck_2026_27.json + .csv and buck.html: "
          f"{len(rows)} players, {len(qualified)} qualified, "
          f"{len(missing)} missing qualified")
    print("Top 10 qualified by BUCK:")
    for r in sorted(qualified, key=lambda x: (-x["buck_full"], -x["years_left"],
                                              -x["rpr_full"]))[:10]:
        print(f"  {r['buck_rank_qualified']}. {r['name']} ({r['team']}) "
              f"RPR {r['rpr']:+.2f} ${r['salary_m']:.2f}M now, "
              f"${r['avg_salary_m']:.2f}M avg, {r['years_left']} yrs left, "
              f"BUCK {r['buck']:+.3f} (now {r['buck_now']:+.3f}, total {r['buck_total']:+.3f})")
    ajay = next((r for r in qualified if r["name"] == "Ajay Mitchell"), None)
    if ajay:
        print(f"Ajay Mitchell: BUCK {ajay['buck']:+.3f}, "
              f"#{ajay['buck_rank_qualified']} qualified, "
              f"#{ajay['buck_rank']} of all {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
