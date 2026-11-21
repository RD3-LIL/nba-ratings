"""Build one player profile page per player for the NBA Ratings site.

Reads data/players_2026_27_eval.json (base profiles + the eval
layer: ORtg_full, RPR v1.2 components, shooting, playmaking,
consistency, trending), data/gm_board_2026_27.csv (tier,
archetype, value signals, red flags), data/teams_2026_27.json
(team Net rank), and the raw ESPN summaries under
data/raw/summary/ for a per-player game log.

Writes players/<slug>.html for every profile (475 on the Nov 20
run, 473 distinct athletes: Nique Clifford and Carson Cooper
each have one profile per team, so the slug carries the team
too) plus players/index.html, a filterable directory of all
profiles. No network, stdlib only. Re-run after regenerating
the ratings files; stale players/*.html files are deleted.

What a profile shows, and why:
- Bio: headshot, team, position, jersey, age, height, weight,
  experience, rookie flag, GM tier. All from the roster pull
  or the box scores, never invented.
- At a glance: RPR, PPG, RPG, APG, ORtg (full), TS%, each with
  its NBA rank. Ranks are competition ranks (1, 2, 2, 4) among
  all players with a value for that stat, denominator shown,
  so a rank is never presented without its pool. A small-sample
  note appears when a player is under 40% of team minutes or
  under 5 games: those ranks are among all 475 players who
  appeared, so a hot few games can rank high.
- Season line: totals, per game, and per 36 for the counting
  stats, plus shooting (FG/3P/FT/eFG/TS, raw and shrunk where
  the site already shrinks them) with bars against the league
  average, and a stacked bar for where the points come from
  (2s / 3s / FTs).
- RPR breakdown: the four v1.2 components (scoring,
  playmaking, defense, rebounding) as diverging bars that sum
  to RPR, plus RPR value delivered so far, the v1.1 number for
  comparison, OppAdj, and the ORtg shrinkage chain.
- Playmaking / defense / rebounding, consistency (points
  std dev, games with 20+ / 30+), and trending (last-7-days
  PPG vs season PPG).
- Game log: every game in the data, most recent first, with
  opponent, venue, result, minutes, and the counting line.
- Teammates: the rest of the player's team, by minutes, each
  linking to their own profile.

Usage:
    python3 build_player_pages.py
"""

import csv
import html
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import build_page as bp

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"
REPO = "https://github.com/RD3-LIL/nba-ratings"
BUILT = datetime.now(ZoneInfo("America/Chicago")).strftime("%B %-d, %Y")
STAT_NAMES = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO",
              "STL", "BLK", "OREB", "DREB", "PF", "+/-"]


def player_slug(name, athlete_id, team):
    """Player name -> stable, unique URL slug for players/<slug>.html.

    Name slug + athlete id + team slug. The team is in there
    because two players in this data have one profile per team
    after changing teams mid-season (Nique Clifford, Carson
    Cooper); name + id alone would collide for them.
    """
    norm = unicodedata.normalize("NFKD", name)
    ascii_name = norm.encode("ascii", "ignore").decode("ascii")
    s = ascii_name.lower().replace("'", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return f"{s}-{athlete_id}-{bp.team_slug(team)}"


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _fmt(v, nd=1):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def _pct(v):
    if v is None:
        return "-"
    return f"{100 * float(v):.1f}%"


def _height(height_in):
    if height_in is None:
        return "-"
    total = int(round(float(height_in)))
    return f"{total // 12}'{total % 12}\""


def _rank_map(players, getter):
    """Competition ranks (1, 2, 2, 4) for one metric.

    Returns ({(athlete_id, team): rank}, denominator). Players
    with a None value get no rank and do not count in the
    denominator.
    """
    vals = []
    for p in players:
        v = getter(p)
        if v is not None:
            vals.append(((p["athlete_id"], p["team"]), v))
    vals.sort(key=lambda kv: kv[1], reverse=True)
    out, prev_val, prev_rank = {}, None, 0
    for i, (key, v) in enumerate(vals):
        rank = prev_rank if v == prev_val else i + 1
        out[key] = rank
        prev_val, prev_rank = v, rank
    return out, len(vals)


def load_game_logs():
    """Per (athlete_id, team_id) game logs from raw summaries.

    Each entry: {date, opponent, venue, result, score_for,
    score_against, min, pts, fg, reb, ast, to, stl, blk}.
    Date, teams, and scores come from the parsed games file,
    never the raw header date (UTC, can be a day off).
    """
    games = json.loads((DATA / "games_2026_27.json").read_text())
    game_map = {str(g["event_id"]): g for g in games}
    logs = defaultdict(list)
    raw_dir = DATA / "raw" / "summary"
    for raw_path in sorted(raw_dir.glob("*.json")) if raw_dir.is_dir() else []:
        game = game_map.get(raw_path.stem)
        if game is None:
            continue
        try:
            summary = json.loads(raw_path.read_text())
        except ValueError:
            continue
        entries = summary.get("boxscore", {}).get("players", []) or []
        for team_entry in entries:
            team = team_entry.get("team", {}) or {}
            team_name = team.get("displayName", "")
            team_id = str(team.get("id", ""))
            opp_name = ""
            for other in entries:
                if other is not team_entry:
                    opp_name = ((other.get("team", {}) or {})
                                .get("displayName", ""))
                    break
            is_home = team_name == game.get("home")
            venue = "H" if is_home else "A"
            pts_for = game["home_score"] if is_home else game["away_score"]
            pts_against = game["away_score"] if is_home else game["home_score"]
            result = "W" if pts_for > pts_against else "L"
            for group in team_entry.get("statistics", []) or []:
                names = group.get("names", []) or STAT_NAMES
                idx = {n: i for i, n in enumerate(names)}
                for row in group.get("athletes", []) or []:
                    if row.get("didNotPlay") or not row.get("stats"):
                        continue
                    athlete = row.get("athlete", {}) or {}
                    aid = str(athlete.get("id", ""))
                    if not aid:
                        continue
                    stats = row.get("stats") or []

                    def get(name):
                        i = idx.get(name)
                        return stats[i] if i is not None and i < len(stats) else None

                    logs[(aid, team_id)].append({
                        "date": game["date"],
                        "opponent": opp_name,
                        "venue": venue,
                        "result": result,
                        "score_for": pts_for,
                        "score_against": pts_against,
                        "min": _parse_int(get("MIN")) or 0,
                        "pts": _parse_int(get("PTS")) or 0,
                        "fg": get("FG") or "",
                        "reb": _parse_int(get("REB")) or 0,
                        "ast": _parse_int(get("AST")) or 0,
                        "to": _parse_int(get("TO")) or 0,
                        "stl": _parse_int(get("STL")) or 0,
                        "blk": _parse_int(get("BLK")) or 0,
                    })
    for key in logs:
        logs[key].sort(key=lambda g: g["date"], reverse=True)
    return logs


EXTRA_CSS = """
.profile-head { display: flex; gap: 16px; align-items: flex-start; margin: 12px 0; }
.profile-head img { width: 96px; height: 96px; object-fit: cover; border-radius: 8px; border: 1px solid #d0d7de; background: #f6f8fa; }
.profile-head .bio { color: #656d76; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eaeef2; font-size: 12px; font-weight: 600; margin-right: 4px; }
.badge.rookie { background: #dbeafe; color: #1d4ed8; }
.badge.tier { background: #dcfce7; color: #166534; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 12px 0; }
.stat-card { border: 1px solid #d0d7de; border-radius: 8px; padding: 10px 12px; }
.stat-card .label { font-size: 12px; color: #656d76; text-transform: uppercase; letter-spacing: 0.04em; }
.tip { cursor: help; border-bottom: 1px dotted #656d76; position: relative; }
.tip::after { content: attr(data-tip); display: none; position: absolute; bottom: 125%; left: 0; width: 220px; background: #1f2328; color: #fff; padding: 8px; border-radius: 6px; font-size: 13px; line-height: 1.4; text-transform: none; letter-spacing: normal; font-weight: 400; z-index: 10; white-space: normal; }
.tip:hover::after, .tip:focus::after, .tip:active::after { display: block; }
.shoot-row .tip { border-bottom: 1px dotted #656d76; }
.stat-card .value { font-size: 26px; font-weight: 700; line-height: 1.2; }
.stat-card .rank { font-size: 13px; color: #656d76; }
.rank-bar { height: 6px; background: #eaeef2; border-radius: 999px; overflow: hidden; margin-top: 6px; }
.rank-bar span { display: block; height: 100%; background: #38bdf8; }
.comp-chart { max-width: 640px; margin: 8px 0; }
.comp-row { display: grid; grid-template-columns: 110px 1fr 56px; align-items: center; gap: 10px; margin: 8px 0; }
.comp-track { position: relative; height: 16px; background: #eaeef2; border-radius: 999px; }
.comp-center { position: absolute; left: 50%; top: -3px; bottom: -3px; width: 2px; background: #656d76; }
.comp-fill { position: absolute; top: 0; bottom: 0; border-radius: 999px; }
.comp-fill.pos { background: #38bdf8; left: 50%; }
.comp-fill.neg { background: #f59e0b; right: 50%; }
.comp-value { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.shoot-row { display: grid; grid-template-columns: 90px 1fr 110px; align-items: center; gap: 10px; margin: 8px 0; max-width: 640px; }
.shoot-track { position: relative; height: 16px; background: #eaeef2; border-radius: 999px; }
.shoot-fill { display: block; height: 100%; background: #38bdf8; border-radius: 999px; }
.shoot-league { position: absolute; top: -3px; bottom: -3px; width: 2px; background: #656d76; }
.stacked { display: flex; height: 22px; border-radius: 999px; overflow: hidden; max-width: 640px; margin: 8px 0; }
.stacked span { display: block; height: 100%; }
.stack-2s { background: #38bdf8; }
.stack-3s { background: #1d4ed8; }
.stack-ft { background: #93c5fd; }
.legend { font-size: 13px; color: #656d76; }
.legend b { color: #1f2328; }
table.profile { font-size: 14px; width: auto; }
table.profile th, table.profile td { padding: 4px 12px; }
table.profile td:nth-child(n+2) { text-align: right; font-variant-numeric: tabular-nums; }
table.gamelog { font-size: 13px; }
"""


TIPS = {
    "RPR": "Our single-number rating for how much a player helps his team, where an average player scores about 0 and a star scores about +3 to +7.",
    "PPG": "Points scored per game.",
    "RPG": "Rebounds grabbed per game.",
    "APG": "Assists per game.",
    "ORtg (full)": "Points the player produces per 100 possessions he uses, with credit for assists.",
    "TS%": "Points scored per shooting attempt, counting twos, threes, and free throws together.",
    "SHARP": "Extra points per game from threes and free throws above league average.",
    "FG%": "Share of all field goal attempts made.",
    "3P%": "Share of three point attempts made.",
    "FT%": "Share of free throw attempts made.",
    "eFG%": "Field goal percentage adjusted so a three counts more than a two, since it is worth more points.",
}


def rank_rows(p, ranks):
    """(label, value, rank, denominator) rows for the at-a-glance grid."""
    key = (p["athlete_id"], p["team"])
    ev = p["eval"]
    r = p.get("ratings") or {}
    defs = [
        ("RPR", r.get("rpr"), "rpr", 2),
        ("PPG", p["per_game"]["pts"], "pts_pg", 1),
        ("RPG", p["per_game"]["reb"], "reb_pg", 1),
        ("APG", p["per_game"]["ast"], "ast_pg", 1),
        ("ORtg (full)", ev.get("ortg_full"), "ortg_full", 1),
        ("TS%", p["shooting"].get("ts_pct"), "ts_pct", 1),
        ("SHARP", p.get("sharp"), "sharp", 2),
    ]
    out = []
    for label, value, rank_key, nd in defs:
        rank_map, denom = ranks[rank_key]
        rank = rank_map.get(key)
        out.append((label, value, rank, denom, nd))
    return out


def profile_html(p, ranks, league, logs, teammates, other_profile,
                 team_net_rank, through, n_games):
    """One player's full profile page, as an HTML string."""
    t = p["totals"]
    pg = p["per_game"]
    p36 = p["per_36"]
    s = p["shooting"]
    r = p.get("ratings") or {}
    ev = p["eval"]
    gp = t["gp"]
    min_pct = r.get("min_pct")
    small_sample = (gp < 5 or (min_pct is not None and min_pct < 40))
    slug = player_slug(p["name"], p["athlete_id"], p["team"])
    team_slug = bp.team_slug(p["team"])
    gm = p.get("gm") or {}
    tier = gm.get("tier", "")
    archetype = gm.get("archetype", "")
    comp = ev.get("rpr_components") or {}
    pb = ev.get("points_breakdown") or {}
    trend = ev.get("trending") or {}
    cons = ev.get("consistency") or {}
    play = ev.get("playmaking") or {}
    defe = ev.get("defense_rebounding") or {}

    headshot = ""
    if p.get("headshot"):
        headshot = (f'<img src="{html.escape(p["headshot"])}" '
                    f'alt="{html.escape(p["name"])} headshot">')
    badges = []
    if p.get("is_rookie"):
        badges.append('<span class="badge rookie">Rookie</span>')
    if tier:
        badges.append(f'<span class="badge tier">{html.escape(tier)}</span>')
    if archetype:
        badges.append(f'<span class="badge">{html.escape(archetype)}</span>')
    exp = p.get("experience_years")
    exp_s = f"{exp} yrs exp" if exp is not None else "experience unknown"
    _w = p.get("weight_lbs")
    _w_s = f"{int(_w)}" if _w is not None else "-"
    bio = (f"{html.escape(p['position'] or '-')} &middot; "
           f"#{html.escape(str(p.get('jersey') or '-'))} &middot; "
           f"Age {p.get('age') or '-'} &middot; "
           f"{_height(p.get('height_in'))} &middot; "
           f"{_w_s} lbs &middot; {exp_s}")

    # At-a-glance cards with ranks.
    cards = []
    for label, value, rank, denom, nd in rank_rows(p, ranks):
        tip = html.escape(TIPS.get(label, ""), quote=True)
        label_html = (f'<span class="tip" tabindex="0" data-tip="{tip}">{label}</span>'
                      if tip else label)
        if value is None:
            cards.append(
                f'<div class="stat-card"><div class="label">{label_html}</div>'
                f'<div class="value">-</div>'
                f'<div class="rank">No value in this data</div></div>')
            continue
        if rank is None:
            rank_s = "Unranked"
            bar = ""
        else:
            pct_top = 100 * (1 - (rank - 1) / denom) if denom else 0
            rank_s = f"#{rank} of {denom} in the NBA"
            bar = (f'<div class="rank-bar"><span style="width:'
                   f'{pct_top:.0f}%"></span></div>')
        cards.append(
            f'<div class="stat-card"><div class="label">{label_html}</div>'
            f'<div class="value">{_fmt(value, nd) if label != "TS%" else _pct(value)}</div>'
            f'<div class="rank">{rank_s}</div>{bar}</div>')

    # Shooting bars vs league average (grey tick = league).
    shoot_rows = []
    for label, value, league_v in [
            ("FG%", s.get("fg_pct"), league["fg_pct"]),
            ("3P%", s.get("fg3_pct"), league["fg3_pct"]),
            ("FT%", s.get("ft_pct"), league["ft_pct"]),
            ("eFG%", s.get("efg_pct"), league["efg_pct"]),
            ("TS%", s.get("ts_pct"), league["ts_pct"])]:
        tip = html.escape(TIPS.get(label, ""), quote=True)
        label_html = (f'<span class="tip" tabindex="0" data-tip="{tip}">{label}</span>'
                      if tip else label)
        if value is None:
            shoot_rows.append(
                f'<div class="shoot-row"><span>{label_html}</span>'
                f'<span class="shoot-track"></span>'
                f'<span>- (no attempts)</span></div>')
            continue
        width = max(0.0, min(100.0, 100 * float(value)))
        league_x = max(0.0, min(100.0, 100 * float(league_v)))
        shoot_rows.append(
            f'<div class="shoot-row"><span>{label_html}</span>'
            f'<span class="shoot-track"><span class="shoot-fill" '
            f'style="width:{width:.1f}%"></span>'
            f'<span class="shoot-league" style="left:{league_x:.1f}%"></span>'
            f'</span><span>{_pct(value)} (league {_pct(league_v)})</span></div>')

    # Points breakdown stacked bar.
    shares = [(pb.get("share_2s") or 0, "stack-2s", "2s"),
              (pb.get("share_3s") or 0, "stack-3s", "3s"),
              (pb.get("share_ft") or 0, "stack-ft", "FTs")]
    stack = "".join(
        f'<span class="{cls}" style="width:{share * 100:.1f}%"></span>'
        for share, cls, _ in shares)
    legend = " &middot; ".join(
        f"<b>{label}</b> {_pct(share)}" for share, _, label in shares)

    # RPR component diverging bars, scale +/-4.
    comp_rows = []
    for label, key_c in [("Scoring", "scoring"), ("Playmaking", "playmaking"),
                         ("Defense", "defense"), ("Rebounding", "rebounding")]:
        v = comp.get(key_c)
        if v is None:
            comp_rows.append(
                f'<div class="comp-row"><span>{label}</span>'
                f'<span class="comp-track"><span class="comp-center"></span></span>'
                f'<span class="comp-value">-</span></div>')
            continue
        width = min(50.0, abs(float(v)) / 4.0 * 50.0)
        cls = "pos" if v >= 0 else "neg"
        comp_rows.append(
            f'<div class="comp-row"><span>{label}</span>'
            f'<span class="comp-track"><span class="comp-center"></span>'
            f'<span class="comp-fill {cls}" style="width:{width:.1f}%"></span></span>'
            f'<span class="comp-value">{float(v):+.2f}</span></div>')

    # SHARP diverging bar, scale +/-2.5, same style as the RPR
    # component bars. Center is 0, league average. Positive goes
    # right in blue, negative goes left in orange.
    sharp_v = p.get("sharp")
    sharp_tip = html.escape(TIPS.get("SHARP", ""), quote=True)
    sharp_label = (f'<span class="tip" tabindex="0" data-tip="{sharp_tip}">SHARP</span>')
    if sharp_v is None:
        sharp_bar = (f'<div class="comp-row"><span>{sharp_label}</span>'
                     f'<span class="comp-track"><span class="comp-center"></span></span>'
                     f'<span class="comp-value">-</span></div>')
    else:
        sharp_w = min(50.0, abs(float(sharp_v)) / 2.5 * 50.0)
        sharp_cls = "pos" if sharp_v >= 0 else "neg"
        sharp_bar = (f'<div class="comp-row"><span>{sharp_label}</span>'
                     f'<span class="comp-track"><span class="comp-center"></span>'
                     f'<span class="comp-fill {sharp_cls}" style="width:{sharp_w:.1f}%"></span></span>'
                     f'<span class="comp-value">{float(sharp_v):+.2f}</span></div>')

    # Game log rows.
    log_rows = []
    for g in logs:
        opp = html.escape(g["opponent"])
        log_rows.append(
            f"<tr><td>{g['date'][4:6]}/{g['date'][6:8]}</td>"
            f"<td>{opp}</td><td>{g['venue']}</td><td>{g['result']}</td>"
            f"<td>{g['score_for']}-{g['score_against']}</td>"
            f"<td>{g['min']}</td><td>{g['fg']}</td><td>{g['pts']}</td>"
            f"<td>{g['reb']}</td><td>{g['ast']}</td><td>{g['to']}</td>"
            f"<td>{g['stl']}</td><td>{g['blk']}</td></tr>")
    log_html = ("<p>No game log in this data.</p>" if not log_rows else
                '<div class="table-scroll"><table class="gamelog"><thead><tr>'
                "<th>Date</th><th>Opponent</th><th>Venue</th><th>Result</th>"
                "<th>Score</th><th>MIN</th><th>FG</th><th>PTS</th><th>REB</th>"
                "<th>AST</th><th>TO</th><th>STL</th><th>BLK</th>"
                "</tr></thead><tbody>" + "".join(log_rows) +
                "</tbody></table></div>")

    # Teammates.
    team_rows = []
    for mate in teammates:
        mslug = player_slug(mate["name"], mate["athlete_id"], mate["team"])
        team_rows.append(
            f'<li><a href="{mslug}.html">{html.escape(mate["name"])}</a> '
            f'({html.escape(mate["position"] or "-")}, '
            f'{_fmt(mate["per_game"]["pts"], 1)} PPG, '
            f'RPR {_fmt(mate.get("ratings", {}).get("rpr"), 2)})</li>')
    other_html = ""
    if other_profile:
        oslug = player_slug(other_profile["name"], other_profile["athlete_id"],
                            other_profile["team"])
        other_html = (f'<p class="note">Also played for the '
                      f'<a href="{oslug}.html">{html.escape(other_profile["team"])}</a> '
                      f'this season ({other_profile["totals"]["gp"]} games): '
                      f'that stint has its own profile, since the ratings '
                      f'are per team.</p>')

    small = ""
    if small_sample:
        small = (f'<p class="warn">Small sample: {gp} games, '
                 f'{_fmt(min_pct, 1)}% of team minutes. Ranks on this '
                 f'page are among every player who appeared this '
                 f'season, so a hot few games can rank high. The '
                 f'leaderboards on the main page use a 40% minutes '
                 f'floor for exactly this reason.</p>')

    rpr = r.get("rpr")
    body = f"""<h1>{html.escape(p["name"])}</h1>
<p class="subtitle"><a href="../teams/{team_slug}.html">{html.escape(p["team"])}</a>
&middot; Data through {through} ({n_games} games)
&middot; <a href="../index.html">&larr; NBA Ratings</a>
&middot; <a href="index.html">All players</a></p>
<div class="profile-head">{headshot}<div><div class="bio">{bio}</div>
<p>{' '.join(badges)}</p>
<p>Team Net rank #{team_net_rank} &middot; {gp} GP, {t["gs"]} GS, {_fmt(pg["min"], 1)} MPG, {_fmt(min_pct, 1)}% of team minutes</p></div></div>
{other_html}
{small}
<h2>At a glance</h2>
<div class="stat-grid">{''.join(cards)}</div>
<p class="note">RPR is v1.2, our own single-number rating (average player about 0, star about +3 to +7), not KenPom's proprietary kPOY. ORtg (full) is the Oliver rating with assist credit. SHARP is extra points per game from threes and free throws above league average, not a percent. Ranks are among all players with a value for that stat; the bar shows the percentile (100% is best in the league).</p>

<h2>Season line</h2>
<table class="profile"><thead><tr><th></th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th><th>TO</th><th>PF</th></tr></thead><tbody>
<tr><td>Totals</td><td>{t["pts"]}</td><td>{t["reb"]}</td><td>{t["ast"]}</td><td>{t["stl"]}</td><td>{t["blk"]}</td><td>{t["to"]}</td><td>{t["pf"]}</td></tr>
<tr><td>Per game</td><td>{_fmt(pg["pts"])}</td><td>{_fmt(pg["reb"])}</td><td>{_fmt(pg["ast"])}</td><td>{_fmt(pg["stl"])}</td><td>{_fmt(pg["blk"])}</td><td>{_fmt(pg["to"])}</td><td>{_fmt(pg["pf"])}</td></tr>
<tr><td>Per 36</td><td>{_fmt(p36["pts"])}</td><td>{_fmt(p36["reb"])}</td><td>{_fmt(p36["ast"])}</td><td>{_fmt(p36["stl"])}</td><td>{_fmt(p36["blk"])}</td><td>{_fmt(p36["to"])}</td><td>{_fmt(p36["pf"])}</td></tr>
</tbody></table>
<p class="note">{t["fgm"]}-{t["fga"]} FG, {t["fg3m"]}-{t["fg3a"]} 3P, {t["ftm"]}-{t["fta"]} FT. {t["min"]} minutes in {gp} games.</p>

<h2>Shooting</h2>
{''.join(shoot_rows)}
<p class="note">Bars are the player's rate; the grey tick is the league average. 3P% shrunk toward league average (m = 750 attempts) is {_pct(s.get("fg3_pct_shrunk"))} vs raw {_pct(s.get("fg3_pct"))}; TS% shrunk is {_pct(s.get("ts_pct_shrunk"))} vs raw {_pct(s.get("ts_pct"))}. Small samples shrink hard toward league average.</p>
<div class="comp-chart">{sharp_bar}</div>
<p class="note">SHARP bar is centered at 0, league average, on a scale of plus or minus 2.5 points per game.</p>
<p>SHARP <strong>{f"{p.get('sharp'):+.2f}" if p.get("sharp") is not None else "-"}</strong>: {f"extra points per game from threes and free throws above league average ({_pct(s.get('fg3_pct'))} on {p.get('sharp_detail', {}).get('fg3a_pg', '-')} 3PA per game, {_pct(s.get('ft_pct'))} FT). It counts threes and free throws only, not midrange or layups, and it is not a percent." if p.get("sharp") is not None else "No SHARP value: below the floor of 5 games and 20 minutes per game."}</p>

<h2>Where the points come from</h2>
<div class="stacked">{stack}</div>
<p class="legend">{legend}</p>

<h2>RPR breakdown</h2>
<div class="comp-chart">{''.join(comp_rows)}</div>
<p>RPR v1.2 <strong>{_fmt(rpr, 2)}</strong> = scoring {_fmt(comp.get("scoring"), 2)} + playmaking {_fmt(comp.get("playmaking"), 2)} + defense {_fmt(comp.get("defense"), 2)} + rebounding {_fmt(comp.get("rebounding"), 2)}. Value delivered so far (RPR &times; GP / 82): {_fmt(r.get("rpr_value"), 2)}. RPR v1.1 was {_fmt(r.get("rpr_v1_1"), 2)}; v1.2 recenters the counting terms on league averages, so an average player scores about 0.</p>
<p class="note">ORtg simple {_fmt(r.get("ortg"))} &rarr; shrunk {_fmt(r.get("ortg_shrunk"))} &rarr; opponent-adjusted {_fmt(r.get("ortg_adj"))} (OppAdj {_fmt(r.get("opp_adj"), 2)}, league ORtg {_fmt(r.get("league_ortg"))}). Usage {_fmt(r.get("poss_pct"))}% of team possessions. Quadrant: {html.escape(ev.get("quadrant") or "-")}.</p>

<h2>Playmaking, defense, rebounding</h2>
<p>AST/TO ratio {_fmt(play.get("ast_to_ratio"), 2)} &middot; AST per 100 possessions used {_fmt(play.get("ast_per_100_poss_used"))} &middot; STL+BLK per game {_fmt(defe.get("stl_blk_pg"))}, per 36 {_fmt(defe.get("stl_blk_per36"))} &middot; REB per 36 {_fmt(defe.get("reb_per36"))} (OREB {_fmt(defe.get("oreb_per36"))}, DREB {_fmt(defe.get("dreb_per36"))}) &middot; PF per 36 {_fmt(defe.get("pf_per36"))}.</p>
<p class="note">GM board: {html.escape(tier or "-")}{f", {html.escape(archetype)}" if archetype else ""}. {html.escape(gm.get("value_signals") or "")}{(" Red flags: " + html.escape(gm["red_flags"])) if gm.get("red_flags") else ""}</p>

<h2>Consistency and trending</h2>
<p>Points per game std dev {_fmt(cons.get("pts_std"))} (CV {_fmt(cons.get("pts_cv"), 2)}) &middot; {cons.get("games_20") or 0} games with 20+ points, {cons.get("games_30") or 0} with 30+ &middot; Last 7 days: {_fmt(trend.get("last7_ppg"))} PPG in {trend.get("last7_gp") or 0} games, trend {_fmt(trend.get("trend_ppg"), 1)} PPG vs season ({_fmt(pg["pts"])} PPG).</p>

<h2>Game log</h2>
{log_html}
<p class="note">Most recent first. FG is made-attempted.</p>

<h2>Teammates on the {html.escape(p["team"])}</h2>
<ul>{''.join(team_rows)}</ul>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(p["name"])} | NBA Ratings</title>
<style>{bp.CSS}{EXTRA_CSS}</style>
</head>
<body>
{body}
<footer>
<p>Data: ESPN public scoreboard and box scores. Code and full CSVs:
<a href="{REPO}">RD3-LIL/nba-ratings on GitHub</a>. Data through {through}
({n_games} games). Built {BUILT}.</p>
</footer>
</body>
</html>
"""


def players_index_html(players, through, n_games):
    """Filterable directory of every profile, sorted by RPR."""
    rows = []
    for p in sorted(players, key=lambda x: (x.get("ratings") or {}).get("rpr") if (x.get("ratings") or {}).get("rpr") is not None else -99, reverse=True):
        slug = player_slug(p["name"], p["athlete_id"], p["team"])
        rpr = (p.get("ratings") or {}).get("rpr")
        rows.append(
            f'<tr><td><a href="{slug}.html">{html.escape(p["name"])}</a></td>'
            f'<td><a href="../teams/{bp.team_slug(p["team"])}.html">{html.escape(p["team"])}</a></td>'
            f'<td>{html.escape(p["position"] or "-")}</td>'
            f'<td>{p["totals"]["gp"]}</td>'
            f'<td>{_fmt(p["per_game"]["pts"])}</td>'
            f'<td>{_fmt(p["per_game"]["reb"])}</td>'
            f'<td>{_fmt(p["per_game"]["ast"])}</td>'
            f'<td>{_fmt(rpr, 2) if rpr is not None else "-"}</td>'
            f'<td>{html.escape((p.get("gm") or {}).get("tier", "") or "-")}</td></tr>')
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All players | NBA Ratings</title>
<style>{bp.CSS}{EXTRA_CSS}</style>
</head>
<body>
<h1>All players</h1>
<p class="subtitle">Data through {through} ({n_games} games) &middot; <a href="../index.html">&larr; NBA Ratings</a></p>
<p>{len(players)} profiles, sorted by RPR. Type to filter by player or team name. Click a player for their full profile: season line, shooting, RPR breakdown, game log, and NBA ranks.</p>
<input class="filter" type="search" placeholder="Filter players..." id="q" style="width: 280px;">
<div class="table-scroll"><table class="ratings" id="players"><thead><tr><th>Player</th><th>Team</th><th>Pos</th><th>GP</th><th>PPG</th><th>RPG</th><th>APG</th><th>RPR</th><th>Tier</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table></div>
<footer><p>Data: ESPN public scoreboard and box scores. Code and full CSVs: <a href="{REPO}">RD3-LIL/nba-ratings on GitHub</a>. Data through {through} ({n_games} games). Built {BUILT}.</p></footer>
<script>
document.getElementById("q").addEventListener("input", function (e) {{
  var q = e.target.value.trim().toLowerCase();
  document.querySelectorAll("#players tbody tr").forEach(function (r) {{
    r.style.display = (!q || r.textContent.toLowerCase().indexOf(q) !== -1) ? "" : "none";
  }});
}});
</script>
</body>
</html>
"""


def main():
    through, n_games = bp.data_through()
    eval_doc = json.loads((DATA / "players_2026_27_eval.json").read_text())
    players = eval_doc["players"]
    gm_by_id = {}
    with open(DATA / "gm_board_2026_27.csv", newline="") as f:
        for row in csv.DictReader(f):
            gm_by_id[(row["athlete_id"], row["team"])] = row
    for p in players:
        p["gm"] = gm_by_id.get((p["athlete_id"], p["team"]), {})
    sharp_path = DATA / "sharp_2026_27.json"
    if sharp_path.exists():
        sharp_doc = json.loads(sharp_path.read_text())
        sharp_by_id = {(r["athlete_id"], r["team"]): r
                       for r in sharp_doc["players"]}
        for p in players:
            row = sharp_by_id.get((p["athlete_id"], p["team"]))
            p["sharp"] = row["sharp"] if row else None
            p["sharp_detail"] = row or {}
    else:
        for p in players:
            p["sharp"] = None
            p["sharp_detail"] = {}

    # League shooting averages, from the totals themselves.
    tot = {k: sum(p["totals"][k] for p in players)
           for k in ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "pts")}
    league = {
        "fg_pct": tot["fgm"] / tot["fga"] if tot["fga"] else None,
        "fg3_pct": tot["fg3m"] / tot["fg3a"] if tot["fg3a"] else None,
        "ft_pct": tot["ftm"] / tot["fta"] if tot["fta"] else None,
        "efg_pct": ((tot["fgm"] + 0.5 * tot["fg3m"]) / tot["fga"]
                    if tot["fga"] else None),
        "ts_pct": (tot["pts"] / (2 * (tot["fga"] + 0.44 * tot["fta"]))
                   if tot["fga"] + tot["fta"] else None),
    }

    # NBA ranks for the at-a-glance stats.
    ranks = {
        "rpr": _rank_map(players, lambda p: (p.get("ratings") or {}).get("rpr")),
        "pts_pg": _rank_map(players, lambda p: p["per_game"]["pts"]),
        "reb_pg": _rank_map(players, lambda p: p["per_game"]["reb"]),
        "ast_pg": _rank_map(players, lambda p: p["per_game"]["ast"]),
        "ortg_full": _rank_map(players, lambda p: p["eval"].get("ortg_full")),
        "ts_pct": _rank_map(players, lambda p: p["shooting"].get("ts_pct")),
        "sharp": _rank_map(players, lambda p: p.get("sharp")),
    }

    # Team Net ranks, from the solver output.
    teams_doc = json.loads((DATA / "teams_2026_27.json").read_text())
    team_net_rank = {}
    for i, name in enumerate(sorted(teams_doc["teams"],
                                    key=lambda n: teams_doc["teams"][n]["net"],
                                    reverse=True), 1):
        team_net_rank[name] = i

    logs = load_game_logs()
    by_team = defaultdict(list)
    for p in players:
        by_team[p["team"]].append(p)
    for team in by_team:
        by_team[team].sort(key=lambda p: p["totals"]["min"], reverse=True)
    by_athlete = defaultdict(list)
    for p in players:
        by_athlete[p["athlete_id"]].append(p)

    out_dir = HERE / "players"
    out_dir.mkdir(exist_ok=True)
    written = set()
    total = 0
    for p in players:
        slug = player_slug(p["name"], p["athlete_id"], p["team"])
        other = None
        for cand in by_athlete[p["athlete_id"]]:
            if cand["team"] != p["team"]:
                other = cand
        mates = [m for m in by_team[p["team"]] if m is not p]
        text = profile_html(
            p, ranks, league,
            logs.get((p["athlete_id"], p["team_id"]), []),
            mates, other, team_net_rank.get(p["team"], 0),
            through, n_games)
        path = out_dir / f"{slug}.html"
        path.write_text(text)
        written.add(path.name)
        total += len(text.encode("utf-8"))
    index_text = players_index_html(players, through, n_games)
    (out_dir / "index.html").write_text(index_text)
    written.add("index.html")
    total += len(index_text.encode("utf-8"))
    for stale in out_dir.glob("*.html"):
        if stale.name not in written:
            stale.unlink()
    print(f"Wrote {len(written)} player pages in players/: {total} bytes")
    print(f"Players: {len(players)}, game-log entries: "
          f"{sum(len(v) for v in logs.values())}, "
          f"league FG% {league['fg_pct']:.3f} 3P% {league['fg3_pct']:.3f} "
          f"TS% {league['ts_pct']:.3f}")
    # Spot-check the top of the board.
    top = sorted(players, key=lambda x: (x.get("ratings") or {}).get("rpr") or -99,
                 reverse=True)[:3]
    for p in top:
        print(f"  {p['name']} ({p['team']}) RPR "
              f"{(p.get('ratings') or {}).get('rpr'):.2f} "
              f"rank #{ranks['rpr'][0][(p['athlete_id'], p['team'])]} "
              f"PPG {p['per_game']['pts']:.1f} "
              f"rank #{ranks['pts_pg'][0][(p['athlete_id'], p['team'])]}")


if __name__ == "__main__":
    main()
