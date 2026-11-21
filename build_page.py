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
top 5 by PTS, REB, AST, ORtg (full), and RPR (min 2 games, 40% of team
minutes), plus a sortable top-100 players table by RPR with a
Rookies-only toggle. A Rookies section lists the top 10 rookies
by RPR (same 40% floor) plus notable rookies below the floor,
labelled as such. A GM board section lists the acquisition tiers.
A rookie is a player whose ESPN roster entry shows
experience.years == 0 (first NBA season).

Where the columns come from:
- Conf: East/West from the static CONFERENCE map below (standard
  NBA alignment, 15 teams each). ESPN's NBA teams endpoint carries
  no conference field, so this is a fixed mapping, not fetched.
- W-L: the record in the games these ratings are built from
  (played, boxscore present). W+L equals GP for every team.
- AdjO/AdjD/Net/AdjT: team_ratings.py solver output in
  data/teams_2026_27.json (damped Gauss-Seidel, league-average
  prior, margin cap 20, home court estimated from the data).
  No last-season prior: Ron decided NBA teams change too much
  year over year for one to help.
- ORtg (full): the Oliver ORtg with assist credit from
  data/players_2026_27_eval.csv, falling back to the shrunk
  simple ORtg when it is missing. The simple ORtg (no assist
  credit) is shown in the detail rows, never as the main column.
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
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"
REPO = "https://github.com/RD3-LIL/nba-ratings"

# Teams reflect the 2026-27 rosters after a busy 2026 offseason.
# The note below says so on the index and on every team page.
DISCLAIMER = ("Teams reflect the 2026-27 rosters after a busy "
              "2026 offseason: Giannis Antetokounmpo to Miami, "
              "LeBron James to Philadelphia, Walker Kessler to "
              "the Lakers, and Anthony Davis to Washington, "
              "among others.")

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


def player_slug(name, athlete_id, team):
    """Player name -> stable, unique URL slug for players/<slug>.html.

    Name slug + athlete id + team slug, same formula as
    build_player_pages.player_slug (duplicated here so
    build_page.py does not import the player builder, which
    imports this module). The team is in there because two
    players have one profile per team after changing teams
    mid-season; name + id alone would collide for them.
    """
    norm = unicodedata.normalize("NFKD", name)
    ascii_name = norm.encode("ascii", "ignore").decode("ascii")
    s = ascii_name.lower().replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return f"{s}-{athlete_id}-{team_slug(team)}"


def _money_m(value):
    """Salary in dollars to '$5.48M', None to 'unknown' (never a guess)."""
    if value is None:
        return "unknown"
    return f"${value / 1e6:.2f}M"


def _signed_money_m(value):
    """Signed net salary change in dollars to '-$3.89M', None to '-'."""
    if value is None:
        return "-"
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value) / 1e6:.2f}M"


def _payroll_line(payroll, cap_lines):
    """'over the tax line' style label for a payroll, or ''.

    cap_lines comes from the team_needs payload (Basketball-
    Reference fetch): cap, tax, first apron, second apron.
    """
    if payroll is None or not cap_lines:
        return ""
    if payroll >= cap_lines.get("second_apron", float("inf")):
        return "over the second apron"
    if payroll >= cap_lines.get("first_apron", float("inf")):
        return "over the first apron"
    if payroll >= cap_lines.get("luxury_tax", float("inf")):
        return "over the tax line"
    if payroll >= cap_lines.get("salary_cap", float("inf")):
        return "over the cap"
    return "under the cap"


def _pct_of(vals, value):
    """Percentile of value within a list of numbers, same formula
    as team_needs.py: 100 * (below + 0.5 * equal) / n."""
    below = sum(1 for x in vals if x < value)
    equal = sum(1 for x in vals if x == value)
    return 100.0 * (below + 0.5 * equal) / len(vals) if vals else 50.0


def _ord(n):
    """1st, 2nd, 3rd, 4th... for a percentile value."""
    n = int(round(n))
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


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
    # Tier keyed by (athlete_id, team): a player who changed teams
    # has one profile per team with a different tier (Carson Cooper:
    # Rotation / depth on the Lakers, Overvalued / avoid on Memphis).
    # Keying by athlete_id alone lets one profile overwrite the other.
    tier_by_id = {(r["athlete_id"], r["team"]): r["tier"] for r in gm}
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
            tier_by_id.get((p["athlete_id"], p["team"]), ""),
            player_slug(p["name"], p["athlete_id"], p["team"]),
        ] for p in lst]
    return out


def load_player_detail():
    """Player detail for expandable rows, keyed by name|team.

    Merges the eval CSV (ORtg_full, TS%, RPR components, trend)
    with the base CSV (shrunk/adjusted ORtg, OppAdj). Every value
    is a display string or None; the JS renders it, it never
    recomputes a rating. GP and the shrinkage applied (ORtg
    simple -> shrunk -> adjusted) are in here so a reader can see
    how much of a player's number is shrinkage.
    """
    detail = {}
    base = {(r["athlete_id"], r["team"]): r
            for r in read_csv(DATA / "players_2026_27.csv")}
    for r in read_csv(DATA / "players_2026_27_eval.csv"):
        b = base.get((r["athlete_id"], r["team"]), {})
        detail[f"{r['name']}|{r['team']}"] = {
            "name": r["name"], "team": r["team"],
            "position": r.get("position", ""),
            "rpr": r["rpr"], "rpr_v1_1": r.get("rpr_v1_1", ""),
            "scoring": r["rpr_scoring"], "playmaking": r["rpr_playmaking"],
            "defense": r["rpr_defense"], "rebounding": r["rpr_rebounding"],
            "ortg_full": r["ortg_full"], "ortg_simple": r["ortg_simple"],
            "ortg_shrunk": b.get("ortg_shrunk", ""),
            "ortg_adj": b.get("ortg_adj", ""),
            "opp_adj": b.get("opp_adj", ""),
            "ts_pct": r["ts_pct"], "fg3_pct": r["fg3_pct"],
            "fg3_pct_shrunk": b.get("fg3_pct_shrunk", ""),
            "gp": r["gp"], "min_pct": r["min_pct"], "mpg": r["mpg"],
            "poss_pct": r["poss_pct"], "trend_ppg": r["trend_ppg"],
            "last7_gp": r["last7_gp"],
        }
    return detail


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
    out.append("<div class=\"table-scroll\"><table class=\"ratings\" id=\"teams-2026_27\">"
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
            f"<td data-v=\"{r['w'] / (r['w'] + r['l']) if r['w'] + r['l'] else 0:.4f}\">{r['w']}-{r['l']}</td>"
            f"{_metric_cell(r['adj_o'], r['adj_o'], ranks['adj_o'].get(r['team']))}"
            f"{_metric_cell(r['adj_d'], r['adj_d'], ranks['adj_d'].get(r['team']))}"
            f"<td class=\"net\" data-v=\"{r['net']}\">{r['net']}</td>"
            f"{_metric_cell(r['adj_t'], r['adj_t'], ranks['adj_t'].get(r['team']))}"
            "</tr>")
    out.append("</tbody></table></div>")
    out.append(
        f"<p class=\"note\">Full table above ({len(rows)} teams). "
        "Download the team ratings: "
        "<a href=\"data/teams_2026_27.json\">teams_2026_27.json</a>. "
        "W-L is the record in the games these ratings are built from."
        "</p>")
    return "\n".join(out)


def _leader_table(title, players, key, extra_key, extra_label):
    """One top-5 leader table. key/extra_key are player CSV fields.

    Four columns only: player, team, the metric, and one extra
    (RPR, %Poss, or ORtg). The old fifth PPG column duplicated
    the metric column on the PTS table and added nothing.
    """
    top = sorted(players, key=lambda p: float(p[key] or 0),
                 reverse=True)[:5]
    out = [f"<div><h3>{title}</h3><table><thead><tr>"
           f"<th>Player</th><th>Team</th><th>{title.split(' by ')[-1]}</th>"
           f"<th>{extra_label}</th></tr></thead><tbody>"]
    for p in top:
        out.append(f"<tr><td><a href=\"players/"
                   f"{player_slug(p['name'], p['athlete_id'], p['team'])}"
                   f".html\">{html.escape(p['name'])}</a></td>"
                   f"<td>{html.escape(p['team'])}</td>"
                   f"<td>{p[key]}</td><td>{p[extra_key]}</td>"
                   f"</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def player_leaders(players, min_games=2):
    """Top 5 by PTS, REB, AST, ORtg (full), and RPR.

    Same floor as RonPom's 2026-27 leaders: min 2 games and 40%
    of team minutes, so a two-minute cameo cannot top the list.
    The ORtg leader table uses ORtg_full (Oliver, with assist
    credit), not the raw simplified ORtg: the old table crowned
    a low-usage shooter on a raw 135.7. Players without an
    ORtg_full fall back to their shrunk simple ORtg.
    """
    qualified = [p for p in players
                 if int(p["gp"]) >= min_games and p["rpr"]
                 and p["min_pct"] and float(p["min_pct"]) >= 40]
    for p in qualified:
        p["ortg_full"] = p.get("ortg_full") or p.get("ortg_shrunk") \
            or p.get("ortg")
    out = ["<div class=\"leaders\">"]
    out.append(_leader_table("Top players by PTS", qualified,
                             "pts_pg", "rpr", "RPR"))
    out.append(_leader_table("Top players by REB", qualified,
                             "reb_pg", "rpr", "RPR"))
    out.append(_leader_table("Top players by AST", qualified,
                             "ast_pg", "rpr", "RPR"))
    out.append(_leader_table("Top players by ORtg", qualified,
                             "ortg_full", "poss_pct", "%Poss"))
    out.append(_leader_table("Top players by RPR", qualified,
                             "rpr", "ortg_full", "ORtg (full)"))
    out.append("</div>")
    out.append(
        f"<p class=\"note\">Min {min_games} games and 40% of team minutes. "
        "ORtg here is ORtg (full), the Oliver rating with assist credit; "
        "read it with %Poss. Full profiles in "
        "<a href=\"data/players_2026_27.csv\">players_2026_27.csv</a>.</p>")
    return "\n".join(out)


def shooters_section():
    """Top 25 shooters by SHARP in sharp_rating.py.

    Reads data/sharp_2026_27.json if it exists, otherwise
    returns an empty string so a build without the SHARP
    step still works. SHARP is points per game added by
    shooting above league average, from threes and free throws
    only, so finishers do not top it the way they top TS%.
    """
    path = DATA / "sharp_2026_27.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text())
    top = data["players"][:25]
    league = data["league"]
    out = ["<section id=\"shooters\"><h2>Best shooters by SHARP</h2>",
           "<p>Top 25 shooters of 2026-27 by SHARP: points "
           "per game added by shooting above league average, from "
           "threes and free throws only. SHARP is not a percent, "
           "it is extra points per game. "
           f"League 3P {league['fg3_pct'] * 100:.1f}%, FT "
           f"{league['ft_pct'] * 100:.1f}%. 3P% is shrunk toward "
           "league average (m = 100 attempts), FT% the same "
           "(m = 50). Floor is 5 games and 20 minutes per game, "
           "not the 40% minutes floor, so Curry qualifies. "
           "TS% is shown for context only, it is not part of the "
           "score, because TS% mixes dunks and layups in with jump "
           "shots. Full list in "
           "<a href=\"data/sharp_2026_27.csv\">sharp_2026_27.csv</a>.</p>",
           "<div class=\"table-scroll\"><table class=\"ratings\" "
           "id=\"shooters-top25\" data-renumber=\"1\"><thead><tr>"
           "<th data-t=\"num\">Rk</th><th data-t=\"text\">Player</th>"
           "<th data-t=\"text\">Team</th><th data-t=\"num\">SHARP</th>"
           "<th data-t=\"num\">3P%</th><th data-t=\"num\">3PA/g</th>"
           "<th data-t=\"num\">FT%</th><th data-t=\"num\">TS%</th>"
           "</tr></thead><tbody>"]
    for p in top:
        fg3 = f"{p['fg3_pct'] * 100:.1f}%" if p.get("fg3_pct") is not None else "-"
        ft = f"{p['ft_pct'] * 100:.1f}%" if p.get("ft_pct") is not None else "-"
        ts = f"{p['ts_pct'] * 100:.1f}%" if p.get("ts_pct") is not None else "-"
        out.append(f"<tr><td>{p['rank']}</td>"
                   f"<td><a href=\"players/"
                   f"{player_slug(p['name'], p['athlete_id'], p['team'])}"
                   f".html\">{html.escape(p['name'])}</a></td>"
                   f"<td>{html.escape(p['team'])}</td>"
                   f"<td>{p['sharp']:+.2f}</td>"
                   f"<td>{fg3}</td><td>{p['fg3a_pg']:.1f}</td>"
                   f"<td>{ft}</td><td>{ts}</td></tr>")
    out.append("</tbody></table></div></section>")
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
    for p in top:
        p["ortg_full"] = p.get("ortg_full") or p.get("ortg_shrunk") \
            or p.get("ortg")

    def num(v, disp):
        return (f"<td data-v=\"{v}\">{disp}</td>" if v not in (None, "")
                else "<td data-v=\"0\">-</td>")

    out = ["<section id=\"top-players\"><h2>Top players</h2>",
           "<p>Top 100 players of 2026-27 by RPR. Minimum 2 games "
           "and 40% of team minutes, same as the leader tables. "
           "Click a column header to sort (the rank renumbers to "
           "the sorted order); the filter box searches "
           "player names. Click a team name to open that team's "
           "own page, click a player name to open that player's "
           "full profile (season line, shooting, RPR breakdown, "
           "game log, and NBA ranks), or click a player row to see "
           "that player's RPR components, ORtg (full) vs simple, "
           "shooting, and trend. The Rookies only button filters "
           "this table "
           "to first-year players (experience.years == 0).</p>",
           "<input class=\"filter\" type=\"search\" "
           "placeholder=\"Filter players...\" data-table=\"top-players\"> "
           "<button class=\"conf-tab rookie-toggle\" "
           "data-table=\"top-players\">Rookies only</button>",
           "<div class=\"table-scroll\">"
           "<table class=\"ratings\" id=\"top-players\" data-renumber=\"1\">"
           "<thead><tr>"
           "<th data-t=\"num\">Rk</th>"
           "<th data-t=\"text\">Player</th>"
           "<th data-t=\"text\">Team</th>"
           "<th data-t=\"text\">Pos</th>"
           "<th data-t=\"num\">GP</th>"
           "<th data-t=\"num\">PPG</th>"
           "<th data-t=\"num\">ORtg (full)</th>"
           "<th data-t=\"num\">%Poss</th>"
           "<th data-t=\"num\">%Min</th>"
           "<th data-t=\"num\">RPR</th>"
           "</tr></thead><tbody>"]
    for rank, p in enumerate(top, 1):
        pos = p["position"] if p["position"] not in ("", "NA") else ""
        rookie = 1 if p.get("is_rookie") == "1" else 0
        out.append(
            f"<tr class=\"player\" data-rookie=\"{rookie}\" "
            f"data-pid=\"{html.escape(p['name'])}|{html.escape(p['team'])}\">"
            f"<td data-v=\"{rank}\">{rank}</td>"
            f"<td data-v=\"{html.escape(p['name'].lower())}\">"
            f"<a href=\"players/{player_slug(p['name'], p['athlete_id'], p['team'])}.html\">"
            f"{html.escape(p['name'])}</a></td>"
            f"<td data-v=\"{html.escape(p['team'].lower())}\">"
            f"<a href=\"teams/{team_slug(p['team'])}.html\">"
            f"{html.escape(p['team'])}</a></td>"
            f"<td data-v=\"{html.escape(pos.lower())}\">"
            f"{html.escape(pos) if pos else '-'}</td>"
            f"<td data-v=\"{p['gp']}\">{p['gp']}</td>"
            f"{num(p['pts_pg'], p['pts_pg'])}"
            f"{num(p['ortg_full'], p['ortg_full'])}"
            f"{num(p['poss_pct'], p['poss_pct'])}"
            f"{num(p['min_pct'], p['min_pct'])}"
            f"{num(p['rpr'], p['rpr'])}"
            f"</tr>")
    out.append("</tbody></table></div>")
    out.append(
        "<p class=\"note\">ORtg (full) is the Oliver rating with assist "
        "credit; the simple ORtg (no assist credit) is in the detail "
        "row and the CSVs. Read ORtg with %Poss. RPR is v1.2, our own "
        "single-number rating, not KenPom's proprietary kPOY. Full "
        "profiles in "
        "<a href=\"data/players_2026_27.csv\">players_2026_27.csv</a>."
        "</p></section>")
    return "\n".join(out)


def _rookie_table(table_id, title, players):
    """A sortable RPR table for a rookie list (same columns as top 100)."""
    for p in players:
        p["ortg_full"] = p.get("ortg_full") or p.get("ortg_shrunk") \
            or p.get("ortg")

    def num(v, disp):
        return (f"<td data-v=\"{v}\">{disp}</td>" if v not in (None, "")
                else "<td data-v=\"0\">-</td>")
    out = [f"<h3>{title}</h3>",
           "<div class=\"table-scroll\">"
           "<table class=\"ratings\" id=\"" + table_id + "\" data-renumber=\"1\">"
           "<thead><tr>"
           "<th data-t=\"num\">Rk</th>"
           "<th data-t=\"text\">Player</th>"
           "<th data-t=\"text\">Team</th>"
           "<th data-t=\"text\">Pos</th>"
           "<th data-t=\"num\">GP</th>"
           "<th data-t=\"num\">PPG</th>"
           "<th data-t=\"num\">ORtg (full)</th>"
           "<th data-t=\"num\">%Poss</th>"
           "<th data-t=\"num\">%Min</th>"
           "<th data-t=\"num\">RPR</th>"
           "</tr></thead><tbody>"]
    for rank, p in enumerate(players, 1):
        pos = p["position"] if p["position"] not in ("", "NA") else ""
        out.append(
            f"<tr class=\"player\" "
            f"data-pid=\"{html.escape(p['name'])}|{html.escape(p['team'])}\">"
            f"<td data-v=\"{rank}\">{rank}</td>"
            f"<td data-v=\"{html.escape(p['name'].lower())}\">"
            f"<a href=\"players/{player_slug(p['name'], p['athlete_id'], p['team'])}.html\">"
            f"{html.escape(p['name'])}</a></td>"
            f"<td data-v=\"{html.escape(p['team'].lower())}\">"
            f"<a href=\"teams/{team_slug(p['team'])}.html\">"
            f"{html.escape(p['team'])}</a></td>"
            f"<td data-v=\"{html.escape(pos.lower())}\">"
            f"{html.escape(pos) if pos else '-'}</td>"
            f"<td data-v=\"{p['gp']}\">{p['gp']}</td>"
            f"{num(p['pts_pg'], p['pts_pg'])}"
            f"{num(p['ortg_full'], p['ortg_full'])}"
            f"{num(p['poss_pct'], p['poss_pct'])}"
            f"{num(p['min_pct'], p['min_pct'])}"
            f"{num(p['rpr'], p['rpr'])}"
            f"</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def rookies_section(players):
    """Rookies-only view: top 10 rookies by RPR, plus below-floor ones.

    A rookie is experience.years == 0 on the ESPN roster (first
    NBA season), matched by athlete_id. The top 10 uses the same
    floor as every other leaderboard (min 2 games, 40% of team
    minutes). Rookies below the floor are shown separately and
    labelled, because the raw RPR leader among rookies is often
    a small-sample cameo: a player on 5 games at 9% of minutes
    is not the best rookie, he is the noisiest one.
    """
    rookies = [p for p in players if p.get("is_rookie") == "1"]
    distinct = {p["athlete_id"] for p in rookies}
    qualified = [p for p in rookies
                 if int(p["gp"]) >= 2 and p["rpr"]
                 and p["min_pct"] and float(p["min_pct"]) >= 40]
    qualified.sort(key=lambda p: float(p["rpr"]), reverse=True)
    top = qualified[:10]
    below = [p for p in rookies
             if int(p["gp"]) >= 2 and p["rpr"]
             and p["min_pct"] and float(p["min_pct"]) < 40]
    below.sort(key=lambda p: float(p["rpr"]), reverse=True)
    below = below[:8]
    best = top[0] if top else None
    out = ["<section id=\"rookies\"><h2>Rookies</h2>"]
    if best:
        out.append(
            f"<p><strong>Best rookie by RPR: "
            f"{html.escape(best['name'])} "
            f"({html.escape(best['team'])}, RPR {best['rpr']}, "
            f"{best['pts_pg']} PPG in {best['gp']} games).</strong> "
            f"{len(rookies)} rookie profiles have played this season "
            f"({len(distinct)} distinct rookies; Carson Cooper has "
            f"one profile per team after changing teams). "
            f"Rookies are first-year players: their ESPN roster "
            f"entry shows experience.years == 0. The 2025 draft "
            f"class (Cooper Flagg, Cedric Coward) are second-year "
            f"players in 2026-27 and are not rookies here.</p>")
    out.append(_rookie_table("rookies-top10",
                             "Top 10 rookies by RPR", top))
    out.append(
        "<p class=\"note\">Same floor as the other leaderboards: "
        "min 2 games and 40% of team minutes. Click a column "
        "header to sort; click a team name for that team's page.</p>")
    if below:
        out.append(_rookie_table(
            "rookies-below-floor",
            "Below the minutes floor (notable rookies, small samples)",
            below))
        out.append(
            "<p class=\"note\">These rookies are below the 40% "
            "minutes floor, so they do not qualify for the top 10 "
            "above. Their RPRs are noisier: few games and few "
            "minutes move the number a lot. Read them as names to "
            "watch, not as a ranking.</p>")
    out.append("</section>")
    return "\n".join(out)


def gm_board_section():
    """GM acquisition board tiers, from data/gm_board_2026_27.csv."""
    gm = read_csv(DATA / "gm_board_2026_27.csv")
    tiers = {}
    for row in gm:
        tiers.setdefault(row["tier"], []).append(row)
    tier_order = ["Franchise cornerstone", "All-Star level",
                  "High-value starter", "Undervalued / buy-low",
                  "Elite role player", "Overvalued / avoid",
                  "Rotation / depth", "Insufficient minutes"]
    out = ["<section id=\"gm-board\"><h2>GM acquisition board</h2>",
           "<p>Tiers are set by thresholds on RPR v1.2, efficiency at "
           "usage, and minutes per game when the player plays, not "
           "by reputation and not by %Min (a star who missed games "
           "looks like a bench player on %Min). Players with under "
           "8 games or under 150 total minutes are not tiered: they "
           "sit in Insufficient minutes. No contracts or salaries. "
           "Age, height (inches), and weight (lbs) come from the "
           "roster pull and are in the CSVs. "
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
    # Diversity disclosure (issue 9): the same best available role
    # players top many teams' lists. Say so, with the numbers, so
    # it does not look like a bug. A cap on how many teams one
    # player can top was considered and rejected: it would hide
    # the true best fit for the later teams and break the ranking.
    _all_top = [s for p in teams.values()
                for s in p["targets"]["top_overall"]]
    _distinct = {s["name"] for s in _all_top}
    _common = ", ".join(
        f"{n} ({c})" for n, c in Counter(
            s["name"] for s in _all_top).most_common(4))
    _scores = [s["trade_score"] for s in _all_top]
    _score_max = max(_scores) if _scores else 0.0
    _score_min = min(_scores) if _scores else 0.0
    _qs = [s["quality_gain"] for s in _all_top]
    _fs = [s["fit_gain"] for s in _all_top]
    _q_mean = sum(_qs) / len(_qs) if _qs else 0.0
    _f_mean = sum(_fs) / len(_fs) if _fs else 0.0
    _driver = ("QualityGain" if _q_mean >= _f_mean else "FitGain")
    out = ["<section id=\"team-needs\"><h2>Team needs and trade fit</h2>",
           "<p>Each team's strengths and weaknesses, its star, and the "
           "realistic players who would fill its biggest holes. "
           "<strong>Realistic</strong> is the point: targets are only "
           "Rotation / depth, Elite role player, Undervalued / buy-low, "
           "or High-value starter players, never a top-3 player on "
           "their own team, never a 20-point scorer or a 30-minute-a-"
           "night player on their own team. Salaries are shown with "
           "the fit on each team page (Basketball-Reference "
           "contracts, display only), but they do not change the "
           "score. This is a box-score "
           "fit, a prior for lineup data, not a lineup prediction. "
           "TradeScore is QualityGain plus FitGain minus the "
           "availability discount, on this run from "
           f"{_score_min:.2f} to {_score_max:.2f}, "
           f"with QualityGain averaging {_q_mean:.2f} and FitGain "
           f"{_f_mean:.2f}, so {_driver} drives the score more here; "
           "it is a fit ranking, "
           "not a prediction and not a pick. Each team page also "
           "lists less-common alternatives: its best targets who do "
           "not top many other teams' lists. "
           "Full per-team lists in "
           "<a href=\"data/team_needs_2026_27.csv\">"
           "team_needs_2026_27.csv</a>.</p>",
           f"<p class=\"note\">Only {len(_distinct)} distinct players "
           f"fill the {len(_all_top)} top-overall slots across the 30 "
           f"teams: the same best available role players top many "
           f"teams' lists (most often {_common}). That is expected, "
           f"the best available role players are the best fit for "
           f"every needy team, but it means the lists read as nearly "
           f"the same list. Salaries help tell them apart on the team "
           f"pages: the same fit at $2.85M is a different suggestion "
           f"than the same fit at $19.55M.</p>",
           f"<p class=\"warn\">{html.escape(DISCLAIMER)}</p>",
           "<div class=\"table-scroll\">"
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
        _star = prof["star"] or {}
        # A star under RPR 1.5 is a role player on a rebuilding
        # team, not a star. Fall back to the RPR check for a team_needs
        # file built before is_clear_star existed.
        _clear = _star.get("is_clear_star",
                           (_star.get("rpr") or 0) >= 1.5)
        star = _star.get("name", "") if _clear else "No clear star"
        need = prof["ranked_needs"][0] if prof["ranked_needs"] else ""
        top = (prof["targets"]["top_overall"][0]
               if prof["targets"]["top_overall"] else None)
        if top:
            _sal = _money_m(top.get("target_salary"))
            _net = _signed_money_m(top.get("net_salary_change"))
            target = (f"{html.escape(top['name'])} "
                      f"({html.escape(top['team'])}, "
                      f"{html.escape(top['tier'])}, fills "
                      f"{html.escape(top['fills_need'])}, "
                      f"Q {top['quality_gain']:+.3f} "
                      f"F {top['fit_gain']:+.3f}, "
                      f"{_sal}, net {_net})")
            score = f"{top['trade_score']:.3f}"
        else:
            target, score = "-", "-"
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
    out.append("</tbody></table></div>")
    unfilled = [(t, k, v) for t, p in teams.items()
                for k, v in p["targets"].get("unfilled_needs", {}).items()]
    if unfilled:
        out.append("<h3>No realistic target for a need</h3>")
        for team, need, reason in unfilled:
            out.append(f"<p><strong>{html.escape(team)}</strong> - "
                       f"{html.escape(need)}: {html.escape(reason)}</p>")
    out.append("</section>")
    return "\n".join(out)


def _rank_str(team, key):
    """' (#n)' for a team's rank in one metric, or ''."""
    r = RANKS.get(key, {}).get(team)
    return f" (#{r})" if r is not None else ""


def _roster_table(roster, team=""):
    """Roster HTML table, shared by team pages and (via JS) the
    inline detail panels' data. Columns: Player, Pos, GP, PPG,
    ORtg, %Poss, %Min, RPR, Tier. Rows carry data-pid so the
    team page script can expand a player's detail row."""
    parts = ['<table class="roster"><thead><tr><th>Player</th>'
             "<th>Pos</th><th>GP</th><th>PPG</th><th>ORtg</th>"
             "<th>%Poss</th><th>%Min</th><th>RPR</th><th>Tier</th>"
             "</tr></thead><tbody>"]
    for p in roster:
        pos = p[1] if p[1] not in ("", "NA", None) else "-"
        # p[9] is the player-profile slug (see rosters()); team
        # pages live in teams/, so the profile link is ../players/.
        _slug = p[9] if len(p) > 9 else ""
        _name = (f"<a href=\"../players/{_slug}.html\">"
                 f"{html.escape(p[0])}</a>" if _slug
                 else html.escape(p[0]))
        cells = [_name, pos, str(p[2])]
        for v in p[3:8]:
            cells.append("-" if v is None else f"{v:.1f}")
        cells.append(html.escape(p[8]) if p[8] else "-")
        pid = (f"{html.escape(p[0])}|{html.escape(team)}"
               if team else "")
        parts.append(f"<tr class=\"player\" data-pid=\"{pid}\">"
                     + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


# Rotation skill profile chart (team pages). Labels and per-skill
# definitions match team_needs.py skill_rates exactly: shooting is
# 3PM per 36, playmaking AST per 36, rebounding REB per 36, rim
# protection BLK per 36, perimeter defence STL per 36, bench
# scoring PTS per 36 for players under 60% of team minutes.
# Values are the minutes-weighted mean of per-player percentiles
# for the top 9 rotation players, from data/team_needs_2026_27.json
# (team["skills"]). Bars at or above the 60th percentile target
# are blue; below it, orange: those are the holes.
SKILL_LABELS = [
    ("shooting", "Shooting", "3PM per 36"),
    ("playmaking", "Playmaking", "AST per 36"),
    ("rebounding", "Rebounding", "REB per 36"),
    ("rim_protection", "Rim protection", "BLK per 36"),
    ("perimeter_defense", "Perimeter defence",
     "STL per 36 plus half-weight BLK per 36"),
    ("bench_scoring", "Bench scoring",
     "PTS per 36, players under 60% of team minutes"),
]
SKILL_TARGET_PCT = 60


def skill_chart_html(skills, raw=None):
    """Rotation skill profile chart: horizontal percentile bars.

    skills is the team's dict from team_needs (team["skills"]).
    raw is team["skill_raw"]: the minutes-weighted per-36 rate
    behind each percentile, shown in the row title and listed
    under the chart, so a percentile is never presented without
    the rate it came from. Returns "" when the data is missing,
    so a team page never breaks on a missing team_needs run.
    """
    if not skills:
        return ""
    rows = []
    raw_lines = []
    for key, label, definition in SKILL_LABELS:
        value = skills.get(key)
        if value is None:
            continue
        pct = int(round(value))
        cls = "above" if pct >= SKILL_TARGET_PCT else "below"
        raw_v = (raw or {}).get(key)
        title = definition
        if raw_v is not None:
            title = f"{definition}; raw rate {raw_v:.2f} per 36"
            _suf = ("th" if 11 <= pct % 100 <= 13 else
                    {1: "st", 2: "nd", 3: "rd"}.get(pct % 10, "th"))
            raw_lines.append(f"{label}: {raw_v:.2f} per 36 "
                             f"({pct}{_suf} percentile)")
        rows.append(
            f'<div class="skill-row" title="{html.escape(title)}">'
            f'<span class="skill-label">{html.escape(label)}</span>'
            f'<span class="skill-track"><span class="skill-fill {cls}" '
            f'style="width:{pct}%"></span>'
            f'<span class="skill-target"></span></span>'
            f'<span class="skill-value">{pct}</span></div>')
    if not rows:
        return ""
    defs = "; ".join(f"{label}: {definition}"
                     for _, label, definition in SKILL_LABELS)
    raw_note = ""
    if raw_lines:
        raw_note = ('<p class="note">Raw rates behind the bars: '
                    + html.escape("; ".join(raw_lines)) + ".</p>")
    return (
        '<h4>Rotation skill profile (percentile, minutes-weighted)</h4>'
        '<p class="note">Top 9 players by minutes. Each skill is '
        'percentile-ranked against every player in the league with '
        '150+ minutes (bench scoring is ranked bench-only, and the '
        'team bench skill is bench-only), then minutes-weighted. '
        'The grey line is the '
        '60th percentile target: orange bars below it are the holes. '
        f'{html.escape(defs)}.</p>'
        '<div class="skill-chart">' + "".join(rows) + '</div>'
        + raw_note)


# Vs league average, team level, in the player profile visual
# language (build_player_pages.py): at-a-glance cards with a rank
# and a rank bar, and diverging bars centered at league average.
# Blue is above average, orange is below, the grey center line is
# league average, and the dark tick on the skill rows is the 60th
# percentile target from the skill chart. The value shown is the
# percentile (AdjO/AdjD/Net/AdjT use rank percentiles across the
# 30 teams), the raw value sits in the row title and the note
# underneath, so a percentile is never presented without the
# number it came from. Strengths in team_needs.py are pct >= 65
# and weaknesses pct <= 35, so a strength always lands on the
# blue side here and a weakness on the orange side.
FF_LABELS = [
    ("efg_pct", "Shooting (eFG%)", "Team effective field goal percentage"),
    ("tov_pct", "Ball security (TOV%)", "Team turnovers per possession"),
    ("oreb_pct", "Offensive rebounding (OREB%)", "Share of available offensive rebounds the team grabs"),
    ("ft_rate", "Getting to the line (FT rate)", "Free throw attempts per field goal attempt"),
    ("opp_efg_pct", "Opponent shooting (opp eFG%)", "Effective field goal percentage the team allows"),
    ("opp_tov_pct", "Forcing turnovers (opp TOV%)", "Opponent turnovers per possession"),
    ("dreb_pct", "Defensive rebounding (DREB%)", "Share of available defensive rebounds the team grabs"),
    ("opp_ft_rate", "Opponent FT rate", "Opponent free throw attempts per field goal attempt"),
]


def _comp_row(label, pct, raw_text, target60=False, tip=""):
    """One diverging bar row: pct is a percentile, 50 is average.

    Bar width is |pct - 50| on a 0 to 50 scale, so a 50th
    percentile team has no bar at all, which is the point.
    """
    pct = max(0.0, min(100.0, float(pct)))
    width = abs(pct - 50.0)
    cls = "pos" if pct >= 50 else "neg"
    target = ('<span class="comp-target" title="60th percentile target"></span>'
              if target60 else "")
    label_html = html.escape(label)
    if tip:
        label_html = (f'<span class="tip" tabindex="0" data-tip="'
                      f'{html.escape(tip, quote=True)}">{label_html}</span>')
    return (
        f'<div class="comp-row" title="{html.escape(raw_text)}">'
        f'<span class="comp-label">{label_html}</span>'
        f'<span class="comp-track"><span class="comp-center"></span>{target}'
        f'<span class="comp-fill {cls}" style="width:{width:.1f}%"></span></span>'
        f'<span class="comp-value">{_ord(pct)}</span></div>')


def _glance_card(label, value, rank, denom=30, nd=1):
    """At-a-glance card, player profile style: big value, rank,
    rank bar. Rank 1 fills the bar, rank 30 leaves it near empty."""
    if value is None or rank is None:
        return (f'<div class="stat-card"><div class="label">{html.escape(label)}</div>'
                f'<div class="value">-</div>'
                f'<div class="rank">No value in this data</div></div>')
    pct_top = 100 * (1 - (rank - 1) / denom) if denom else 0
    return (
        f'<div class="stat-card"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{value:.{nd}f}</div>'
        f'<div class="rank">#{rank} of {denom} in the NBA</div>'
        f'<div class="rank-bar"><span style="width:{pct_top:.0f}%"></span></div></div>')


def vs_average_html(needs, row):
    """At-a-glance cards plus the vs league average diverging bars.

    Groups: team ratings (AdjO, AdjD, Net, AdjT as rank
    percentiles), the 6 rotation skills, and the 8 Four Factors
    as percentiles. Returns "" when the team_needs data is
    missing, so a team page never breaks on a missing run.
    """
    if not needs:
        return ""
    team = row["team"]
    cards = [
        _glance_card("AdjO", row["adj_o"], RANKS.get("adj_o", {}).get(team)),
        _glance_card("AdjD", row["adj_d"], RANKS.get("adj_d", {}).get(team)),
        _glance_card("Net", row["net"], row["rank"]),
        _glance_card("AdjT", row["adj_t"], RANKS.get("adj_t", {}).get(team)),
    ]
    parts = ["<h4>At a glance</h4>",
             '<div class="stat-grid">' + "".join(cards) + "</div>",
             '<p class="note">AdjO and AdjD are points per 100 '
             "possessions, adjusted for opponent. Net is AdjO minus "
             "AdjD, AdjT is adjusted tempo. The rank is out of 30 "
             "teams; the bar shows how close the rank is to the top "
             "(100% is best in the league). AdjD rank 1 is the best "
             "defense.</p>",
             "<h4>Vs league average</h4>",
             '<p class="note">Every bar is a percentile, centered at '
             "the 50th percentile, league average. Blue bars to the "
             "right are above average, orange bars to the left are "
             "below average, and a team exactly at average has no "
             "bar. The dark tick on the skill rows is the 60th "
             "percentile target from the skill chart below. Hover or "
             "tap a row for the raw number behind the percentile.</p>"]
    pcts = TEAM_PCT.get(team, {})
    groups = [("Team ratings", [
        ("AdjO", pcts.get("adj_o"), f"AdjO {row['adj_o']:.1f}"),
        ("AdjD", pcts.get("adj_d"), f"AdjD {row['adj_d']:.1f}"),
        ("Net", pcts.get("net"), f"Net {row['net']:.1f}"),
        ("AdjT", pcts.get("adj_t"), f"AdjT {row['adj_t']:.1f}"),
    ])]
    skills = needs.get("skills") or {}
    raw = needs.get("skill_raw") or {}
    skill_rows = []
    for key, label, definition in SKILL_LABELS:
        if skills.get(key) is None:
            continue
        raw_v = raw.get(key)
        raw_s = (f"{label}: {raw_v:.2f} per 36, {skills[key]:.0f}th percentile"
                 if raw_v is not None else f"{label}: {skills[key]:.0f}th percentile")
        skill_rows.append((label, skills[key], raw_s, definition))
    groups.append(("Rotation skills", skill_rows))
    ff = needs.get("four_factor_pct") or {}
    ff_raw = needs.get("four_factors") or {}
    ff_rows = []
    for key, label, tip in FF_LABELS:
        if ff.get(key) is None:
            continue
        raw_v = ff_raw.get(key)
        raw_s = (f"{label}: {raw_v * 100:.1f}%, {ff[key]:.0f}th percentile"
                 if raw_v is not None else f"{label}: {ff[key]:.0f}th percentile")
        ff_rows.append((label, ff[key], raw_s, tip))
    groups.append(("Four Factors", ff_rows))
    rows_html = []
    raw_lines = []
    for gname, grows in groups:
        if not grows:
            continue
        rows_html.append(f'<div class="comp-group">{html.escape(gname)}</div>')
        for item in grows:
            label, pct, raw_s = item[0], item[1], item[2]
            tip = item[3] if len(item) > 3 else ""
            target60 = (gname == "Rotation skills")
            if pct is None:
                continue
            rows_html.append(_comp_row(label, pct, raw_s, target60, tip))
            raw_lines.append(raw_s)
    if not rows_html:
        return ""
    parts.append('<div class="comp-chart">' + "".join(rows_html) + "</div>")
    if raw_lines:
        parts.append('<p class="note">Raw numbers behind the bars: '
                     + html.escape("; ".join(raw_lines)) + ".</p>")
    return "\n".join(parts)


def _target_link(name, team):
    """Link a player name to ../players/<slug>.html, or plain text
    when no profile slug is known for that name and team."""
    slug = SLUG_BY_NAME_TEAM.get((name, team))
    if slug:
        return (f'<a href="../players/{slug}.html">'
                f"{html.escape(name)}</a>")
    return html.escape(name)


def target_table_html(targets, team):
    """A target table: fit and money in the same row.

    Columns: player, fills, Q, F, TradeScore, displaces, target
    salary, net change, payroll after. The displaced player is
    on the acquiring team, so his link uses that team.
    """
    if not targets:
        return ""
    rows = []
    for s in targets:
        disp = s["displaced"]
        disp_link = _target_link(disp["name"], team)
        pay_after = s.get("payroll_after")
        pay_line = _payroll_line(pay_after, CAP_LINES)
        pay_s = _money_m(pay_after)
        if pay_after is not None and pay_line:
            pay_s += f'<span class="sub">{html.escape(pay_line)}</span>'
        rows.append(
            "<tr>"
            f"<td>{_target_link(s['name'], s['team'])}"
            f"<span class=\"sub\">{html.escape(s['team'])}, "
            f"{html.escape(s['tier'])}, RPR {s['rpr']:.2f}</span></td>"
            f"<td>{html.escape(s['fills_need'])}</td>"
            f"<td class=\"num\">{s['quality_gain']:+.3f}</td>"
            f"<td class=\"num\">{s['fit_gain']:+.3f}</td>"
            f"<td class=\"num\">{s['trade_score']:.3f}</td>"
            f"<td>{disp_link}"
            f"<span class=\"sub\">RPR {disp['rpr']:.2f}</span></td>"
            f"<td class=\"num\">{_money_m(s.get('target_salary'))}"
            f"<span class=\"sub\">was {_money_m(s.get('displaced_salary'))}</span></td>"
            f"<td class=\"num\">{_signed_money_m(s.get('net_salary_change'))}</td>"
            f"<td class=\"num\">{pay_s}</td>"
            "</tr>")
    return (
        '<div class="table-scroll"><table class="targets"><thead><tr>'
        "<th>Player</th><th>Fills</th><th>Q</th><th>F</th>"
        "<th>TradeScore</th><th>Displaces</th><th>Target salary</th>"
        "<th>Net change</th><th>Payroll after</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _trade_means():
    """Mean QualityGain and FitGain across every top-overall
    target on this run, for the TradeScore explanation. Computed
    from the data, never hardcoded, so the sentence stays true
    when the run changes."""
    qs, fs = [], []
    for prof in NEEDS.values():
        for s in prof.get("targets", {}).get("top_overall", []):
            qs.append(s["quality_gain"])
            fs.append(s["fit_gain"])
    if not qs:
        return None, None
    return sum(qs) / len(qs), sum(fs) / len(fs)


def team_page_html(team, row, slug_by_team):
    """One team's full page, as an HTML string."""
    conf = row["conf"]
    _through, _n_games = DATA_THROUGH
    parts = [f"<h1>{html.escape(team)}</h1>",
             f"<p class=\"subtitle\">"
             f"{html.escape(conf)}ern Conference"
             f" &middot; Data through {_through} ({_n_games} games)"
             f" &middot; <a href=\"../index.html\">&larr; NBA "
             f"Ratings</a> &middot; "
             f"<a href=\"../distributions.html\">Distributions</a> "
             f"&middot; <a href=\"../index.html#shooters\">SHARP "
             f"shooters</a></p>",
             f"<p class=\"warn\">{html.escape(DISCLAIMER)}</p>"]
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
        _clear = star.get("is_clear_star",
                          (star.get("rpr") or 0) >= 1.5)
        star_label = (star.get("name", "") if _clear
                      else "No clear star in this data")
        _star_rpr = star.get("rpr")
        _star_rpr_s = f"{_star_rpr:.2f}" if _star_rpr is not None else "-"
        parts.append("<h3>Team needs</h3>")
        parts.append(
            f"<p>Star: {html.escape(star_label)} "
            f"(RPR {_star_rpr_s}). Star here means the highest-RPR "
            f"qualifying player on the team, not the most famous "
            f"player. &middot; Top needs: "
            f"{', '.join(html.escape(n) for n in needs['ranked_needs'][:3])}</p>")
        parts.append(vs_average_html(needs, row))
        parts.append(
            f"<p><strong>Strengths</strong> (above average, blue bars "
            f"above): "
            f"{html.escape('; '.join(needs['strengths'])) or 'none flagged'}"
            f"<br><strong>Weaknesses</strong> (below average, orange "
            f"bars above): "
            f"{html.escape('; '.join(needs['weaknesses'])) or 'none flagged'}</p>")
        parts.append(skill_chart_html(needs.get("skills") or {},
                                      needs.get("skill_raw") or {}))
        # Needs as gaps: from the 60th percentile target and from
        # league average (50th). A skill at 54 is barely below
        # average, a skill at 41 is a real hole, and the old text
        # hid that difference. Almost every team sits below 60 on
        # most skills, since the average team is near 50.
        _gap_lines = []
        for k in needs["ranked_needs"][:3]:
            _nd = needs["needs"][k]
            _sk = _nd["team_skill_pct"]
            _gap_lines.append(
                f"{html.escape(k)}: {_sk:.0f}th percentile, "
                f"{max(0.0, 60 - _sk):.0f} points below the 60th "
                f"percentile target and "
                f"{abs(50 - _sk):.0f} points "
                f"{'below' if _sk < 50 else 'above'} league average")
        parts.append("<h4>Needs, ranked</h4><p>"
                     + "<br>".join(_gap_lines) + "</p>")
        # SHARP context for the shooting skill: the skill bar uses
        # made threes per 36, which is why it can disagree with
        # team shooting (eFG%). SHARP is extra points per game from
        # threes and free throws above league average.
        _sh = TEAM_SHARP.get(team)
        if _sh and _sh.get("best_name"):
            parts.append(
                f"<p>Shooting, by SHARP: this team is "
                f"{_sh['mean']:+.2f} points per game above league "
                f"average from threes and free throws, from its "
                f"{_sh['n']} qualifying shooters (best: "
                f"{_target_link(_sh['best_name'], team)} "
                f"{_sh['best_sharp']:+.2f}). SHARP counts threes and "
                f"free throws only, not layups or dunks, so it does "
                f"not reward finishers the way TS% does. "
                f"<a href=\"../index.html#shooters\">SHARP shooters</a> "
                f"&middot; <a href=\"../distributions.html\">Distributions</a></p>")
        _mq, _mf = _trade_means()
        _trade_note = (
            "TradeScore is QualityGain plus FitGain minus the "
            "availability discount. QualityGain is how much better "
            "the target is than the player he displaces, scaled by "
            "his minutes. FitGain is how much he lifts the skills "
            "this team actually needs, with diminishing returns, so "
            "the first rebounder counts more than the second. "
            + (f"On this run QualityGain averages {_mq:.2f} and "
               f"FitGain {_mf:.2f} across the top targets, so "
               f"QualityGain drives the score more than fit does "
               f"here. " if _mq is not None else "")
            + "TradeScore is a fit ranking, not a prediction and not "
            "a pick. Salaries are shown with the fit and do not "
            "change TradeScore.")
        parts.append(f"<p class=\"note\">{_trade_note}</p>")
        top = needs["targets"]["top_overall"][:3]
        if top:
            parts.append("<h4>Best realistic fits</h4>")
            parts.append(target_table_html(top, team))
            # Payroll context for the team, from the top target's
            # payroll fields (the same payroll before the swap for
            # every target on this team). The per-row payroll after
            # is in the table; this line names the lines.
            _pb = top[0].get("payroll_before")
            if _pb is not None and CAP_LINES:
                parts.append(
                    f"<p class=\"note\">Team payroll (Basketball-Reference "
                    f"team total): {_money_m(_pb)}, "
                    f"{_payroll_line(_pb, CAP_LINES)}. Cap "
                    f"{_money_m(CAP_LINES.get('salary_cap'))}, tax "
                    f"{_money_m(CAP_LINES.get('luxury_tax'))}, first "
                    f"apron {_money_m(CAP_LINES.get('first_apron'))}, "
                    f"second apron {_money_m(CAP_LINES.get('second_apron'))}. "
                    f"A salary shown here does not prove a trade is "
                    f"legal: the NBA is a soft cap and matching rules "
                    f"are complex.</p>")
        # Targets grouped by need, so the connection between a need
        # and its targets is visible. The top overall list mixes
        # needs, which is why the top target often fills a
        # different need than the top need. Thin lists are shown
        # honestly, never padded with non-fits.
        parts.append("<h4>Targets by need</h4>")
        for need in needs["ranked_needs"][:3]:
            _sel = needs["targets"].get("by_need", {}).get(need, [])
            _nd = needs["needs"][need]
            parts.append(
                f"<p><strong>{html.escape(need)}</strong> "
                f"({_nd['team_skill_pct']:.0f}th percentile, "
                f"{_nd['need']:.0f} points below the 60th percentile "
                f"target)</p>")
            if _sel:
                parts.append(target_table_html(_sel, team))
                if len(_sel) < 3:
                    parts.append(
                        f"<p class=\"note\">Only {len(_sel)} realistic "
                        f"target{'s' if len(_sel) > 1 else ''} fill{'s' if len(_sel) == 1 else ''} "
                        f"this need in this data.</p>")
            else:
                parts.append(
                    "<p class=\"note\">No realistic target fills this "
                    "need in this data.</p>")
        distinct = [s for s in needs["targets"].get("top_distinct", [])
                    if s["name"] not in {t["name"] for t in top}]
        if distinct:
            parts.append("<p>Less-common alternatives (best targets "
                         "who do not top many other teams' lists): "
                         + "; ".join(
                             f"{_target_link(s['name'], s['team'])} "
                             f"({html.escape(s['team'])}, "
                             f"{_money_m(s.get('target_salary'))}, "
                             f"TradeScore {s['trade_score']:.3f})"
                             for s in distinct) + "</p>")
        elif needs["targets"].get("top_distinct"):
            parts.append("<p>The top targets above are already "
                         "less-common fits: they do not top many other "
                         "teams' lists.</p>")
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
        parts.append(_roster_table(roster, team))
        parts.append('<p class="note">Sorted by minutes. Click a player '
                     "row to see that player's RPR components, ORtg "
                     "(full) vs simple, and shooting. ORtg in the table "
                     "is the simple approximation (no assist credit); "
                     "RPR is v1.2, our own "
                     "rating, not KenPom's kPOY. Tier is the GM "
                     "board tier for that player.</p>")
    else:
        parts.append("<p>No roster data for this season.</p>")
    body = "\n".join(parts)
    # Player detail for this team's roster rows, embedded so the
    # page works with no network and no other file.
    _detail = {k: v for k, v in PLAYER_DETAIL.items()
               if k.endswith(f"|{team}")}
    _detail_json = json.dumps(_detail, separators=(",", ":"))
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
<a href="{REPO}">RD3-LIL/nba-ratings on GitHub</a>. Data through {{THROUGH}}.
Built {{BUILT}}.</p>
</footer>
<script>var PLAYER_DETAIL = {_detail_json};</script>
<script>{{TEAM_JS}}</script>
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
        text = text.replace("{THROUGH}",
                            f"{DATA_THROUGH[0]} ({DATA_THROUGH[1]} games)")
        text = text.replace("{TEAM_JS}", TEAM_JS)
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
table.ratings tbody tr.player { cursor: pointer; }
table.ratings tbody tr.player:hover td { background: #eef4ff; }
table.roster tbody tr.player { cursor: pointer; }
table.roster tbody tr.player:hover td { background: #eef4ff; }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.player-detail { font-size: 14px; line-height: 1.6; }
.player-detail .pd-comp { font-variant-numeric: tabular-nums; }
.player-detail .pd-note { color: #656d76; font-size: 13px; }
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
.skill-chart { max-width: 640px; margin: 8px 0 4px; }
.skill-row { display: grid; grid-template-columns: 150px 1fr 36px;
  align-items: center; gap: 10px; margin: 8px 0; }
.skill-label { font-size: 15px; }
.skill-track { position: relative; display: block; height: 16px;
  background: #eaeef2; border-radius: 999px; overflow: visible; }
.skill-fill { display: block; height: 100%; border-radius: 999px; }
.skill-fill.below { background: #f59e0b; }
.skill-fill.above { background: #38bdf8; }
.skill-target { position: absolute; left: 60%; top: -3px; bottom: -3px;
  width: 2px; background: #656d76; }
.skill-value { text-align: right; font-variant-numeric: tabular-nums;
  font-weight: 600; }
/* At-a-glance cards and diverging bars, same visual language as
  the player profiles in build_player_pages.py: big value, rank
  underneath with a rank bar, and diverging bars centered at
  league average. Blue is above average, orange is below. */
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 12px 0; }
.stat-card { border: 1px solid #d0d7de; border-radius: 8px; padding: 10px 12px; }
.stat-card .label { font-size: 12px; color: #656d76; text-transform: uppercase; letter-spacing: 0.04em; }
.stat-card .value { font-size: 26px; font-weight: 700; line-height: 1.2; }
.stat-card .rank { font-size: 13px; color: #656d76; }
.rank-bar { height: 6px; background: #eaeef2; border-radius: 999px; overflow: hidden; margin-top: 6px; }
.rank-bar span { display: block; height: 100%; background: #38bdf8; }
.comp-chart { max-width: 640px; margin: 8px 0; }
.comp-row { display: grid; grid-template-columns: 150px 1fr 52px;
  align-items: center; gap: 10px; margin: 8px 0; }
.comp-row .comp-label { font-size: 15px; }
.comp-track { position: relative; height: 16px; background: #eaeef2; border-radius: 999px; }
.comp-center { position: absolute; left: 50%; top: -3px; bottom: -3px; width: 2px; background: #656d76; }
.comp-target { position: absolute; left: 60%; top: -3px; bottom: -3px; width: 2px; background: #1f2328; }
.comp-fill { position: absolute; top: 0; bottom: 0; border-radius: 999px; }
.comp-fill.pos { background: #38bdf8; left: 50%; }
.comp-fill.neg { background: #f59e0b; right: 50%; }
.comp-value { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.comp-group { font-size: 13px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.04em; color: #656d76; margin: 16px 0 4px; }
.tip { cursor: help; border-bottom: 1px dotted #656d76; position: relative; }
.tip::after { content: attr(data-tip); display: none; position: absolute; bottom: 125%; left: 0; width: 220px; background: #1f2328; color: #fff; padding: 8px; border-radius: 6px; font-size: 13px; line-height: 1.4; text-transform: none; letter-spacing: normal; font-weight: 400; z-index: 10; white-space: normal; }
.tip:hover::after, .tip:focus::after, .tip:active::after { display: block; }
table.targets { font-size: 13px; }
table.targets th, table.targets td { padding: 4px 10px; }
table.targets td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.targets td .sub { display: block; font-size: 11px; color: #656d76; font-weight: 400; }
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
// win percentage (data-v is wins / (wins + losses)).
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function fmt1(v) {
  // Values arrive as numbers (roster rows) or strings (PLAYER_DETAIL
  // is built from CSVs); coerce so a string never throws on toFixed.
  var n = (v === null || v === undefined || v === "") ? NaN : parseFloat(v);
  return isNaN(n) ? "-" : n.toFixed(1);
}
function fmt2(v) {
  var n = (v === null || v === undefined || v === "") ? NaN : parseFloat(v);
  return isNaN(n) ? "-" : n.toFixed(2);
}
function fmtPct(v) {
  // Fractions in the CSVs (0.61) rendered as percentages (61.0%).
  var n = (v === null || v === undefined || v === "") ? NaN : parseFloat(v);
  return isNaN(n) ? "-" : (n * 100).toFixed(1) + "%";
}
function openPlayerDetail(row) {
  var next = row.nextElementSibling;
  if (next && next.classList.contains("detail")) {
    next.remove();
    return;
  }
  var table = row.closest("table");
  if (table) closeDetails(table);
  var pid = row.dataset.pid || "";
  var d = (typeof PLAYER_DETAIL !== "undefined") ? PLAYER_DETAIL[pid] : null;
  var tr = document.createElement("tr");
  tr.className = "detail";
  var td = document.createElement("td");
  td.colSpan = row.cells.length;
  if (!d) {
    td.textContent = "No detail available for this player.";
  } else {
    // PLAYER_DETAIL values are flat CSV strings, not nested
    // components: scoring/playmaking/defense/rebounding sit on d.
    var html = '<div class="player-detail"><strong>' + esc(d.name) + '</strong>'
      + " (" + esc(d.team) + ", " + esc(d.position || "-") + ") - "
      + d.gp + " GP, " + d.mpg + " MPG, " + d.min_pct + "% of team minutes"
      + '<br><span class="pd-comp">RPR v1.2 ' + fmt2(d.rpr)
      + " = scoring " + fmt2(d.scoring) + " + playmaking " + fmt2(d.playmaking)
      + " + defense " + fmt2(d.defense) + " + rebounding "
      + fmt2(d.rebounding) + "</span>"
      + "<br>ORtg (full, with assist credit) " + fmt1(d.ortg_full)
      + " - simple ORtg " + fmt1(d.ortg_simple)
      + " (shrunk " + fmt1(d.ortg_shrunk) + ", opponent adj "
      + fmt2(d.opp_adj) + ") - TS% " + fmtPct(d.ts_pct)
      + "<br>3P% raw " + fmtPct(d.fg3_pct) + " - shrunk "
      + fmtPct(d.fg3_pct_shrunk) + " (shrunk toward league average "
      + "with m = 750 attempts; small samples shrink hard)"
      + "<br>Usage " + fmt1(d.poss_pct) + "% of team possessions"
      + (d.trend_ppg ? " - trend " + fmt1(d.trend_ppg) + " PPG vs season"
         : "")
      + '<br><span class="pd-note">RPR v1.1 was ' + fmt2(d.rpr_v1_1)
      + "; v1.2 recenters the counting terms on league averages, "
      + "so an average player scores about 0.</span></div>";
    td.innerHTML = html;
  }
  tr.appendChild(td);
  row.after(tr);
}
function closeDetails(table) {
  table.querySelectorAll("tbody tr.detail").forEach(function (d) {
    d.remove();
  });
}
function applyFilter(table) {
  var q = table._q || "";
  var conf = table._conf || "";
  var rk = table._rookiesOnly || false;
  table.querySelectorAll("tbody tr:not(.detail)").forEach(function (r) {
    var team = r.cells[1].textContent.toLowerCase();
    var ok = (q === "" || team.indexOf(q) !== -1)
      && (conf === "" || r.dataset.conf === conf)
      && (!rk || r.dataset.rookie === "1");
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
    + '</strong> - AdjO ' + c[4].dataset.v + ' (#' + c[4].dataset.r + ') &middot; AdjD '
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
      var pid = esc(p[0]) + "|" + esc(c[1].textContent);
      // p[9] is the player-profile slug (see rosters()); this
      // panel only renders on index.html, so players/ is local.
      var pname = p[9] ? '<a href="players/' + p[9] + '.html">' + esc(p[0]) + '</a>' : esc(p[0]);
      html += '<tr class="player" data-pid="' + pid + '"><td>' + pname + "</td><td>"
        + ((p[1] && p[1] !== "NA") ? esc(p[1]) : "-")
        + "</td><td>" + p[2] + "</td><td>" + fmt1(p[3]) + "</td><td>"
        + fmt1(p[4]) + "</td><td>" + fmt1(p[5]) + "</td><td>"
        + fmt1(p[6]) + "</td><td>" + fmt1(p[7]) + "</td><td>"
        + (p[8] ? esc(p[8]) : "-") + "</td></tr>";
    });
    html += "</tbody></table>"
      + '<p class="note">Sorted by minutes. Click a player row for '
      + "detail. ORtg is approximate "
      + "(no assist credit); RPR is v1.2, our own rating, not KenPom's "
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
      if (table.dataset.renumber) {
        rows.forEach(function (r, idx) {
          r.cells[0].dataset.v = String(idx + 1);
          r.cells[0].textContent = String(idx + 1);
        });
      }
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
document.querySelectorAll("button.rookie-toggle").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var table = document.getElementById(btn.dataset.table);
    closeDetails(table);
    table._rookiesOnly = !table._rookiesOnly;
    btn.classList.toggle("active", table._rookiesOnly);
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
// Click a player row (top 100, rookies, team roster, or a roster
// inside an expanded team panel) to expand that player's detail.
// Delegated so rows added later by openDetail are covered too.
document.addEventListener("click", function (e) {
  var row = e.target.closest("tr.player");
  if (!row) return;
  if (e.target.closest("a")) return;
  openPlayerDetail(row);
});
"""

# Team pages run the same script: they have no ratings tables, so
# the table handlers are no-ops and only the player-row handler
# and openPlayerDetail do anything.
TEAM_JS = JS

RANKS = {}
LOGS = {}
ROSTERS = {}
LOG_IDX = {}
NEEDS = {}
PLAYER_DETAIL = {}
# Team SHARP (minutes-weighted mean and best shooter per team),
# cap lines from the team_needs payload, player slug by
# (name, team) for target links, and AdjO/AdjD/Net/AdjT
# percentiles per team. Populated in main() before the pages
# are built; team_page_html reads them, never recomputes a rating.
TEAM_SHARP = {}
CAP_LINES = {}
SLUG_BY_NAME_TEAM = {}
TEAM_PCT = {}
BUILT = datetime.now(ZoneInfo("America/Chicago")).strftime("%B %-d, %Y")
DATA_THROUGH = ("unknown", 0)


def data_through():
    """(display date, n_games) from the games file, never hardcoded.

    The data-through date is the last played date in
    data/games_2026_27.json, so the header always says what the
    ratings actually cover, even when a run lands a day late.
    """
    games = json.loads((DATA / "games_2026_27.json").read_text())
    played = [g for g in games
              if not (g["home_score"] == 0 and g["away_score"] == 0)
              and not g.get("stats_missing")]
    last = max(g["date"] for g in played) if played else None
    disp = datetime.strptime(last, "%Y%m%d").strftime("%B %-d, %Y") \
        if last else "unknown"
    return disp, len(played)


def main():
    global DATA_THROUGH
    DATA_THROUGH = data_through()
    rows, league_o, league_t, info = load_teams()
    players = read_csv(DATA / "players_2026_27.csv")
    # ORtg_full lives in the eval CSV, not the base player CSV:
    # merge it in so the top-100 and rookie tables can show it.
    _eval_by_id = {(r["athlete_id"], r["team"]): r
                   for r in read_csv(DATA / "players_2026_27_eval.csv")}
    for p in players:
        _ev = _eval_by_id.get((p["athlete_id"], p["team"]), {})
        p["ortg_full"] = (_ev.get("ortg_full") or p.get("ortg_shrunk")
                          or p.get("ortg"))
    PLAYER_DETAIL.update(load_player_detail())
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
    shooters_html = shooters_section()
    top_html = top_players_section(players)
    rookies_html = rookies_section(players)
    gm_html = gm_board_section()
    needs_path = DATA / "team_needs_2026_27.json"
    if needs_path.exists():
        _needs_doc = json.loads(needs_path.read_text())
        NEEDS.update(_needs_doc["teams"])
        CAP_LINES.update(_needs_doc.get("cap_lines", {}))
    # Player slug by (name, team), for target links on team pages.
    for p in players:
        SLUG_BY_NAME_TEAM[(p["name"], p["team"])] = player_slug(
            p["name"], p["athlete_id"], p["team"])
    # AdjO/AdjD/Net/AdjT percentiles per team, same formula as
    # team_needs.py, for the vs league average graph. AdjD is
    # reversed (lower is better), so a good defense scores high.
    for key, rev in (("adj_o", False), ("adj_d", True),
                     ("net", False), ("adj_t", False)):
        vals = [r[key] for r in rows]
        for r in rows:
            v = -r[key] if rev else r[key]
            TEAM_PCT.setdefault(r["team"], {})[key] = _pct_of(
                [-x if rev else x for x in vals], v)
    # Team SHARP: minutes-weighted mean among qualifying shooters
    # plus the best shooter per team, from sharp_2026_27.json.
    # Missing file means no SHARP line on the team pages, not a
    # failed build.
    _sharp_path = DATA / "sharp_2026_27.json"
    if _sharp_path.exists():
        _sharp_doc = json.loads(_sharp_path.read_text())
        _by_team = {}
        for sp in _sharp_doc.get("players", []):
            _by_team.setdefault(sp["team"], []).append(sp)
        for _team, _lst in _by_team.items():
            _wsum = sum(sp["mpg"] * sp["gp"] for sp in _lst)
            _mean = (sum(sp["sharp"] * sp["mpg"] * sp["gp"] for sp in _lst)
                     / _wsum) if _wsum else 0.0
            _best = max(_lst, key=lambda sp: sp["sharp"])
            TEAM_SHARP[_team] = {
                "mean": round(_mean, 2),
                "best_name": _best["name"],
                "best_sharp": _best["sharp"],
                "n": len(_lst),
            }
    needs_html = team_needs_section()
    logs_json = json.dumps(LOGS, separators=(",", ":"))
    rosters_json = json.dumps(ROSTERS, separators=(",", ":"))
    detail_json = json.dumps(PLAYER_DETAIL, separators=(",", ":"))
    built = BUILT
    through, n_games_data = DATA_THROUGH

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
<p class="subtitle">Data through {through} ({n_games_data} games). NBA
ratings in the style of RonPom (College Ratings),
built from free ESPN data. Adjusted offense and defense per 100
possessions, solved properly (damped Gauss-Seidel), league-average
prior. Same ORtg, %Poss, and RPR formulas as RonPom, adapted to
48-minute games; RPR is v1.2 (recentered, see
How to read this).</p>

<nav class="topnav">
<a href="#team-ratings">Team ratings</a>
<a href="#players">Player leaders</a>
<a href="#shooters">SHARP shooters</a>
<a href="#top-players">Top players</a>
<a href="#rookies">Rookies</a>
<a href="#gm-board">GM board</a>
<a href="#team-needs">Team needs</a>
<a href="#how-to-read">How to read this</a>
<a href="rpr-research.html">RPR research</a>
<a href="distributions.html">Distributions</a>
</nav>

<section id="team-ratings"><h2>2026-27 team ratings</h2>
<p class="warn">Early season: most teams have only about 15 games so far.
The ratings use a league-average prior (no last-season prior: NBA
teams change too much year over year for one to help), so the top of
the table is still mostly noise, not a real ranking.</p>
<p class="warn">{DISCLAIMER}</p>
<p>{len(rows)} teams rated, {len(players)} players, {n_games} games.
Click a column header to sort. Click a team name for that team's own
page, or a team row to see its game log and roster here.</p>
{table_html}
</section>

<section id="players"><h2>Player leaders</h2>
{leaders_html}
</section>

{shooters_html}

{top_html}

{rookies_html}

{gm_html}

{needs_html}

<section id="how-to-read"><h2>How to read this</h2>
<p><strong>Getting around</strong>: the bar at the top jumps to the
team ratings, the player leaders, the top 100 players, the
rookies, or the GM board. East / West tabs above the team table filter to one
conference; the filter box searches team names. Click a team name
anywhere to open that team's own page, with its ratings, game log,
and roster. Click a team row to see its game log and roster for the
season right here. The <a href="rpr-research.html">RPR research</a> page
ranks the free data sources that could improve RPR, with the proposed
formulas, the validation gate, and what is not worth adding.</p>
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
underrates playmakers. Read it with %Poss. The tables show
<strong>ORtg (full)</strong>, the Oliver version with assist credit,
falling back to shrunk simple ORtg where the full version is not
available. <strong>RPR v1.2</strong>
is our own single-number player rating, not KenPom's proprietary
kPOY: the ORtg that enters it is first shrunk toward the league-average ORtg (K = 200 possessions
used) and adjusted for the defences the player faced (OppAdj,
shrunk toward 0 with K = 500 possessions faced). On top of that,
v1.2 recenters the counting terms (playmaking, defense,
rebounding) on their league-average per-36 rates, so an average
player scores about 0 from those terms and the median RPR is
near 0, instead of every player getting free credit just for
playing. The old values are kept as rpr_v1 and rpr_v1_1 in the
CSVs. RPR_value = RPR v1.2 * GP / 82 is how
much value a player has delivered so far; it is not part of the
rating. Leaderboards only include players with at least 40% of their
team's minutes, so a two-minute cameo cannot top the list.
Click a player row to expand that player's detail: RPR
components, ORtg (full) vs simple, TS%, GP, and the 3P%
shrinkage applied.</p>
<p><strong>Rookies</strong>: a rookie is a player in his first NBA
season, from the ESPN roster entry (experience.years == 0).
The Rookies section ranks them by RPR with the same 40% minutes
floor, and lists notable below-floor rookies separately. The
Rookies only button on the top-100 table filters it to rookies.
Age, height, and weight in the CSVs also come from the roster
pull; a player on no current roster has no experience or bio
fields.</p>
<p><strong>Honest limits</strong>: about 15 games per team, so the
order is still noisy. League-average prior only, no last-season
prior, so the shrinkage pulls every player toward the same league
average, rookies and veterans alike. Box-score only: no tracking,
no on/off. RPR v1.2 adjusts ORtg for opponent defence; ORtg_full
does not. Salaries on the team pages come from Basketball-Reference
contracts and are display only: they do not change any rating or
TradeScore, and a salary shown there does not prove a trade is
legal. {DISCLAIMER} Full
method in the README.</p></section>

<footer>
<p>Data: ESPN public scoreboard and box scores. Code and full CSVs:
<a href="{REPO}">RD3-LIL/nba-ratings on GitHub</a>. Data through {through}
({n_games_data} games). Built {built}.</p>
</footer>
<script>var GAME_LOGS = {logs_json};</script>
<script>var ROSTERS = {rosters_json};</script>
<script>var PLAYER_DETAIL = {detail_json};</script>
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
