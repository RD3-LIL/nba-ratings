"""Deeper player evaluation layer for NBA ratings (v1, ESPN box-score only).

Reads data/players_2026_27.json (base profiles: ORtg approx, %Poss, RPR)
plus data/games_2026_27.json and data/raw/summary/*.json (game logs),
and adds per player:

  - ORtg_full: Dean Oliver / Basketball-Reference style offensive rating
    in points form (see _oliver_ortg): points produced = PTS minus
    the FG credit transferred to passers (qAST) plus assist credit
    (2 x BR ASTPart) plus OREB credit, over possessions used minus
    OREBs extended. Kept alongside the simplified ORtg
    (100*PTS/poss used, no assist credit) so the difference is
    visible. What is STILL missing vs a true Oliver ORtg: no
    play-by-play, no opponent adjustment, team factors are season
    aggregates. It is a full-ish box-score Oliver, not the
    proprietary KenPom formula and not a tracking stat.
  - Shooting profile: TS%, eFG%, FG/3P/FT (from base) plus points
    breakdown: points from 2s, 3s and FTs and their shares.
  - Playmaking: AST/TO ratio, AST per 100 possessions used, AST per
    100 team possessions (assist-rate proxy).
  - Defense/rebounding: STL+BLK per game and per 36, REB per 36
    split OREB/DREB, PF per 36.
  - Volume vs efficiency quadrant (%Poss vs ORtg_full) and the RPR
    component breakdown (scoring, playmaking, defense, rebounding
    terms, which sum to RPR) so each RPR can be explained.
  - Per-36 for all counting stats.
  - Consistency from game logs (never invented): std dev of points
    per game, games with 20+ / 30+ points.
  - Minutes/durability: GP, %Min, MPG.
  - Trending: last-7-days PPG vs season PPG (last 7 days = dates
    >= season last date - 6 days), with games in the window.

Stdlib only. No network calls. No stats.nba.com / nba_api in v1.

Usage:
    python3 player_eval.py
        Writes data/players_2026_27_eval.json and
        data/players_2026_27_eval.csv, prints top 10s per lens.
    python3 player_eval.py --top ortg_full --limit 10
"""
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"
MIN_PCT_FLOOR = 40.0


def _parse_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _parse_ma(v):
    if not v or not isinstance(v, str) or "-" not in v:
        return (0, 0)
    m, _, a = v.partition("-")
    return (_parse_int(m) or 0, _parse_int(a) or 0)


def load_base():
    payload = json.loads((DATA / "players_2026_27.json").read_text())
    return payload["players"], payload.get("games_included", 0)


def load_game_map():
    games = json.loads((DATA / "games_2026_27.json").read_text())
    return {str(g["event_id"]): g for g in games}


def team_totals_from_profiles(profiles):
    """Team season totals summed from player profiles (box-score only)."""
    teams = {}
    for p in profiles:
        t = teams.setdefault(p["team"], {
            "min": 0, "pts": 0, "fgm": 0, "fga": 0, "fg3m": 0,
            "ftm": 0, "fta": 0, "ast": 0, "to": 0, "oreb": 0,
            "games": 0})
        tot = p["totals"]
        for k in ("min", "pts", "fgm", "fga", "fg3m", "ftm", "fta",
                  "ast", "to", "oreb"):
            t[k] += tot[k]
    # games per team from the consolidated games file
    games = json.loads((DATA / "games_2026_27.json").read_text())
    for g in games:
        for side in ("home", "away"):
            teams.setdefault(g[side], {"min": 0, "pts": 0, "fgm": 0,
                                       "fga": 0, "fg3m": 0, "ftm": 0,
                                       "fta": 0, "ast": 0, "to": 0,
                                       "oreb": 0, "games": 0})
            teams[g[side]]["games"] += 1
    for t in teams.values():
        t["poss"] = t["fga"] - t["oreb"] + t["to"] + 0.44 * t["fta"]
    return teams


def team_oreb_pct_from_summaries(game_map):
    """Team OREB% = team OREB / (team OREB + opponent DREB).

    Opponent DREB comes from the raw summary boxscore team stats
    (defensiveRebounds). Falls back to 0.27 (rough league average)
    for a team with no usable data.
    """
    oreb = defaultdict(int)
    opp_dreb = defaultdict(int)
    for eid, g in game_map.items():
        path = DATA / "raw" / "summary" / f"{eid}.json"
        if not path.exists():
            continue
        try:
            s = json.loads(path.read_text())
        except ValueError:
            continue
        box = s.get("boxscore", {}).get("teams", [])
        if len(box) != 2:
            continue
        stats = []
        for bt in box:
            d = {x["name"]: x["displayValue"]
                 for x in bt.get("statistics", [])}
            try:
                stats.append((bt["team"]["displayName"],
                              int(float(d.get("offensiveRebounds", 0))),
                              int(float(d.get("defensiveRebounds", 0)))))
            except ValueError:
                continue
        if len(stats) != 2:
            continue
        (n1, o1, dr1), (n2, o2, dr2) = stats
        oreb[n1] += o1
        opp_dreb[n1] += dr2
        oreb[n2] += o2
        opp_dreb[n2] += dr1
    out = {}
    for name in set(list(oreb) + list(opp_dreb)):
        den = oreb[name] + opp_dreb[name]
        out[name] = (oreb[name] / den) if den else 0.27
    return out


def _oliver_ortg(p, tm, tm_oreb_pct):
    """Dean Oliver style ORtg, points-based, from box scores.

    The Basketball-Reference Oliver parts are used in points form:
      qAST = (MIN/(TmMIN/5)) * 1.14 * ((TmAST-AST)/TmFGM), capped [0, 1]
      FG debit  = FGM * ((PTS-FTM)/(2*FGA)) * qAST
                  (points of FG credit transferred to the passers)
      AST credit = (((TmPTS-TmFTM)-(PTS-FTM)) / (2*(TmFGA-FGA))) * AST
                  (2 * BR's ASTPart: each assist creates about a
                  2-point scoring possession, shared with the scorer)
      OREB credit = OREB * TmOREB% * (TmPTS/TmPoss)
    Points produced = PTS - FG debit + AST credit + OREB credit.
    Possessions = FGA + 0.44*FTA + TO, the same denominator as the
    simplified ORtg, so the two are directly comparable. (An OREB
    is credited in points only; subtracting it from possessions
    as well would double-count it.) ORtg_full = 100 * points
    produced / possessions.
    Conservation check: summed over a team, FG debit roughly equals
    AST credit (assists are transfers, not new points), so the team
    average stays near the simplified ORtg; playmakers move up and
    pure finishers move down a bit, which is the point. Still
    missing vs a true Oliver ORtg: no play-by-play, no opponent
    adjustment, team factors are season aggregates.
    Returns (pts, fg_debit, ast_credit_raw, oreb_credit, poss) or
    (None, None, None, None, None). The caller scales ast_credit
    per team (see evaluate) and then ORtg_full =
    100 * (PTS - FG debit + scaled AST credit + OREB credit) / poss.
    """
    tot = p["totals"]
    fgm, fga = tot["fgm"], tot["fga"]
    ftm, fta = tot["ftm"], tot["fta"]
    pts, ast, oreb, to = tot["pts"], tot["ast"], tot["oreb"], tot["to"]
    minutes = tot["min"]
    if not fga and not fta and not to:
        return None, None, None, None, None
    tm_min = tm["min"] or 1
    tm_fgm = tm["fgm"] or 1
    tm_fga = tm["fga"] or 1
    qast = (minutes / (tm_min / 5.0)) * 1.14 * ((tm["ast"] - ast) / tm_fgm)
    qast = max(0.0, min(1.0, qast))
    fg_debit = 0.0
    if fga:
        fg_debit = fgm * ((pts - ftm) / (2 * fga)) * qast
    denom_ast = 2 * (tm_fga - fga)
    ast_credit = 0.0
    if denom_ast > 0:
        ast_credit = (((tm["pts"] - tm["ftm"]) - (pts - ftm)) / denom_ast) * ast
    tm_poss = tm["poss"] or 1
    oreb_credit = oreb * tm_oreb_pct * (tm["pts"] / tm_poss)
    poss = fga + 0.44 * fta + to
    if poss <= 0:
        return None, None, None, None, None
    # Caller calibrates: AST credit is scaled per team so total
    # assist credit equals total FG debit (assists transfer points,
    # they do not create them). Return raw parts for that step.
    return pts, fg_debit, ast_credit, oreb_credit, poss


def build_game_logs(game_map):
    """Per (athlete_id, team_id) game logs from raw summaries.

    Each entry: list of dicts {date, pts, min, ast, reb, stl, blk, to}.
    Date comes from the parsed games file, never the raw header.
    """
    logs = defaultdict(list)
    for eid, g in game_map.items():
        path = DATA / "raw" / "summary" / f"{eid}.json"
        if not path.exists():
            continue
        try:
            s = json.loads(path.read_text())
        except ValueError:
            continue
        for team_entry in s.get("boxscore", {}).get("players", []):
            team = team_entry.get("team", {}) or {}
            team_id = str(team.get("id", ""))
            for group in team_entry.get("statistics", []) or []:
                names = group.get("names", []) or []
                idx = {n: i for i, n in enumerate(names)}
                for row in group.get("athletes", []) or []:
                    if row.get("didNotPlay"):
                        continue
                    stats = row.get("stats") or []
                    if not stats:
                        continue
                    athlete = row.get("athlete", {}) or {}
                    aid = str(athlete.get("id", ""))
                    if not aid:
                        continue
                    def get(name):
                        i = idx.get(name)
                        return stats[i] if i is not None and i < len(stats) else None
                    logs[(aid, team_id)].append({
                        "date": g["date"],
                        "pts": _parse_int(get("PTS")) or 0,
                        "min": _parse_int(get("MIN")) or 0,
                        "ast": _parse_int(get("AST")) or 0,
                        "reb": _parse_int(get("REB")) or 0,
                        "stl": _parse_int(get("STL")) or 0,
                        "blk": _parse_int(get("BLK")) or 0,
                        "to": _parse_int(get("TO")) or 0,
                    })
    return logs


def quadrant(poss_pct, ortg):
    if poss_pct is None or ortg is None:
        return None
    if poss_pct >= 25 and ortg >= 115:
        return "high usage, efficient"
    if poss_pct >= 25:
        return "high usage, inefficient"
    if ortg >= 115:
        return "low usage, efficient"
    return "low usage, inefficient"


def evaluate(profiles, game_map):
    teams = team_totals_from_profiles(profiles)
    oreb_pct = team_oreb_pct_from_summaries(game_map)
    logs = build_game_logs(game_map)
    # season last played date, for the last-7-days window
    all_dates = sorted({g["date"] for g in game_map.values()})
    last_date = date(int(all_dates[-1][:4]), int(all_dates[-1][4:6]),
                     int(all_dates[-1][6:8])) if all_dates else None
    window_start = (last_date - timedelta(days=6)).strftime("%Y%m%d") if last_date else None

    # Pass 1: raw Oliver parts per player, then calibrate AST credit
    # per team so total assist credit equals total FG debit.
    raw_parts = {}
    for p in profiles:
        tm = teams.get(p["team"])
        if tm is None:
            continue
        raw_parts[(p["athlete_id"], p["team_id"])] = _oliver_ortg(
            p, tm, oreb_pct.get(p["team"], 0.27))
    debit_sum = {}
    credit_sum = {}
    for (aid, tid), parts in raw_parts.items():
        if parts[0] is None:
            continue
        team = next((pp["team"] for pp in profiles
                     if pp["athlete_id"] == aid and pp["team_id"] == tid), None)
        if team is None:
            continue
        debit_sum[team] = debit_sum.get(team, 0.0) + parts[1]
        credit_sum[team] = credit_sum.get(team, 0.0) + parts[2]
    ast_scale = {team: (debit_sum[team] / credit_sum[team]
                        if credit_sum.get(team) else 0.0)
                 for team in debit_sum}

    out = []
    for p in profiles:
        tot = p["totals"]
        pg = p["per_game"]
        p36 = p["per_36"]
        ratings = p.get("ratings") or {}
        ortg_simple = ratings.get("ortg")
        poss_pct = ratings.get("poss_pct")
        min_pct = ratings.get("min_pct")
        rpr = ratings.get("rpr")
        tm = teams.get(p["team"], {"min": 1, "pts": 0, "fgm": 1, "fga": 1,
                                   "ftm": 0, "fta": 0, "ast": 0, "to": 0,
                                   "oreb": 0, "poss": 1})
        _pts, _debit, _credit_raw, _oreb_cr, poss_oliver = raw_parts.get(
            (p["athlete_id"], p["team_id"]),
            (None, None, None, None, None))
        if _pts is None:
            ortg_full, pprod = None, None
        else:
            pprod = (_pts - _debit
                     + _credit_raw * ast_scale.get(p["team"], 0.0) + _oreb_cr)
            ortg_full = 100 * pprod / poss_oliver if poss_oliver else None
        gp = tot["gp"]
        poss_used = tot["fga"] + 0.44 * tot["fta"] + tot["to"]
        poss_pg = poss_used / gp if gp else 0.0
        # RPR component breakdown (sums to RPR)
        scoring = ((ortg_simple - 100) * poss_pg / 100
                   if ortg_simple is not None else None)
        playmaking = 0.5 * (pg["ast"] - pg["to"])
        defense = pg["stl"] + pg["blk"]
        rebounding = 0.25 * pg["reb"]
        # Shooting points breakdown
        pts_2s = 2 * (tot["fgm"] - tot["fg3m"])
        pts_3s = 3 * tot["fg3m"]
        pts_ft = tot["ftm"]
        # Game logs for this player/team
        gl = logs.get((p["athlete_id"], p["team_id"]), [])
        pts_list = [x["pts"] for x in gl]
        pts_std = (statistics.stdev(pts_list) if len(pts_list) > 1 else 0.0)
        last7 = [x for x in gl if window_start and x["date"] >= window_start]
        last7_ppg = (sum(x["pts"] for x in last7) / len(last7)
                     if last7 else None)
        ev = dict(p)
        ev["eval"] = {
            "ortg_full": ortg_full,
            "ortg_simple": ortg_simple,
            "ortg_diff": ((ortg_full - ortg_simple)
                          if ortg_full is not None and ortg_simple is not None
                          else None),
            "points_produced_oliver": pprod,
            "possessions_oliver": poss_oliver,
            "points_breakdown": {
                "pts_2s": pts_2s, "pts_3s": pts_3s, "pts_ft": pts_ft,
                "share_2s": (pts_2s / tot["pts"] if tot["pts"] else None),
                "share_3s": (pts_3s / tot["pts"] if tot["pts"] else None),
                "share_ft": (pts_ft / tot["pts"] if tot["pts"] else None),
            },
            "playmaking": {
                "ast_to_ratio": (pg["ast"] / pg["to"] if pg["to"] else None),
                "ast_per_100_poss_used": (100 * tot["ast"] / poss_used
                                          if poss_used else None),
                "ast_per_100_team_poss": (100 * tot["ast"] / tm["poss"]
                                          if tm["poss"] else None),
            },
            "defense_rebounding": {
                "stl_blk_pg": pg["stl"] + pg["blk"],
                "stl_blk_per36": p36["stl"] + p36["blk"],
                "reb_per36": p36["reb"],
                "oreb_per36": p36["oreb"],
                "dreb_per36": p36["dreb"],
                "pf_per36": p36["pf"],
            },
            "quadrant": quadrant(poss_pct, ortg_full if ortg_full is not None else ortg_simple),
            "rpr_components": {
                "scoring": scoring, "playmaking": playmaking,
                "defense": defense, "rebounding": rebounding,
            },
            "per36": {k: p36[k] for k in
                      ("pts", "reb", "oreb", "dreb", "ast", "to", "stl",
                       "blk", "pf")},
            "consistency": {
                "pts_std": pts_std,
                "pts_cv": (pts_std / pg["pts"] if pg["pts"] else None),
                "games_20": sum(1 for x in pts_list if x >= 20),
                "games_30": sum(1 for x in pts_list if x >= 30),
                "games_logged": len(gl),
            },
            "minutes": {
                "gp": gp, "min_pct": min_pct,
                "mpg": pg["min"],
            },
            "trending": {
                "window": f"{window_start}..{all_dates[-1]}" if all_dates else None,
                "last7_gp": len(last7),
                "last7_ppg": last7_ppg,
                "trend_ppg": ((last7_ppg - pg["pts"])
                              if last7_ppg is not None else None),
            },
        }
        out.append(ev)
    return out, {"last_date": all_dates[-1] if all_dates else None,
                 "window_start": window_start}


def _r(v):
    return round(v, 2) if isinstance(v, (int, float)) else v


def write_csv(profiles, out_path):
    fields = ["athlete_id", "name", "team", "position", "gp", "min_pct",
              "mpg", "pts_pg", "reb_pg", "ast_pg", "stl_blk_pg",
              "ortg_simple", "ortg_full", "ortg_diff", "poss_pct", "rpr",
              "rpr_scoring", "rpr_playmaking", "rpr_defense",
              "rpr_rebounding", "quadrant", "ts_pct", "efg_pct",
              "fg3_pct", "pts_2s_share", "pts_3s_share", "pts_ft_share",
              "ast_to_ratio", "ast_per_100_poss", "stl_blk_per36",
              "reb_per36", "oreb_per36", "dreb_per36", "pf_per36",
              "pts_per36", "ast_per36", "pts_std", "games_20", "games_30",
              "last7_gp", "last7_ppg", "trend_ppg"]
    with open(out_path, "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for p in profiles:
            ev = p["eval"]
            tot = p["totals"]
            pg = p["per_game"]
            r = p.get("ratings") or {}
            def f(v):
                return "" if v is None else round(v, 2)
            w.writerow({
                "athlete_id": p["athlete_id"], "name": p["name"],
                "team": p["team"], "position": p["position"],
                "gp": tot["gp"], "min_pct": f(ev["minutes"]["min_pct"]),
                "mpg": f(pg["min"]), "pts_pg": f(pg["pts"]),
                "reb_pg": f(pg["reb"]), "ast_pg": f(pg["ast"]),
                "stl_blk_pg": f(ev["defense_rebounding"]["stl_blk_pg"]),
                "ortg_simple": f(ev["ortg_simple"]),
                "ortg_full": f(ev["ortg_full"]),
                "ortg_diff": f(ev["ortg_diff"]),
                "poss_pct": f(r.get("poss_pct")), "rpr": f(r.get("rpr")),
                "rpr_scoring": f(ev["rpr_components"]["scoring"]),
                "rpr_playmaking": f(ev["rpr_components"]["playmaking"]),
                "rpr_defense": f(ev["rpr_components"]["defense"]),
                "rpr_rebounding": f(ev["rpr_components"]["rebounding"]),
                "quadrant": ev["quadrant"],
                "ts_pct": f(p["shooting"]["ts_pct"]),
                "efg_pct": f(p["shooting"]["efg_pct"]),
                "fg3_pct": f(p["shooting"]["fg3_pct"]),
                "pts_2s_share": f(ev["points_breakdown"]["share_2s"]),
                "pts_3s_share": f(ev["points_breakdown"]["share_3s"]),
                "pts_ft_share": f(ev["points_breakdown"]["share_ft"]),
                "ast_to_ratio": f(ev["playmaking"]["ast_to_ratio"]),
                "ast_per_100_poss": f(ev["playmaking"]["ast_per_100_poss_used"]),
                "stl_blk_per36": f(ev["defense_rebounding"]["stl_blk_per36"]),
                "reb_per36": f(ev["defense_rebounding"]["reb_per36"]),
                "oreb_per36": f(ev["defense_rebounding"]["oreb_per36"]),
                "dreb_per36": f(ev["defense_rebounding"]["dreb_per36"]),
                "pf_per36": f(ev["defense_rebounding"]["pf_per36"]),
                "pts_per36": f(ev["per36"]["pts"]),
                "ast_per36": f(ev["per36"]["ast"]),
                "pts_std": f(ev["consistency"]["pts_std"]),
                "games_20": ev["consistency"]["games_20"],
                "games_30": ev["consistency"]["games_30"],
                "last7_gp": ev["trending"]["last7_gp"],
                "last7_ppg": f(ev["trending"]["last7_ppg"]),
                "trend_ppg": f(ev["trending"]["trend_ppg"]),
            })
    return out_path


def top10(profiles, key, min_pct=MIN_PCT_FLOOR, reverse=True):
    sel = [p for p in profiles
           if (p.get("ratings") or {}).get("min_pct") is not None
           and p["ratings"]["min_pct"] >= min_pct
           and key(p) is not None]
    sel.sort(key=key, reverse=reverse)
    return sel[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", default=None)
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    profiles, games_included = load_base()
    game_map = load_game_map()
    ev_profiles, meta = evaluate(profiles, game_map)
    payload = {"season": SEASON, "games_included": games_included,
               "player_count": len(ev_profiles),
               "window": meta,
               "notes": ("Eval layer on base profiles. ortg_full is a "
                         "Dean Oliver / Basketball-Reference style ORtg "
                         "in points form (see player_eval.py _oliver_ortg), "
                         "box-score only; no play-by-play, no opponent "
                         "adjustment. "
                         "ortg_simple is the base simplified ORtg. "
                         "Trending window is the last 7 days of games "
                         "in the data."),
               "players": ev_profiles}
    (DATA / "players_2026_27_eval.json").write_text(json.dumps(payload))
    write_csv(ev_profiles, DATA / "players_2026_27_eval.csv")
    print(f"Wrote data/players_2026_27_eval.json + .csv: "
          f"{len(ev_profiles)} players, window {meta['window_start']}.."
          f"{meta['last_date']}")
    if args.top:
        return 0
    # Top 10s per lens (40% minutes floor unless noted)
    lenses = [
        ("Most efficient high-usage (ORtg_full, %Poss>=25)",
         lambda p: p["eval"]["ortg_full"] if (p.get("ratings") or {}).get("poss_pct", 0) >= 25 else None),
        ("Best playmakers (AST per 100 poss used)",
         lambda p: p["eval"]["playmaking"]["ast_per_100_poss_used"]),
        ("Best defenders (STL+BLK per 36)",
         lambda p: p["eval"]["defense_rebounding"]["stl_blk_per36"]),
        ("Best rebounders (REB per 36)",
         lambda p: p["eval"]["defense_rebounding"]["reb_per36"]),
        ("Trending up (last-7-days PPG minus season PPG, min 3 games in window)",
         lambda p: p["eval"]["trending"]["trend_ppg"] if p["eval"]["trending"]["last7_gp"] >= 3 else None),
        ("Biggest ORtg lift from assist credit (ORtg_full minus ORtg_simple)",
         lambda p: p["eval"]["ortg_diff"]),
    ]
    for title, key in lenses:
        print(f"\n{title}:")
        for p in top10(ev_profiles, key):
            ev = p["eval"]
            print(f"  {p['name']} ({p['team']}) GP {p['totals']['gp']} "
                  f"PTS {p['per_game']['pts']:.1f} ORtg {ev['ortg_simple'] and round(ev['ortg_simple'], 1)}"
                  f" -> full {ev['ortg_full'] and round(ev['ortg_full'], 1)} "
                  f"%Poss {p['ratings']['poss_pct'] and round(p['ratings']['poss_pct'], 1)} "
                  f"RPR {p['ratings']['rpr'] and round(p['ratings']['rpr'], 2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
