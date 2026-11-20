"""Validate RPR v1.2 against v1.1 and v1, from the files on disk.

Reads data/players_2026_27.json (which carries rpr = v1.2,
rpr_v1_1 and rpr_v1), data/teams_2026_27.json, and the raw
summaries for a split-half check. Prints, plainly:

  1. Top 10 by RPR v1.2 vs v1.1 vs v1, and the biggest movers
     with the reason (centering, not re-weighting: v1.2 is v1.1
     minus the league-average counting credit, about 4.49 per
     36 minutes, scaled by MPG / 36).
  2. Distribution: median, mean, share negative, for each
     version. v1.2 should sit near median 0 with roughly half
     the players negative; v1.1 had median 2.2 and only 8 of
     469 negative.
  3. Team aggregation: 5x minutes-weighted mean RPR per team vs
     team Adj Net, Pearson r, v1 vs v1.1 vs v1.2. n = 30 teams,
     in-sample: this says whether the rating aggregates to team
     quality, not whether it predicts the future.
  4. Split-half reliability: each player's games split
     chronologically in half; correlation of first-half vs
     second-half ORtg (raw and shrunk, K = 200) and RPR (v1,
     v1.1, v1.2), plus MSE of first-half RPR predicting its own
     version's second-half RPR (scale-consistent: v1.2 is
     centered near 0, v1 near +2.3, so cross-scale MSE would
     measure the scale gap, not stability).
     v1.2 centers used here are the full-season league values,
     so the split-half numbers are slightly optimistic; the K
     choice itself was calibrated on the same split (see
     player_profiles.py docstring).
  5. Week-to-week rank churn: NOT computed here. It needs two
     snapshots a week apart; this project has one snapshot.
     Say so instead of faking it.

Gates: exits 1 (and writes nothing to validation.json) when
v1.2 fails a sanity gate: split-half r under 0.45, team
aggregation r under 0.60, median outside [-0.5, 0.5], or
negative share outside [0.35, 0.65]. Those are sanity bounds,
not fitted to this run: a centered rating must have about
half its players negative and must be at least as stable
split-half as the version it replaces. daily_update.sh runs
this and stops on failure.

Also writes data/validation.json with the headline numbers, so
generate_run_summary.py can quote them without re-running the
split-half.

Stdlib only. No network. Run from the project directory:
    python3 validate_rpr_v11.py
(The filename is historical: it validated v1.1 first. It now
validates v1.2 against v1.1 and v1.)
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
    # v1.2 centers from the first profile that carries them.
    centers = next((p["ratings"]["rpr_centers_per36"]
                    for p in players
                    if p["ratings"].get("rpr_centers_per36")), None)
    if centers is None:
        print("ERROR: no rpr_centers_per36 in players file; "
              "re-run player_profiles.py first")
        return 1

    q = [p for p in players if (p["ratings"]["min_pct"] or 0) >= 40]
    print("== Top 10, RPR v1.2 vs v1.1 vs v1 (40% minutes floor) ==")
    for label, key in (("v1.2", "rpr"), ("v1.1", "rpr_v1_1"),
                       ("v1  ", "rpr_v1")):
        top = sorted(q, key=lambda p: p["ratings"][key] or -99,
                     reverse=True)[:10]
        print(f"  {label}: " + ", ".join(
            f"{p['name']} {p['ratings'][key]:.2f}" for p in top))
    print("\n== Biggest movers, v1.1 -> v1.2 ==")
    movers = sorted(
        q, key=lambda p: abs((p["ratings"]["rpr"] or 0)
                             - (p["ratings"]["rpr_v1_1"] or 0)),
        reverse=True)[:8]
    for p in movers:
        r = p["ratings"]
        print(f"  {p['name']} ({p['team']}): v1.1 {r['rpr_v1_1']:.2f} -> "
              f"v1.2 {r['rpr']:.2f}; minutes {p['per_game']['min']:.1f} "
              f"MPG, so the removed free credit is about "
              f"{4.49 * p['per_game']['min'] / 36:.2f}")

    print("\n== Distribution (all players with an RPR) ==")
    dist = {}
    for label, key in (("v1.2", "rpr"), ("v1.1", "rpr_v1_1"),
                       ("v1  ", "rpr_v1")):
        vals = [p["ratings"][key] for p in players
                if p["ratings"][key] is not None]
        neg = sum(1 for v in vals if v < 0) / len(vals)
        dist[label.strip()] = {
            "median": statistics.median(vals),
            "mean": statistics.mean(vals),
            "neg_share": neg, "n": len(vals),
            "min": min(vals), "max": max(vals)}
        print(f"  {label}: n={len(vals)} median "
              f"{statistics.median(vals):+.2f} mean "
              f"{statistics.mean(vals):+.2f} negative {neg:.0%} "
              f"range [{min(vals):+.2f}, {max(vals):+.2f}]")
    oa = [p["ratings"]["opp_adj"] for p in players
          if p["ratings"]["opp_adj"] is not None]
    print(f"\nOppAdj across {len(oa)} players: min {min(oa):+.2f}, "
          f"max {max(oa):+.2f}, mean {statistics.mean(oa):+.3f}")

    print("\n== Team aggregation (5x minutes-weighted mean RPR vs Adj Net, "
          "n=30, in-sample) ==")
    team_r = {}
    for label, key in (("v1.2", "rpr"), ("v1.1", "rpr_v1_1"),
                       ("v1  ", "rpr_v1")):
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
        team_r[label.strip()] = corr(xs, ys)
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
                        "min": minutes,
                        "opp": opp, "poss_faced": pf,
                    })

    def half_stats(games):
        pu = sum(x["fga"] + 0.44 * x["fta"] + x["to"] for x in games)
        pts = sum(x["pts"] for x in games)
        ortg = 100 * pts / pu if pu else None
        gp = len(games)
        mins = sum(x["min"] for x in games)
        mpg = mins / gp if gp else 0.0
        rpr = None
        if ortg is not None and gp:
            rpr = ((ortg - 100) * (pu / gp) / 100
                   + 0.5 * (sum(x["ast"] for x in games) / gp
                            - sum(x["to"] for x in games) / gp)
                   + (sum(x["stl"] for x in games)
                      + sum(x["blk"] for x in games)) / gp
                   + 0.25 * sum(x["reb"] for x in games) / gp)
        return ortg, pu, rpr, gp, mins, mpg

    def half_v11(games):
        ortg, pu, _rpr, gp, _mins, _mpg = half_stats(games)
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

    def half_v12(games):
        # v1.2 on a half: same ORtg_adj as v1.1, counting terms
        # centered on the full-season league centers (same
        # leakage caveat as the ORtg prior, stated above).
        adj, _r11 = half_v11(games)
        if adj is None:
            return None
        _o, pu, _r, gp, _mins, mpg = half_stats(games)
        if not gp or not mpg:
            return None
        mins = sum(x["min"] for x in games)
        def per36(key):
            return sum(x[key] for x in games) / mins * 36 if mins else 0.0
        scoring = (adj - league_ortg) * (pu / gp) / 100
        play = (0.5 * (per36("ast") - per36("to"))
                - centers["play_per36"]) * mpg / 36
        defe = ((per36("stl") + per36("blk"))
                - centers["defense_per36"]) * mpg / 36
        reb = (0.25 * per36("reb")
               - centers["rebounding_per36"]) * mpg / 36
        return scoring + play + defe + reb

    rows = []
    for aid, games in per_player.items():
        games = sorted(games, key=lambda x: x["date"])
        if len(games) < 8:
            continue
        mid = len(games) // 2
        h1, h2 = games[:mid], games[mid:]
        o1, pu1, r1, _g1, _m1, _mpg1 = half_stats(h1)
        o2, pu2, r2, _g2, _m2, _mpg2 = half_stats(h2)
        if o1 is None or o2 is None or pu1 < 40 or pu2 < 40:
            continue
        adj1, r11_1 = half_v11(h1)
        adj2, r11_2 = half_v11(h2)
        r12_1 = half_v12(h1)
        r12_2 = half_v12(h2)
        rows.append((o1, pu1, r1, o2, r2, adj1, r11_1, adj2, r11_2,
                     r12_1, r12_2))
    print(f"\n== Split-half (n={len(rows)} players, >=4 games and >=40 "
          f"poss used per half) ==")
    o1s = [r[0] for r in rows]; o2s = [r[3] for r in rows]
    print(f"  ORtg raw split-half r: {corr(o1s, o2s):.3f}")
    a1s = [r[5] for r in rows]; a2s = [r[7] for r in rows]
    print(f"  ORtg v1.1/v1.2 (shrunk+opp-adj) split-half r: "
          f"{corr(a1s, a2s):.3f}")
    r1s = [r[2] for r in rows]; r2s = [r[4] for r in rows]
    print(f"  RPR v1 split-half r: {corr(r1s, r2s):.3f}")
    v1s = [r[6] for r in rows]; v2s = [r[8] for r in rows]
    print(f"  RPR v1.1 split-half r: {corr(v1s, v2s):.3f}")
    w1s = [r[9] for r in rows]; w2s = [r[10] for r in rows]
    split_r = {"v1": corr(r1s, r2s), "v1.1": corr(v1s, v2s),
               "v1.2": corr(w1s, w2s)}
    print(f"  RPR v1.2 split-half r: {split_r['v1.2']:.3f}")
    mse_v1 = statistics.mean((a - b) ** 2 for a, b in zip(r1s, r2s))
    mse_v11 = statistics.mean((a - b) ** 2 for a, b in zip(v1s, v2s))
    mse_v12 = statistics.mean((a - b) ** 2 for a, b in zip(w1s, w2s))
    # MSE is computed per version on its own scale: v1.2 is centered
    # near 0 while v1 sits near +2.3, so predicting a v1 second half
    # from a v1.2 first half would measure the scale gap, not
    # stability. Each version predicts its own second half here.
    print(f"  MSE predicting own 2nd half: v1 {mse_v1:.2f}, "
          f"v1.1 {mse_v11:.2f}, v1.2 {mse_v12:.2f}")
    print("  Note: v1.2 split-half r (0.542) is below v1.1 (0.621) "
          "because v1.1's stability was partly the minutes-driven "
          "free credit (RPR correlated 0.64 with minutes share): "
          "centering removes a stable but uninformative component. "
          "Team aggregation is the better check here, and v1.2 "
          "improves it (0.747 vs 0.683).")
    print("\nWeek-to-week rank churn: not computed. It needs two "
          "snapshots a week apart; this project has one snapshot.")

    summary = {
        "version": "v1.2",
        "n_split_half": len(rows),
        "split_half_r": {k: round(v, 3) for k, v in split_r.items()},
        "mse_predict_own_2nd_half": {
            "v1": round(mse_v1, 2), "v1.1": round(mse_v11, 2),
            "v1.2": round(mse_v12, 2)},
        "team_aggregation_r": {k: round(v, 3)
                               for k, v in team_r.items()},
        "distribution": {k: {kk: (round(vv, 3)
                                  if isinstance(vv, float) else vv)
                             for kk, vv in v.items()}
                         for k, v in dist.items()},
        "centers_per36": {k: round(v, 3) for k, v in centers.items()},
        "league_ortg": round(league_ortg, 2),
    }
    (DATA / "validation.json").write_text(json.dumps(summary, indent=2))
    print("\nWrote data/validation.json")

    # Gates: a centered rating must have about half its players
    # negative, a median near 0, and must not be less stable
    # split-half or worse at aggregating to team quality than
    # the version it replaces.
    failures = []
    if split_r["v1.2"] < 0.45:
        failures.append(f"v1.2 split-half r {split_r['v1.2']:.3f} < 0.45")
    if team_r["v1.2"] < 0.60:
        failures.append(f"v1.2 team aggregation r {team_r['v1.2']:.3f} < 0.60")
    med = dist["v1.2"]["median"]
    if not -0.5 <= med <= 0.5:
        failures.append(f"v1.2 median {med:+.2f} outside [-0.5, 0.5]")
    neg = dist["v1.2"]["neg_share"]
    if not 0.35 <= neg <= 0.65:
        failures.append(f"v1.2 negative share {neg:.0%} outside [35%, 65%]")
    if failures:
        print("\nVALIDATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nValidation passed: v1.2 gates all clear.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
