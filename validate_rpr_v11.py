"""Validate RPR v1.1 against v1, from the files on disk.

Reads data/players_2026_27.json (which carries both rpr and
rpr_v1), data/teams_2026_27.json, and the raw summaries for a
split-half check. Prints, plainly:

  1. Top 10 by RPR v1.1 vs v1, and the biggest movers with the
     reason (shrinkage vs opponent adjustment).
  2. Team aggregation: 5x minutes-weighted mean RPR per team vs
     team Adj Net, Pearson r, v1 vs v1.1. n = 30 teams, in-sample:
     this says whether the rating aggregates to team quality, not
     whether it predicts the future.
  3. Split-half reliability: each player's games split
     chronologically in half; correlation of first-half vs
     second-half ORtg (raw and shrunk, K = 200) and RPR (v1 and
     v1.1), plus MSE of first-half RPR predicting second-half
     RPR v1. Caveat: the shrinkage prior used here is the
     full-season league average, so the split-half numbers are
     slightly optimistic; the K choice itself was calibrated on
     the same split (see player_profiles.py docstring).
  4. Week-to-week rank churn: NOT computed here. It needs two
     snapshots a week apart; this project has one snapshot.
     Say so instead of faking it.

Stdlib only. No network. Run from the project directory:
    python3 validate_rpr_v11.py
"""
import json, math, statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
K_ORTG = 200.0
K_OPP = 500.0
STAT_NAMES = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO",
              "STL", "BLK", "OREB", "DREB", "PF", "+/-"]


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs)
                    * sum((y - my) ** 2 for y in ys))
    return num / den if den else float("nan")


def pi(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def ma(v):
    if not v or not isinstance(v, str) or "-" not in v:
        return (0, 0)
    m, _, a = v.partition("-")
    return (pi(m), pi(a))


def _game_poss(game):
    poss = []
    for side in ("home", "away"):
        s = (game or {}).get(f"{side}_stats") or {}
        try:
            poss.append(s["fga"] - s["orb"] + s["tov"] + 0.44 * s["fta"])
        except (KeyError, TypeError):
            return None
    if not poss or min(poss) <= 0:
        return None
    return sum(poss) / len(poss)


def main():
    players = json.loads((DATA / "players_2026_27.json").read_text())["players"]
    teams_doc = json.loads((DATA / "teams_2026_27.json").read_text())
    adj_net = {n: t["net"] for n, t in teams_doc["teams"].items()}
    adj_d = {n: t["adj_d"] for n, t in teams_doc["teams"].items()}
    league_adj_d = statistics.mean(adj_d.values())
    league_ortg = 100 * sum(p["totals"]["pts"] for p in players) / sum(
        p["totals"]["fga"] + 0.44 * p["totals"]["fta"] + p["totals"]["to"]
        for p in players)

    q = [p for p in players if (p["ratings"]["min_pct"] or 0) >= 40]
    print("== Top 10, RPR v1.1 vs v1 (40% minutes floor) ==")
    for label, key in (("v1.1", "rpr"), ("v1  ", "rpr_v1")):
        top = sorted(q, key=lambda p: p["ratings"][key] or -99,
                     reverse=True)[:10]
        print(f"  {label}: " + ", ".join(
            f"{p['name']} {p['ratings'][key]:.2f}" for p in top))
    print("\n== Biggest movers, v1 -> v1.1 ==")
    movers = sorted(
        q, key=lambda p: abs((p["ratings"]["rpr"] or 0)
                             - (p["ratings"]["rpr_v1"] or 0)),
        reverse=True)[:8]
    for p in movers:
        r = p["ratings"]
        print(f"  {p['name']} ({p['team']}): v1 {r['rpr_v1']:.2f} -> "
              f"v1.1 {r['rpr']:.2f}; ORtg {r['ortg']:.1f} -> shrunk "
              f"{r['ortg_shrunk']:.1f} -> adj {r['ortg_adj']:.1f} "
              f"(OppAdj {r['opp_adj']:+.2f})")
    oa = [p["ratings"]["opp_adj"] for p in players
          if p["ratings"]["opp_adj"] is not None]
    print(f"\nOppAdj across {len(oa)} players: min {min(oa):+.2f}, "
          f"max {max(oa):+.2f}, mean {statistics.mean(oa):+.3f}")

    print("\n== Team aggregation (5x minutes-weighted mean RPR vs Adj Net, "
          "n=30, in-sample) ==")
    for label, key in (("v1.1", "rpr"), ("v1  ", "rpr_v1")):
        xs, ys = [], []
        for team, net in adj_net.items():
            members = [p for p in players
                       if p["team"] == team
                       and p["ratings"][key] is not None]
            wsum = sum(p["totals"]["min"] for p in members)
            if not wsum:
                continue
            mean = sum(p["ratings"][key] * p["totals"]["min"]
                       for p in members) / wsum
            xs.append(5 * mean)
            ys.append(net)
        print(f"  {label}: r = {corr(xs, ys):.3f} (n={len(xs)})")

    # --- Split-half from raw summaries. ---
    game_map = {}
    for f in sorted(DATA.glob("games_*.json")):
        for g in json.loads(f.read_text()):
            game_map[str(g.get("event_id", ""))] = g
    for d in sorted((DATA / "parsed").glob("*")):
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                for g in json.loads(f.read_text()):
                    game_map.setdefault(str(g.get("event_id", "")), g)
    per_player = defaultdict(list)
    for raw_path in sorted((DATA / "raw" / "summary").glob("*.json")):
        g = game_map.get(raw_path.stem)
        if not g:
            continue
        s = json.loads(raw_path.read_text())
        entries = s.get("boxscore", {}).get("players", []) or []
        gposs = _game_poss(g)
        for te in entries:
            opp = next(((o.get("team", {}) or {}).get("displayName", ""))
                       for o in entries if o is not te)
            team_min = 0
            for grp in te.get("statistics", []) or []:
                names = grp.get("names", []) or STAT_NAMES
                idx = {n: i for i, n in enumerate(names)}
                for row in grp.get("athletes", []) or []:
                    if row.get("didNotPlay") or not row.get("stats"):
                        continue
                    i = idx.get("MIN")
                    if i is not None and i < len(row["stats"]):
                        team_min += pi(row["stats"][i])
            for grp in te.get("statistics", []) or []:
                names = grp.get("names", []) or STAT_NAMES
                idx = {n: i for i, n in enumerate(names)}
                for row in grp.get("athletes", []) or []:
                    if row.get("didNotPlay") or not row.get("stats"):
                        continue
                    st = row["stats"]
                    aid = str((row.get("athlete", {}) or {}).get("id", ""))
                    if not aid:
                        continue
                    minutes = pi(st[idx["MIN"]]) if "MIN" in idx else 0
                    fgm, fga = ma(st[idx["FG"]]) if "FG" in idx else (0, 0)
                    ftm, fta = ma(st[idx["FT"]]) if "FT" in idx else (0, 0)
                    pf = (gposs * minutes / team_min
                          if gposs and team_min and minutes else 0.0)
                    per_player[aid].append({
                        "date": g["date"],
                        "name": (row.get("athlete", {}) or {}).get(
                            "displayName", ""),
                        "pts": pi(st[idx["PTS"]]) if "PTS" in idx else 0,
                        "fga": fga, "fta": fta,
                        "to": pi(st[idx["TO"]]) if "TO" in idx else 0,
                        "ast": pi(st[idx["AST"]]) if "AST" in idx else 0,
                        "stl": pi(st[idx["STL"]]) if "STL" in idx else 0,
                        "blk": pi(st[idx["BLK"]]) if "BLK" in idx else 0,
                        "reb": pi(st[idx["REB"]]) if "REB" in idx else 0,
                        "opp": opp, "poss_faced": pf,
                    })

    def half_stats(games):
        pu = sum(x["fga"] + 0.44 * x["fta"] + x["to"] for x in games)
        pts = sum(x["pts"] for x in games)
        ortg = 100 * pts / pu if pu else None
        gp = len(games)
        rpr = None
        if ortg is not None and gp:
            rpr = ((ortg - 100) * (pu / gp) / 100
                   + 0.5 * (sum(x["ast"] for x in games) / gp
                            - sum(x["to"] for x in games) / gp)
                   + (sum(x["stl"] for x in games)
                      + sum(x["blk"] for x in games)) / gp
                   + 0.25 * sum(x["reb"] for x in games) / gp)
        return ortg, pu, rpr, gp

    def half_v11(games):
        ortg, pu, _rpr, gp = half_stats(games)
        if ortg is None:
            return None, None
        shrunk = (pu * ortg + K_ORTG * league_ortg) / (pu + K_ORTG)
        pf = sum(x["poss_faced"] for x in games)
        if pf:
            raw = sum((adj_d.get(x["opp"], league_adj_d) - league_adj_d)
                      * x["poss_faced"] for x in games) / pf
            opp_adj = raw * pf / (pf + K_OPP)
        else:
            opp_adj = 0.0
        adj = shrunk - opp_adj
        rpr = ((adj - 100) * (pu / gp) / 100
               + 0.5 * (sum(x["ast"] for x in games) / gp
                        - sum(x["to"] for x in games) / gp)
               + (sum(x["stl"] for x in games)
                  + sum(x["blk"] for x in games)) / gp
               + 0.25 * sum(x["reb"] for x in games) / gp)
        return adj, rpr

    rows = []
    for aid, games in per_player.items():
        games = sorted(games, key=lambda x: x["date"])
        if len(games) < 8:
            continue
        mid = len(games) // 2
        h1, h2 = games[:mid], games[mid:]
        o1, pu1, r1, _ = half_stats(h1)
        o2, pu2, r2, _ = half_stats(h2)
        if o1 is None or o2 is None or pu1 < 40 or pu2 < 40:
            continue
        adj1, r11_1 = half_v11(h1)
        adj2, r11_2 = half_v11(h2)
        rows.append((o1, pu1, r1, o2, r2, adj1, r11_1, adj2, r11_2))
    print(f"\n== Split-half (n={len(rows)} players, >=4 games and >=40 "
          f"poss used per half) ==")
    o1s = [r[0] for r in rows]; o2s = [r[3] for r in rows]
    print(f"  ORtg raw split-half r: {corr(o1s, o2s):.3f}")
    a1s = [r[5] for r in rows]; a2s = [r[7] for r in rows]
    print(f"  ORtg v1.1 (shrunk+opp-adj) split-half r: "
          f"{corr(a1s, a2s):.3f}")
    r1s = [r[2] for r in rows]; r2s = [r[4] for r in rows]
    print(f"  RPR v1 split-half r: {corr(r1s, r2s):.3f}")
    v1s = [r[6] for r in rows]; v2s = [r[8] for r in rows]
    print(f"  RPR v1.1 split-half r: {corr(v1s, v2s):.3f}")
    mse_v1 = statistics.mean((a - b) ** 2 for a, b in zip(r1s, r2s))
    mse_v11 = statistics.mean((a - b) ** 2 for a, b in zip(v1s, r2s))
    print(f"  MSE predicting 2nd-half RPR v1: from 1st-half v1 "
          f"{mse_v1:.2f}, from 1st-half v1.1 {mse_v11:.2f}")
    print("\nWeek-to-week rank churn: not computed. It needs two "
          "snapshots a week apart; this project has one snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
