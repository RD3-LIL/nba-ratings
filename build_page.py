"""Build the NBA Ratings GitHub Pages site (index.html + teams/*.html).

Adapted from RonPom's build_page.py (~/workspace/kenpom-clone/build_page.py),
single season only (2026-27), 30 NBA teams, no multi-season history.

Reads data/teams_2026_27.json, data/games_2026_27.json,
data/players_2026_27.csv, data/gm_board_2026_27.csv, and writes a
static index.html at the repo root plus one page per team under
teams/. No network, no build step, stdlib only. Re-run after
regenerating the ratings files.

Format, mirroring RonPom: a dense ratings table is the centerpiece,
rank / team / conf / W-L / AdjO / AdjD / Net / AdjT, with the small
metric rank under each AdjO / AdjD / AdjT value (1 is best; AdjD
rank 1 is the best defense), click-to-sort columns, a team filter
box, and East/West conference tabs. Clicking a team row expands
that team's game log and roster for the season; clicking a team
name opens that team's own page (teams/<slug>.html) with its
ratings, game log, and roster. A player leaders section lists the
top 5 by PTS, REB, AST, ORtg, and RPR (min 2 games, 40% of team
minutes), plus a sortable top-100 players table by RPR. A GM board
section lists the acquisition tiers.

Where the columns come from:
- Conf: East/West from the static CONFERENCE map below (standard
  NBA alignment, 15 teams each). ESPN's NBA teams endpoint carries
  no conference field, so this is a fixed mapping, not fetched.
- W-L: the record in the games these ratings are built from
  (played, boxscore present). W+L equals GP for every team.
- AdjO/AdjD/Net/AdjT: team_ratings.py solver output in
  data/teams_2026_27.json (damped Gauss-Seidel, league-average
  prior, home court off). No last-season prior: Ron decided NBA
  teams change too much year over year for one to help.
- Metric ranks: competition ranks (1, 2, 2, 4) per metric,
  computed here from the solver output, same as RonPom.

Usage:
    python3 build_page.py
"""

import csv
import html
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"
REPO = "https://github.com/RD3-LIL/nba-ratings"

# Standard NBA alignment. ESPN's NBA teams endpoint has no
# conference field, so this is a fixed map, checked at build
# time: every rated team must appear exactly once.
EAST = {
    "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets",
    "Charlotte Hornets", "Chicago Bulls", "Cleveland Cavaliers",
    "Detroit Pistons", "Indiana Pacers", "Miami Heat",
    "Milwaukee Bucks", "New York Knicks", "Orlando Magic",
    "Philadelphia 76ers", "Toronto Raptors", "Washington Wizards",
}
WEST = {
    "Dallas Mavericks", "Denver Nuggets", "Golden State Warriors",
    "Houston Rockets", "LA Clippers", "Los Angeles Lakers",
    "Memphis Grizzlies", "Minnesota Timberwolves",
    "New Orleans Pelicans", "Oklahoma City Thunder", "Phoenix Suns",
    "Portland Trail Blazers", "Sacramento Kings",
    "San Antonio Spurs", "Utah Jazz",
}
CONFERENCE = {t: "East" for t in EAST}
CONFERENCE.update({t: "West" for t in WEST})


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def team_slug(name):
    """Team name -> stable URL slug for teams/<slug>.html.

    Lowercase, accents stripped, apostrophes dropped, every other
    non-alphanumeric run becomes one hyphen. Fails loudly on a
    collision rather than letting one team's page overwrite
    another's.
    """
    norm = unicodedata.normalize("NFKD", name)
    ascii_name = norm.encode("ascii", "ignore").decode("ascii")
    s = ascii_name.lower().replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _rank_map(rows, key, reverse):
    """Competition ranks (1, 2, 2, 4) for one metric column.
    reverse=True means rank 1 is the highest value (AdjO, AdjT);
    reverse=False means rank 1 is the lowest (AdjD, best defense).
    Rows with a missing value get no rank."""
    vals = [(r["team"], float(r[key])) for r in rows
            if r.get(key) not in (None, "")]
    vals.sort(key=lambda kv: kv[1], reverse=reverse)
    out, prev_val, prev_rank = {}, None, 0
    for i, (team, v) in enumerate(vals):
        rank = prev_rank if v == prev_val else i + 1
        out[team] = rank
        prev_val, prev_rank = v, rank
    return out


def metric_ranks(rows):
    """Per-metric ranks, keyed by metric then team."""
    return {
        "adj_o": _rank_map(rows, "adj_o", True),
        "adj_d": _rank_map(rows, "adj_d", False),
        "adj_t": _rank_map(rows, "adj_t", True),
    }


def _metric_cell(data_v, disp, rank):
    """A metric table cell: value on top, small rank underneath."""
    if rank is None:
        return f"<td data-v=\"{data_v}\">{disp}</td>"
    return (f"<td data-v=\"{data_v}\" data-r=\"{rank}\">{disp}"
            f"<span class=\"sub-rank\">{rank}</span></td>")


def load_teams():
    """Team rows sorted by Net, from the solver JSON.

    Returns (rows, league_o, league_t, info) where each row is a
    dict with rank, team, conf, gp, adj_o, adj_d, net, adj_t,
    w, l.
    """
    doc = json.loads((DATA / "teams_2026_27.json").read_text())
    teams = doc["teams"]
    missing = set(teams) - set(CONFERENCE)
    if missing:
        raise SystemExit(f"teams with no conference mapping: {sorted(missing)}")
    games = json.loads((DATA / "games_2026_27.json").read_text())
    wins, losses = {}, {}
    for g in games:
        if g["home_score"] == 0 and g["away_score"] == 0:
            continue
        if g.get("stats_missing"):
            continue
        winner = g["home"] if g["home_score"] > g["away_score"] else g["away"]
        loser = g["away"] if winner == g["home"] else g["home"]
        wins[winner] = wins.get(winner, 0) + 1
        losses[loser] = losses.get(loser, 0) + 1
    rows = []
    for name, t in sorted(teams.items(),
                          key=lambda kv: kv[1]["net"], reverse=True):
        rows.append({
            "team": name, "conf": CONFERENCE[name],
            "gp": t["games"],
            "adj_o": round(t["adj_o"], 1), "adj_d": round(t["adj_d"], 1),
            "net": round(t["net"], 1), "adj_t": round(t["adj_t"], 1),
            "w": wins.get(name, 0), "l": losses.get(name, 0),
        })
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        if r["w"] + r["l"] != r["gp"]:
            raise SystemExit(
                f"W-L does not sum to GP for {r['team']}: "
                f"{r['w']}-{r['l']} vs {r['gp']} games")
    return rows, doc["league_o"], doc["league_t"], doc.get("info", {})


def game_logs():
    """(team_names, logs) for the expandable game-log panels.

    Same game set as the ratings (played, boxscore present).
    logs maps a team's index to a list of [date, opponent index,
    venue, points for, points against], in date order.
    """
    games = json.loads((DATA / "games_2026_27.json").read_text())
    games = [g for g in games
             if not (g["home_score"] == 0 and g["away_score"] == 0)
             and not g.get("stats_missing")]
    names = sorted({g["home"] for g in games} | {g["away"] for g in games})
    idx = {name: i for i, name in enumerate(names)}
    logs = {i: [] for i in range(len(names))}
    for g in sorted(games, key=lambda x: (x["date"], x["event_id"])):
        for side, (team, opp, pts, opp_pts) in enumerate((
                (g["home"], g["away"], g["home_score"], g["away_score"]),
                (g["away"], g["home"], g["away_score"], g["home_score"]))):
            venue = "N" if g.get("neutral_site") else ("H" if side == 0 else "A")
            logs[idx[team]].append([g["date"], idx[opp], venue, pts, opp_pts])
    return names, logs


def rosters():
    """team -> roster, one compact list per team.

    [name, position, gp, pts_pg, ortg, poss_pct, min_pct, rpr, tier],
    sorted by minutes descending. Every player who appeared is
    included (2026-27, like RonPom's current season). Tier comes
    from the GM board CSV; a player missing there gets "".
    """
    players = read_csv(DATA / "players_2026_27.csv")
    gm = read_csv(DATA / "gm_board_2026_27.csv")
    tier_by_id = {r["athlete_id"]: r["tier"] for r in gm}
    by_team = {}
    for p in players:
        by_team.setdefault(p["team"], []).append(p)
    out = {}
    for team, lst in by_team.items():
        lst.sort(key=lambda p: float(p["min"]), reverse=True)
        out[team] = [[
            p["name"], p["position"], int(p["gp"]),
            float(p["pts_pg"]),
            float(p["ortg"]) if p["ortg"] else None,
            float(p["poss_pct"]) if p["poss_pct"] else None,
            float(p["min_pct"]) if p["min_pct"] else None,
            float(p["rpr"]) if p["rpr"] else None,
            tier_by_id.get(p["athlete_id"], ""),
        ] for p in lst]
    return out


def team_table(rows, log_idx):
    ranks = metric_ranks(rows)
    RANKS.update(ranks)
    tid = "2026_27"
    tabs = ["<div class=\"conftabs\" data-table=\"teams-2026_27\">"
            "<button class=\"conf-tab active\" data-conf=\"\">All</button>"
            "<button class=\"conf-tab\" data-conf=\"east\">East</button>"
            "<button class=\"conf-tab\" data-conf=\"west\">West</button>"
            "</div>"]
    out = ["\n".join(tabs),
           "<input class=\"filter\" type=\"search\" "
           "placeholder=\"Filter teams...\" data-table=\"teams-2026_27\">"]
    out.append("<table class=\"ratings\" id=\"teams-2026_27\">"
               "<thead><tr>"
               "<th data-t=\"num\">Rk</th>"
               "<th data-t=\"text\">Team</th>"
               "<th data-t=\"text\">Conf</th>"
               "<th data-t=\"wl\">W-L</th>"
               "<th data-t=\"num\">AdjO</th>"
               "<th data-t=\"num\">AdjD</th>"
               "<th data-t=\"num\">Net</th>"
               "<th data-t=\"num\">AdjT</th>"
               "</tr></thead><tbody>")
    for r in rows:
        out.append(
            f"<tr class=\"team\" data-conf=\"{r['conf'].lower()}\" "
            f"data-season=\"2026-27\" data-tidx=\"{log_idx.get(r['team'], '')}\">"
            f"<td data-v=\"{r['rank']}\">{r['rank']}</td>"
            f"<td data-v=\"{html.escape(r['team'].lower())}\">"
            f"<a class=\"team-page-link\" "
            f"href=\"teams/{team_slug(r['team'])}.html\">"
            f"{html.escape(r['team'])}</a></td>"
            f"<td data-v=\"{r['conf'].lower()}\">{r['conf']}</td>"
            f"<td data-v=\"{r['w'] * 100 - r['l']}\">{r['w']}-{r['l']}</td>"
            f"{_metric_cell(r['adj_o'], r['adj_o'], ranks['adj_o'].get(r['team']))}"
            f"{_metric_cell(r['adj_d'], r['adj_d'], ranks['adj_d'].get(r['team']))}"
            f"<td class=\"net\" data-v=\"{r['net']}\">{r['net']}</td>"
            f"{_metric_cell(r['adj_t'], r['adj_t'], ranks['adj_t'].get(r['team']))}"
            "</tr>")
    out.append("</tbody></table>")
    out.append(
        f"<p class=\"note\">Full table above ({len(rows)} teams). "
        "Download the team ratings: "
        "<a href=\"data/teams_2026_27.json\">teams_2026_27.json</a>. "
        "W-L is the record in the games these ratings are built from."
        "</p>")
    return "\n".join(out)


def _leader_table(title, players, key, extra_key, extra_label):
    """One top-5 leader table. key/extra_key are player CSV fields."""
    top = sorted(players, key=lambda p: float(p[key] or 0),
                 reverse=True)[:5]
    out = [f"<div><h3>{title}</h3><table><thead><tr>"
           f"<th>Player</th><th>Team</th><th>{title.split(' by ')[-1]}</th>"
           f"<th>{extra_label}</th><th>PPG</th></tr></thead><tbody>"]
    for p in top:
        out.append(f"<tr><td>{html.escape(p['name'])}</td>"
                   f"<td>{html.escape(p['team'])}</td>"
                   f"<td>{p[key]}</td><td>{p[extra_key]}</td>"
                   f"<td>{p['pts_pg']}</td></tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def player_leaders(players, min_games=2):
    """Top 5 by PTS, REB, AST, ORtg, and RPR.

    Same floor as RonPom's 2026-27 leaders: min 2 games and 40%
    of team minutes, so a two-minute cameo cannot top the list.
    """
    qualified = [p for p in players
                 if int(p["gp"]) >= min_games and p["rpr"]
                 and p["min_pct"] and float(p["min_pct"]) >= 40]
    out = ["<div class=\"leaders\">"]
    out.append(_leader_table("Top players by PTS", qualified,
                             "pts_pg", "rpr", "RPR"))
    out.append(_leader_table("Top players by REB", qualified,
                             "reb_pg", "rpr", "RPR"))
    out.append(_leader_table("Top players by AST", qualified,
                             "ast_pg", "rpr", "RPR"))
    out.append(_leader_table("Top players by ORtg", qualified,
                             "ortg", "poss_pct", "%Poss"))
    out.append(_leader_table("Top players by RPR", qualified,
                             "rpr", "ortg", "ORtg"))
    out.append("</div>")
    out.append(
        f"<p class=\"note\">Min {min_games} games and 40% of team minutes. "
        "ORtg is approximate (no assist credit); read it with %Poss. "
        "Full profiles in "
        "<a href=\"data/players_2026_27.csv\">players_2026_27.csv</a>.</p>")
    return "\n".join(out)


def top_players_section(players):
    """Full top-100 players table for 2026-27, by RPR.

    Same filters as the leader tables: min 2 GP, 40% of team
    minutes. Sortable and filterable like the team table.
    Team names link to that team's own page.
    """
    qualified = [p for p in players
                 if int(p["gp"]) >= 2 and p["rpr"]
                 and p["min_pct"] and float(p["min_pct"]) >= 40]
    top = sorted(qualified, key=lambda p: float(p["rpr"]),
                 reverse=True)[:100]

    def num(v, disp):
        return (f"<td data-v=\"{v}\">{disp}</td>" if v not in (None, "")
                else "<td data-v=\"0\">&mdash;</td>")

    out = ["<section id=\"top-players\"><h2>Top players</h2>",
           "<p>Top 100 players of 2026-27 by RPR. Minimum 2 games "
           "and 40% of team minutes, same as the leader tables. "
           "Click a column header to sort; the filter box searches "
           "player names. Click a team name to open that team's "
           "own page.</p>",
           "<input class=\"filter\" type=\"search\" "
           "placeholder=\"Filter players...\" data-table=\"top-players\">",
           "<table class=\"ratings\" id=\"top-players\">"
           "<thead><tr>"
           "<th data-t=\"num\">Rk</th>"
           "<th data-t=\"text\">Player</th>"
           "<th data-t=\"text\">Team</th>"
           "<th data-t=\"text\">Pos</th>"
           "<th data-t=\"num\">GP</th>"
           "<th data-t=\"num\">PPG</th>"
           "<th data-t=\"num\">ORtg</th>"
           "<th data-t=\"num\">%Poss</th>"
           "<th data-t=\"num\">%Min</th>"
           "<th data-t=\"num\">RPR</th>"
           "</tr></thead><tbody>"]
    for rank, p in enumerate(top, 1):
        pos = p["position"] if p["position"] not in ("", "NA") else ""
        out.append(
            f"<tr>"
            f"<td data-v=\"{rank}\">{rank}</td>"
            f"<td data-v=\"{html.escape(p['name'].lower())}\">"
            f"{html.escape(p['name'])}</td>"
            f"<td data-v=\"{html.escape(p['team'].lower())}\">"
            f"<a href=\"teams/{team_slug(p['team'])}.html\">"
            f"{html.escape(p['team'])}</a></td>"
            f"<td data-v=\"{html.escape(pos.lower())}\">"
            f"{html.escape(pos) if pos else '&mdash;'}</td>"
            f"<td data-v=\"{p['gp']}\">{p['gp']}</td>"
            f"{num(p['pts_pg'], p['pts_pg'])}"
            f"{num(p['ortg'], p['ortg'])}"
            f"{num(p['poss_pct'], p['poss_pct'])}"
            f"{num(p['min_pct'], p['min_pct'])}"
            f"{num(p['rpr'], p['rpr'])}"
            f"</tr>")
    out.append("</tbody></table>")
    out.append(
        "<p class=\"note\">ORtg is approximate (no assist credit); "
        "read it with %Poss. RPR is our own single-number rating, "
        "not KenPom's proprietary kPOY. Full profiles in "
        "<a href=\"data/players_2026_27.csv\">players_2026_27.csv</a>."
        "</p></section>")
    return "\n".join(out)


def gm_board_section():
    """GM acquisition board tiers, from data/gm_board_2026_27.csv."""
    gm = read_csv(DATA / "gm_board_2026_27.csv")
    tiers = {}
    for row in gm:
        tiers.setdefault(row["tier"], []).append(row)
    tier_order = ["Franchise cornerstone", "All-Star level",
                  "High-value starter", "Undervalued / buy-low",
                  "Elite role player", "Overvalued / avoid"]
    out = ["<section id=\"gm-board\"><h2>GM acquisition board</h2>",
           "<p>Tiers are set by thresholds on RPR, efficiency at "
           "usage, defense, and availability, not by reputation. "
           "No contracts, salaries, age, or height/weight in v1. "
           "Full board in "
           "<a href=\"data/gm_board_2026_27.csv\">gm_board_2026_27.csv</a>."
           "</p>"]
    for tier in tier_order:
        members = sorted(tiers.get(tier, []),
                         key=lambda r: float(r["rpr"] or 0),
                         reverse=True)[:10]
        if not members:
            continue
        items = ", ".join(
            f"{html.escape(m['name'])} ({html.escape(m['team'])}, "
            f"RPR {m['rpr']})" for m in members)
        out.append(f"<h3>{html.escape(tier)} "
                   f"({len(tiers.get(tier, []))})</h3><p>{items}</p>")
    out.append("</section>")
    return "\n".join(out)


def team_needs_section():
    """Team needs and realistic trade targets, from team_needs.py.

    One row per team: star, top need, top realistic target and its
    TradeScore. The full per-need lists live in
    data/team_needs_2026_27.json/.csv; this section is the summary.
    If team_needs.py has not been run yet, the section says so
    instead of rendering stale or empty data.
    """
    path = DATA / "team_needs_2026_27.json"
    if not path.exists():
        return ("<section id=\"team-needs\"><h2>Team needs and "
                "trade fit</h2><p>Run <code>python3 team_needs.py"
                "</code> to build this section.</p></section>")
    doc = json.loads(path.read_text())
    teams = doc["teams"]
    out = ["<section id=\"team-needs\"><h2>Team needs and trade fit</h2>",
           "<p>Each team's strengths and weaknesses, its star, and the "
           "realistic players who would fill its biggest holes. "
           "<strong>Realistic</strong> is the point: targets are only "
           "Rotation / depth, Elite role player, Undervalued / buy-low, "
           "or High-value starter players, never a top-3 player on "
           "their own team, never a 20-point scorer or a 30-minute-a-"
           "night player on their own team. No contracts or salaries "
           "exist in the data, so none are used. This is a box-score "
           "fit, a prior for lineup data, not a lineup prediction. "
           "Full per-team lists in "
           "<a href=\"data/team_needs_2026_27.csv\">"
           "team_needs_2026_27.csv</a>.</p>",
           "<table class=\"ratings\"><thead><tr>"
           "<th data-t=\"text\">Team</th>"
           "<th data-t=\"text\">Star</th>"
           "<th data-t=\"text\">Top need</th>"
           "<th data-t=\"text\">Top realistic target</th>"
           "<th data-t=\"num\">TradeScore</th>"
           "</tr></thead><tbody>"]
    for team in sorted(teams, key=lambda t: teams[t]["net"],
                       reverse=True):
        prof = teams[team]
        star = (prof["star"] or {}).get("name", "")
        need = prof["ranked_needs"][0] if prof["ranked_needs"] else ""
        top = (prof["targets"]["top_overall"][0]
               if prof["targets"]["top_overall"] else None)
        if top:
            target = (f"{html.escape(top['name'])} "
                      f"({html.escape(top['team'])}, "
                      f"{html.escape(top['tier'])}, fills "
                      f"{html.escape(top['fills_need'])}, "
                      f"Q {top['quality_gain']:+.3f} "
                      f"F {top['fit_gain']:+.3f})")
            score = f"{top['trade_score']:.3f}"
        else:
            target, score = "&mdash;", "&mdash;"
        out.append(
            f"<tr><td data-v=\"{html.escape(team.lower())}\">"
            f"<a href=\"teams/{team_slug(team)}.html\">"
            f"{html.escape(team)}</a></td>"
            f"<td data-v=\"{html.escape(star.lower())}\">"
            f"{html.escape(star)}</td>"
            f"<td data-v=\"{html.escape(need)}\">"
            f"{html.escape(need)}</td>"
            f"<td data-v=\"{html.escape(top['name'].lower()) if top else ''}\">"
            f"{target}</td>"
            f"<td data-v=\"{score}\">{score}</td></tr>")
    out.append("</tbody></table>")
    unfilled = [(t, k, v) for t, p in teams.items()
                for k, v in p["targets"].get("unfilled_needs", {}).items()]
    if unfilled:
        out.append("<h3>No realistic target for a need</h3>")
        for team, need, reason in unfilled:
            out.append(f"<p><strong>{html.escape(team)}</strong> &mdash; "
                       f"{html.escape(need)}: {html.escape(reason)}</p>")
    out.append("</section>")
    return "\n".join(out)


def _rank_str(team, key):
    """' (#n)' for a team's rank in one metric, or ''."""
    r = RANKS.get(key, {}).get(team)
    return f" (#{r})" if r is not None else ""


def _roster_table(roster):
    """Roster HTML table, shared by team pages and (via JS) the
    inline detail panels' data. Columns: Player, Pos, GP, PPG,
    ORtg, %Poss, %Min, RPR, Tier."""
    parts = ['<table class="roster"><thead><tr><th>Player</th>'
             "<th>Pos</th><th>GP</th><th>PPG</th><th>ORtg</th>"
             "<th>%Poss</th><th>%Min</th><th>RPR</th><th>Tier</th>"
             "</tr></thead><tbody>"]
    for p in roster:
        pos = p[1] if p[1] not in ("", "NA", None) else "\u2014"
        cells = [html.escape(p[0]), pos, str(p[2])]
        for v in p[3:8]:
            cells.append("\u2014" if v is None else f"{v:.1f}")
        cells.append(html.escape(p[8]) if p[8] else "\u2014")
        parts.append("<tr>" + "".join(
            f"<td>{c}</td>" for c in cells) + "</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def team_page_html(team, row, slug_by_team):
    """One team's full page, as an HTML string."""
    conf = row["conf"]
    parts = [f"<h1>{html.escape(team)}</h1>",
             f"<p class=\"subtitle\">"
             f"{html.escape(conf)}ern Conference"
             f" &middot; <a href=\"../index.html\">&larr; NBA "
             f"Ratings</a></p>"]
    parts.append(
        f"<p>Net rank #{row['rank']} &middot; AdjO {row['adj_o']}"
        f"{_rank_str(team, 'adj_o')} "
        f"&middot; AdjD {row['adj_d']}"
        f"{_rank_str(team, 'adj_d')} &middot; Net {row['net']} "
        f"&middot; AdjT {row['adj_t']}"
        f"{_rank_str(team, 'adj_t')} &middot; {row['w']}-{row['l']}</p>")
    needs = NEEDS.get(team)
    if needs:
        star = needs["star"] or {}
        parts.append("<h3>Team needs</h3>")
        parts.append(
            f"<p>Star: {html.escape(star.get('name', ''))} "
            f"(RPR {star.get('rpr')}) &middot; Top needs: "
            f"{', '.join(html.escape(n) for n in needs['ranked_needs'][:3])}</p>")
        parts.append(
            f"<p>Strengths: "
            f"{html.escape('; '.join(needs['strengths'])) or 'none flagged'}"
            f"<br>Weaknesses: "
            f"{html.escape('; '.join(needs['weaknesses'])) or 'none flagged'}</p>")
        top = needs["targets"]["top_overall"][:3]
        if top:
            parts.append("<p>Top realistic targets: " + "; ".join(
                f"{html.escape(s['name'])} "
                f"({html.escape(s['team'])}, {html.escape(s['tier'])}, "
                f"fills {html.escape(s['fills_need'])}, "
                f"TradeScore {s['trade_score']:.3f})" for s in top) + "</p>")
        for need, reason in needs["targets"].get(
                "unfilled_needs", {}).items():
            parts.append(f"<p>{html.escape(need)}: "
                         f"{html.escape(reason)}</p>")
    games = LOGS["2026-27"]["logs"].get(LOG_IDX[team], [])
    names = LOGS["2026-27"]["teams"]
    parts.append("<h3>Game log</h3>")
    if games:
        parts.append('<table class="gamelog"><thead><tr><th>Date</th>'
                     "<th>Opponent</th><th>Venue</th><th>Result</th>"
                     "<th>Score</th></tr></thead><tbody>")
        for g in games:
            opp = names[g[1]]
            opp_html = html.escape(opp)
            if opp in slug_by_team:
                opp_html = (f"<a href=\"{slug_by_team[opp]}.html\">"
                            f"{opp_html}</a>")
            f_pts, a_pts = g[3], g[4]
            parts.append(
                f"<tr><td>{g[0][4:6]}/{g[0][6:8]}</td>"
                f"<td>{opp_html}</td><td>{g[2]}</td>"
                f"<td>{'W' if f_pts > a_pts else 'L'}</td>"
                f"<td>{f_pts}-{a_pts}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No game log for this season.</p>")
    roster = ROSTERS["2026-27"].get(team)
    parts.append("<h3>Roster</h3>")
    if roster:
        parts.append(_roster_table(roster))
        parts.append('<p class="note">Sorted by minutes. ORtg is '
                     "approximate (no assist credit); RPR is our own "
                     "rating, not KenPom\u2019s kPOY. Tier is the GM "
                     "board tier for that player.</p>")
    else:
        parts.append("<p>No roster data for this season.</p>")
    body = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(team)} | NBA Ratings</title>
<style>{{CSS}}</style>
</head>
<body>
{body}
<footer>
<p>Data: ESPN public scoreboard and box scores. Code and full CSVs:
<a href="{REPO}">RD3-LIL/nba-ratings on GitHub</a>. Built {{BUILT}}.</p>
</footer>
</body>
</html>
"""


def build_team_pages(rows_by_team, slug_by_team):
    """Write teams/<slug>.html for every team.

    Returns (page count, total bytes). Fails loudly on a slug
    collision. Deletes stale teams/*.html files not written
    this run, so a renamed team cannot leave a dead page.
    """
    teams_dir = HERE / "teams"
    teams_dir.mkdir(exist_ok=True)
    seen = {}
    for team in rows_by_team:
        s = team_slug(team)
        if s in seen:
            raise SystemExit(f"slug collision: {team!r} and "
                             f"{seen[s]!r} both map to {s!r}")
        seen[s] = team
    if set(seen.values()) != set(slug_by_team):
        raise SystemExit("slug map mismatch")
    written = set()
    total = 0
    for team in sorted(rows_by_team):
        text = team_page_html(team, rows_by_team[team], slug_by_team)
        text = text.replace("{CSS}", CSS).replace("{BUILT}", BUILT)
        path = teams_dir / f"{slug_by_team[team]}.html"
        path.write_text(text)
        written.add(path.name)
        total += len(text.encode("utf-8"))
    for stale in teams_dir.glob("*.html"):
        if stale.name not in written:
            stale.unlink()
    return len(written), total


CSS = """
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; color: #1f2328;
  line-height: 1.5; }
h1 { font-size: 2em; margin-bottom: 0; }
h2 { border-bottom: 1px solid #d0d7de; padding-bottom: 6px; margin-top: 40px; }
h3 { margin-top: 28px; }
.subtitle { color: #656d76; margin-top: 4px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }
th { background: #f6f8fa; }
nav.topnav { position: sticky; top: 0; z-index: 20; background: #fff;
  border-bottom: 1px solid #d0d7de; padding: 8px 0; display: flex;
  gap: 4px; flex-wrap: wrap; }
nav.topnav a { padding: 4px 10px; border-radius: 6px; color: #0969da;
  text-decoration: none; font-weight: 600; font-size: 14px; }
nav.topnav a:hover { background: #eaeef2; }
section, div[id] { scroll-margin-top: 54px; }
table.ratings { font-size: 13px; }
table.ratings th, table.ratings td { padding: 3px 8px; }
table.ratings thead th { position: sticky; top: 40px; cursor: pointer;
  user-select: none; }
table.ratings thead th:hover { background: #eaeef2; }
table.ratings thead th.sorted-asc::after { content: " \\25B2"; }
table.ratings thead th.sorted-desc::after { content: " \\25BC"; }
table.ratings tbody tr:nth-child(even) td { background: #f6f8fa; }
table.ratings tbody tr.team { cursor: pointer; }
table.ratings tbody tr.team:hover td { background: #eef4ff; }
table.ratings td:nth-child(n+4) { text-align: right; font-variant-numeric: tabular-nums; }
table.ratings td.net { font-weight: 700; }
table.ratings td .sub-rank { display: block; font-size: 10px;
  line-height: 1.1; color: #656d76; font-weight: 400; }
tr.detail td { background: #fff; padding: 10px 14px; cursor: default; }
.detail-box { margin-bottom: 8px; }
table.gamelog { font-size: 13px; width: auto; margin: 0; }
table.gamelog th, table.gamelog td { padding: 2px 10px; }
table.gamelog td:nth-child(n+3) { text-align: right; }
.detail-box h4 { margin: 14px 0 4px; }
table.roster { font-size: 13px; width: auto; margin: 0; }
table.roster th, table.roster td { padding: 2px 10px; }
table.roster td:nth-child(n+3) { text-align: right; }
a.team-link, a.team-page-link { color: #0969da; text-decoration: none; }
a.team-link:hover, a.team-page-link:hover { text-decoration: underline; }
.conftabs { display: flex; flex-wrap: wrap; gap: 4px; margin: 8px 0 4px; }
button.conf-tab { padding: 3px 9px; font-size: 12px;
  border: 1px solid #d0d7de; border-radius: 999px; background: #fff;
  cursor: pointer; }
button.conf-tab.active { background: #1f2328; color: #fff;
  border-color: #1f2328; }
input.filter { padding: 6px 10px; font-size: 14px; width: 240px;
  border: 1px solid #d0d7de; border-radius: 6px; margin: 4px 0; }
.leaders { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.leaders table { font-size: 15px; }
.leaders td:nth-child(n+3) { text-align: right; }
@media (max-width: 700px) { .leaders { grid-template-columns: 1fr; } }
.note { color: #656d76; font-size: 14px; }
.warn { background: #fff8e6; border: 1px solid #e3b341; padding: 10px 14px;
  border-radius: 6px; }
footer { margin-top: 48px; color: #656d76; font-size: 14px;
  border-top: 1px solid #d0d7de; padding-top: 16px; }
"""

JS = """
// Ratings tables: click-to-sort, text filter, conference tabs,
// and click a team row to expand its game log (GAME_LOGS is
// embedded in the page by the builder). Plain script, no
// network, no dependencies. Numeric columns sort on the
// data-v value; team and conference sort as text; W-L sorts on
// wins first (data-v is wins*100 - losses).
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function fmt1(v) {
  return (v === null || v === undefined) ? "\\u2014" : v.toFixed(1);
}
function closeDetails(table) {
  table.querySelectorAll("tbody tr.detail").forEach(function (d) {
    d.remove();
  });
}
function applyFilter(table) {
  var q = table._q || "";
  var conf = table._conf || "";
  table.querySelectorAll("tbody tr:not(.detail)").forEach(function (r) {
    var team = r.cells[1].textContent.toLowerCase();
    var ok = (q === "" || team.indexOf(q) !== -1)
      && (conf === "" || r.dataset.conf === conf);
    r.style.display = ok ? "" : "none";
  });
}
function openDetail(table, row) {
  var next = row.nextElementSibling;
  if (next && next.classList.contains("detail")) {
    next.remove();
    return;
  }
  closeDetails(table);
  var entry = GAME_LOGS["2026-27"];
  if (!entry || row.dataset.tidx === "") return;
  var games = entry.logs[row.dataset.tidx] || [];
  var teams = entry.teams;
  var c = row.cells;
  var html = '<div class="detail-box"><strong>' + esc(c[1].textContent)
    + '</strong> &mdash; AdjO ' + c[4].dataset.v + ' (#' + c[4].dataset.r + ') &middot; AdjD '
    + c[5].dataset.v + ' (#' + c[5].dataset.r + ') &middot; Net ' + c[6].dataset.v
    + ' &middot; AdjT ' + c[7].dataset.v + ' (#' + c[7].dataset.r + ') &middot; '
    + c[3].textContent + '</div>'
    + '<table class="gamelog"><thead><tr><th>Date</th><th>Opponent</th>'
    + '<th>Venue</th><th>Result</th><th>Score</th></tr></thead><tbody>';
  games.forEach(function (g) {
    var f = g[3], a = g[4];
    html += "<tr><td>" + g[0].slice(4, 6) + "/" + g[0].slice(6, 8)
      + "</td><td>" + esc(teams[g[1]]) + "</td><td>" + g[2]
      + "</td><td>" + (f > a ? "W" : "L") + "</td><td>"
      + f + "-" + a + "</td></tr>";
  });
  html += "</tbody></table>";
  var roster = (ROSTERS["2026-27"] || {})[c[1].textContent];
  if (roster && roster.length) {
    html += "<h4>Roster</h4>"
      + '<table class="roster"><thead><tr><th>Player</th><th>Pos</th>'
      + "<th>GP</th><th>PPG</th><th>ORtg</th><th>%Poss</th>"
      + "<th>%Min</th><th>RPR</th><th>Tier</th></tr></thead><tbody>";
    roster.forEach(function (p) {
      html += "<tr><td>" + esc(p[0]) + "</td><td>"
        + ((p[1] && p[1] !== "NA") ? esc(p[1]) : "\\u2014")
        + "</td><td>" + p[2] + "</td><td>" + fmt1(p[3]) + "</td><td>"
        + fmt1(p[4]) + "</td><td>" + fmt1(p[5]) + "</td><td>"
        + fmt1(p[6]) + "</td><td>" + fmt1(p[7]) + "</td><td>"
        + (p[8] ? esc(p[8]) : "\\u2014") + "</td></tr>";
    });
    html += "</tbody></table>"
      + '<p class="note">Sorted by minutes. ORtg is approximate '
      + "(no assist credit); RPR is our own rating, not KenPom\\u2019s "
      + "kPOY. Tier is the GM board tier for that player.</p>";
  }
  var tr = document.createElement("tr");
  tr.className = "detail";
  var td = document.createElement("td");
  td.colSpan = 8;
  td.innerHTML = html;
  tr.appendChild(td);
  row.after(tr);
}
document.querySelectorAll("table.ratings").forEach(function (table) {
  var headers = table.querySelectorAll("thead th");
  headers.forEach(function (th, i) {
    th.addEventListener("click", function () {
      closeDetails(table);
      var asc = !th.classList.contains("sorted-asc");
      headers.forEach(function (h) {
        h.classList.remove("sorted-asc", "sorted-desc");
      });
      th.classList.add(asc ? "sorted-asc" : "sorted-desc");
      var rows = Array.from(table.querySelectorAll("tbody tr:not(.detail)"));
      var numeric = th.dataset.t === "num" || th.dataset.t === "wl";
      rows.sort(function (a, b) {
        var va = a.cells[i].dataset.v, vb = b.cells[i].dataset.v;
        var cmp = numeric ? (parseFloat(va) - parseFloat(vb))
          : (va < vb ? -1 : va > vb ? 1 : 0);
        return asc ? cmp : -cmp;
      });
      var body = table.querySelector("tbody");
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });
  table.querySelectorAll("tbody tr.team").forEach(function (row) {
    row.addEventListener("click", function (e) {
      // The team name is a link to the team page; let it go
      // there instead of toggling the detail panel.
      if (e.target.closest("a")) return;
      openDetail(table, row);
    });
  });
});
document.querySelectorAll("input.filter").forEach(function (input) {
  input.addEventListener("input", function () {
    var table = document.getElementById(input.dataset.table);
    closeDetails(table);
    table._q = input.value.trim().toLowerCase();
    applyFilter(table);
  });
});
document.querySelectorAll(".conftabs").forEach(function (bar) {
  bar.addEventListener("click", function (e) {
    var btn = e.target.closest("button.conf-tab");
    if (!btn) return;
    bar.querySelectorAll("button").forEach(function (b) {
      b.classList.remove("active");
    });
    btn.classList.add("active");
    var table = document.getElementById(bar.dataset.table);
    closeDetails(table);
    table._conf = btn.dataset.conf;
    applyFilter(table);
  });
});
"""

RANKS = {}
LOGS = {}
ROSTERS = {}
LOG_IDX = {}
NEEDS = {}
BUILT = datetime.now(ZoneInfo("America/Chicago")).strftime("%B %-d, %Y")


def main():
    rows, league_o, league_t, info = load_teams()
    players = read_csv(DATA / "players_2026_27.csv")
    log_names, log_entries = game_logs()
    LOGS["2026-27"] = {"teams": log_names, "logs": log_entries}
    ROSTERS["2026-27"] = rosters()
    log_idx = {name: i for i, name in enumerate(log_names)}
    LOG_IDX.update(log_idx)
    rows_by_team = {r["team"]: r for r in rows}
    slug_by_team = {t: team_slug(t) for t in rows_by_team}

    n_games = sum(r["gp"] for r in rows) // 2
    table_html = team_table(rows, log_idx)
    leaders_html = player_leaders(players)
    top_html = top_players_section(players)
    gm_html = gm_board_section()
    needs_path = DATA / "team_needs_2026_27.json"
    if needs_path.exists():
        NEEDS.update(json.loads(needs_path.read_text())["teams"])
    needs_html = team_needs_section()
    logs_json = json.dumps(LOGS, separators=(",", ":"))
    rosters_json = json.dumps(ROSTERS, separators=(",", ":"))
    built = BUILT

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NBA Ratings - 2026-27</title>
<style>{CSS}</style>
</head>
<body>
<h1>NBA Ratings</h1>
<p class="subtitle">NBA ratings in the style of RonPom (College Ratings),
built from free ESPN data. Adjusted offense and defense per 100
possessions, solved properly (damped Gauss-Seidel), league-average
prior. Same ORtg, %Poss, and RPR formulas as RonPom, adapted to
48-minute games.</p>

<nav class="topnav">
<a href="#team-ratings">Team ratings</a>
<a href="#players">Player leaders</a>
<a href="#top-players">Top players</a>
<a href="#gm-board">GM board</a>
<a href="#team-needs">Team needs</a>
<a href="#how-to-read">How to read this</a>
</nav>

<section id="team-ratings"><h2>2026-27 team ratings</h2>
<p class="warn">Early season: most teams have only about 15 games so far.
The ratings use a league-average prior (no last-season prior: NBA
teams change too much year over year for one to help), so the top of
the table is still mostly noise, not a real ranking.</p>
<p>{len(rows)} teams rated, {len(players)} players, {n_games} games.
Click a column header to sort. Click a team name for that team's own
page, or a team row to see its game log and roster here.</p>
{table_html}
</section>

<section id="players"><h2>Player leaders</h2>
{leaders_html}
</section>

{top_html}

{gm_html}

{needs_html}

<section id="how-to-read"><h2>How to read this</h2>
<p><strong>Getting around</strong>: the bar at the top jumps to the
team ratings, the player leaders, the top 100 players, or the GM
board. East / West tabs above the team table filter to one
conference; the filter box searches team names. Click a team name
anywhere to open that team's own page, with its ratings, game log,
and roster. Click a team row to see its game log and roster for the
season right here.</p>
<p><strong>AdjO / AdjD</strong>: points scored and allowed per 100
possessions, adjusted for opponent. The small number under each
value is that team's rank in the metric; AdjD rank 1 is the best
defense. <strong>Net</strong> is AdjO minus AdjD, and its rank is the
Rk column. <strong>AdjT</strong> is adjusted tempo, possessions per
game. <strong>Conf</strong> is East or West. <strong>W-L</strong> is
the record in the games these ratings are built from, so W+L equals
GP.</p>
<p><strong>ORtg</strong> (players) is a simplified approximation:
points per 100 possessions used, with no assist credit, so it
underrates playmakers. Read it with %Poss. <strong>RPR</strong> is
our own single-number player rating, not KenPom's proprietary
kPOY. Leaderboards only include players with at least 40% of their
team's minutes, so a two-minute cameo cannot top the list.</p>
<p><strong>Honest limits</strong>: about 15 games per team, so the
order is still noisy. League-average prior only, no last-season
prior. Box-score only: no tracking, no on/off, no opponent
adjustment at player level. No contracts, salaries, age, or
height/weight. Full method in the README.</p></section>

<footer>
<p>Data: ESPN public scoreboard and box scores. Code and full CSVs:
<a href="{REPO}">RD3-LIL/nba-ratings on GitHub</a>. Built {built}.</p>
</footer>
<script>var GAME_LOGS = {logs_json};</script>
<script>var ROSTERS = {rosters_json};</script>
<script>{JS}</script>
</body>
</html>
"""
    out = HERE / "index.html"
    out.write_text(page)
    print(f"Wrote {out}: {len(page)} bytes")
    n_pages, n_bytes = build_team_pages(rows_by_team, slug_by_team)
    print(f"Wrote {n_pages} team pages in teams/: {n_bytes} bytes")
    print(f"Teams: {len(rows)}, players: {len(players)}, games: {n_games}, "
          f"league OffEff {league_o:.1f}, tempo {league_t:.1f}")


if __name__ == "__main__":
    main()
