"""GM acquisition board for NBA ratings (v1, box-score only).

Takes data/players_2026_27_eval.json and produces, per player:
  - Archetype(s), inferred from the box-score profile only, with the
    rule printed next to each archetype so it is checkable.
  - Acquisition tier, from thresholds on RPR, ORtg_full at usage,
    MPG, GP and total minutes, never from reputation.
    Tiers were recalibrated for RPR v1.2's centered scale (Nov 20,
    2026): v1.2 stars sit about +3 to +7 and the median is about
    0, so the v1 thresholds (Franchise RPR >= 8) would leave the
    top tier empty. The new thresholds were set from the v1.2
    distribution among qualified players (Franchise RPR >= 3.5
    is about the top 1%, All-Star >= 2.5 about the top 5%) and
    checked against the famous names, not fitted to them: a
    player who misses games is tiered on his minutes per game
    when he plays, not on %Min, because %Min conflates
    availability with role (a star who missed games looked like
    a bench player under %Min gates). Players with GP under 8
    or under 150 total minutes are not tiered at all: they are
    labelled "Insufficient minutes". The Overvalued guard is
    checked after that gate, so a low-GP player can never be
    labelled Overvalued.
  - Value signals: production per minute, efficiency at high usage,
    two-way signal, availability, shooting gravity, playmaking.
  - Value signals: production per minute, efficiency at high usage,
    two-way signal, availability, shooting gravity, playmaking.
  - Red flags: high usage low efficiency, turnover prone, foul
    prone, low minutes / availability risk, one-dimensional,
    small-sample warning.
  - Fit tags (generic team needs, v1): shooting, playmaking,
    rim protection, rebounding, perimeter defense, bench scoring.
  - filter_by_need(need, top_n): top fits for one need.

What is NOT here, by design: no contracts, salaries or trade
value. Age/height/weight now come from the ESPN roster pull
(fetch_rosters.py, see README "Rookies"); contracts are not
free anywhere (Spotrac etc. are paywalled) and are NOT scraped.
See README "Known gaps".

Stdlib only. No network calls.

Usage:
    python3 gm_board.py
        Writes data/gm_board_2026_27.json and data/gm_board_2026_27.csv,
        prints top 10 per tier.
    python3 gm_board.py --need shooting --limit 10
        Prints top 10 fits for a need: shooting, playmaking,
        rim_protection, rebounding, perimeter_defense, bench_scoring.
"""
import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"
MIN_PCT_FLOOR = 40.0

# Archetype rules: (name, rule text, predicate). First match is the
# primary archetype; every matching rule is listed as a tag.
# Role-player archetypes (3-and-D, specialist shooter) are gated on
# usage so a high-usage ball-handler is never labelled 3-and-D.
ARCHETYPES = [
    ("primary scorer",
     "%Poss >= 26 and PTS per 36 >= 24",
     lambda e: (e["poss_pct"] or 0) >= 26 and e["pts36"] >= 24),
    ("playmaker",
     "AST per 36 >= 6 and AST/TO >= 2.0",
     lambda e: e["ast36"] >= 6 and (e["ast_to"] or 0) >= 2.0),
    ("rim protector",
     "BLK per 36 >= 2 and REB per 36 >= 8",
     lambda e: e["blk36"] >= 2 and e["reb36"] >= 8),
    ("rebounder",
     "REB per 36 >= 11 and PTS per 36 < 20",
     lambda e: e["reb36"] >= 11 and e["pts36"] < 20),
    ("defensive playmaker",
     "STL per 36 >= 2 and AST per 36 >= 4",
     lambda e: e["stl36"] >= 2 and e["ast36"] >= 4),
    ("two-way big",
     "REB per 36 >= 8 and STL+BLK per 36 >= 2 and PTS per 36 >= 12",
     lambda e: e["reb36"] >= 8 and e["stl_blk36"] >= 2
     and e["pts36"] >= 12),
    ("3-and-D",
     "%Poss < 22 and AST per 36 < 4.5 and 3PA per 36 >= 5 and 3P% >= 0.36 and STL+BLK per 36 >= 1.5",
     lambda e: (e["poss_pct"] or 0) < 22 and e["ast36"] < 4.5
     and e["fg3a36"] >= 5
     and (e["fg3_pct"] or 0) >= 0.36 and e["stl_blk36"] >= 1.5),
    ("specialist shooter",
     "%Poss < 22 and 3P% >= 0.40 and 3PA per 36 >= 6",
     lambda e: (e["poss_pct"] or 0) < 22 and (e["fg3_pct"] or 0) >= 0.40
     and e["fg3a36"] >= 6),
    ("secondary scorer",
     "%Poss 22-28 and PTS per 36 >= 18",
     lambda e: 22 <= (e["poss_pct"] or 0) < 28 and e["pts36"] >= 18),
    ("bench scorer",
     "PTS per 36 >= 20 and %Min < 50",
     lambda e: e["pts36"] >= 20 and (e["min_pct"] or 0) < 50),
    ("glue / role player",
     "%Min >= 50 and RPR -0.5 to 1 and STL+BLK per 36 >= 1",
     lambda e: (e["min_pct"] or 0) >= 50 and -0.5 <= (e["rpr"] or 0) <= 1
     and e["stl_blk36"] >= 1),
]

# Fit tags: (need, rule text, predicate, sort key)
NEEDS = {
    "shooting": ("3P% >= 0.36 and 3PA per game >= 4, or 3P points share >= 0.30",
                 lambda e: ((e["fg3_pct"] or 0) >= 0.36 and e["fg3a_pg"] >= 4)
                 or (e["share_3s"] or 0) >= 0.30,
                 lambda e: e["fg3a36"]),
    "playmaking": ("AST per game >= 5",
                   lambda e: e["ast_pg"] >= 5,
                   lambda e: e["ast36"]),
    "rim_protection": ("BLK per game >= 1.5",
                       lambda e: e["blk_pg"] >= 1.5,
                       lambda e: e["blk36"]),
    "rebounding": ("REB per game >= 9",
                   lambda e: e["reb_pg"] >= 9,
                   lambda e: e["reb36"]),
    "perimeter_defense": ("STL per game >= 1.5",
                          lambda e: e["stl_pg"] >= 1.5,
                          lambda e: e["stl_blk36"]),
    "bench_scoring": ("PTS per game >= 15 and %Min < 60",
                      lambda e: e["pts_pg"] >= 15 and (e["min_pct"] or 0) < 60,
                      lambda e: e["pts36"]),
}


def flat(p):
    """One flat dict of the numbers the board rules need."""
    ev = p["eval"]
    tot = p["totals"]
    pg = p["per_game"]
    r = p.get("ratings") or {}
    minutes = tot["min"] or 1
    return {
        "name": p["name"], "team": p["team"], "position": p["position"],
        "athlete_id": p["athlete_id"], "gp": tot["gp"],
        "min_total": tot["min"],
        "min_pct": r.get("min_pct"), "mpg": pg["min"],
        "age": p.get("age"), "height_in": p.get("height_in"),
        "weight_lbs": p.get("weight_lbs"),
        "pts_pg": pg["pts"], "reb_pg": pg["reb"], "ast_pg": pg["ast"],
        "stl_pg": pg["stl"], "blk_pg": pg["blk"], "to_pg": pg["to"],
        "pf_pg": pg["pf"],
        "pts36": ev["per36"]["pts"], "reb36": ev["per36"]["reb"],
        "ast36": ev["per36"]["ast"], "stl36": ev["per36"]["stl"],
        "blk36": ev["per36"]["blk"], "to36": ev["per36"]["to"],
        "pf36": ev["per36"]["pf"],
        "stl_blk36": ev["defense_rebounding"]["stl_blk_per36"],
        "oreb36": ev["per36"]["oreb"], "dreb36": ev["per36"]["dreb"],
        "poss_pct": r.get("poss_pct"),
        "ortg_simple": ev["ortg_simple"], "ortg_full": ev["ortg_full"],
        "rpr": r.get("rpr"),
        "rpr_components": ev["rpr_components"],
        "quadrant": ev["quadrant"],
        "fg3_pct": p["shooting"]["fg3_pct"],
        "fg3a_pg": tot["fg3a"] / tot["gp"] if tot["gp"] else 0,
        "fg3a36": tot["fg3a"] / minutes * 36,
        "fg3m36": tot["fg3m"] / minutes * 36,
        "ts_pct": p["shooting"]["ts_pct"],
        "share_3s": ev["points_breakdown"]["share_3s"],
        "ast_to": ev["playmaking"]["ast_to_ratio"],
        "pts_std": ev["consistency"]["pts_std"],
        "trend_ppg": ev["trending"]["trend_ppg"],
        "last7_gp": ev["trending"]["last7_gp"],
    }


def archetypes(e):
    hits = [(name, rule) for name, rule, pred in ARCHETYPES if pred(e)]
    if not hits:
        hits = [("rotation / depth",
                 "no archetype rule matched")]
    return hits


def tier(e):
    """Acquisition tier, thresholds only, calibrated for RPR v1.2.

    Checked in order. Two gates come first: a player with GP
    under 8 or under 150 total minutes is not tiered at all
    ("Insufficient minutes"), and only then does the Overvalued
    guard fire, so a small-sample hot or cold stretch can never
    land a player in Overvalued / avoid. Role is judged by MPG
    when the player plays, not by %Min: %Min conflates
    availability with role, so a star who missed games (Curry,
    34.5% Min at 26.5 MPG) was labelled buy-low under the old
    %Min gates. Availability is a red flag, not a tier.
    Thresholds are set from the v1.2 distribution, not fitted
    to individual names: Franchise RPR >= 3.5 is about the top
    1% of qualified players, All-Star >= 2.5 about the top 5%.
    """
    rpr = e["rpr"]
    if rpr is None:
        return ("Insufficient minutes",
                "no RPR: no possessions used")
    ortg = e["ortg_full"]
    if ortg is None:
        ortg = e["ortg_simple"] or 0
    poss = e["poss_pct"] or 0
    mpg = e["mpg"] or 0
    gp = e["gp"]
    # Gate 1: not enough basketball to tier.
    if gp < 8 or (e["min_total"] or 0) < 150:
        return ("Insufficient minutes",
                "GP < 8 or under 150 total minutes")
    # Overvalued / avoid: clearly inefficient high usage, or heavy
    # turnovers with no playmaking return. The RPR bar is -1.0,
    # not 0: in v1.2 about half the league is negative, so RPR < 0
    # alone is average, not overvalued. The bar is ORtg_full < 100,
    # not < 105: a star at 101-104 on huge usage is having an
    # inefficient stretch, not an empty-calorie season.
    if (poss >= 25 and ortg < 100) or (poss >= 20 and rpr < -1.0) \
            or ((e["to36"] >= 4) and (e["ast_to"] or 0) < 1.2):
        return ("Overvalued / avoid",
                "high usage with ORtg_full < 100, or RPR < -1.0 at "
                "20%+ usage, or 4+ TO per 36 with AST/TO < 1.2")
    if rpr >= 3.5 and ortg >= 108 and mpg >= 25 and gp >= 10:
        return ("Franchise cornerstone",
                "RPR >= 3.5, ORtg_full >= 108, MPG >= 25, GP >= 10")
    if rpr >= 2.5 and ortg >= 104 and mpg >= 22:
        return ("All-Star level",
                "RPR >= 2.5, ORtg_full >= 104, MPG >= 22")
    if rpr >= 1.5 and mpg >= 22 and ortg >= 100:
        return ("High-value starter",
                "RPR >= 1.5, MPG >= 22, ORtg_full >= 100")
    if (rpr >= 2.0 and mpg < 25) or \
            (rpr >= 1.5 and ortg >= 120 and poss < 22):
        return ("Undervalued / buy-low",
                "RPR >= 2.0 but MPG < 25 (production without a "
                "starter's minutes), or RPR >= 1.5 with "
                "ORtg_full >= 120 at < 22% usage")
    if 0.5 <= rpr < 1.5 and mpg >= 20:
        return ("Elite role player",
                "RPR 0.5-1.5, MPG >= 20")
    return ("Rotation / depth",
            "does not meet any tier above")


def value_signals(e):
    out = []
    if (e["poss_pct"] or 0) >= 25 and (e["ortg_full"] or 0) >= 115:
        out.append("efficient at high usage (%Poss 25+, ORtg_full 115+)")
    if e["stl_blk36"] >= 2 and e["pf36"] <= 3.5:
        out.append("two-way (STL+BLK per 36 >= 2, PF per 36 <= 3.5)")
    if (e["min_pct"] or 0) >= 65 and e["gp"] >= 13:
        out.append("available (%Min 65+, GP 13+)")
    if e["fg3a36"] >= 6 and (e["fg3_pct"] or 0) >= 0.37:
        out.append("shooting gravity (3PA per 36 >= 6, 3P% >= 0.37)")
    if (e["ast_to"] or 0) >= 2:
        out.append("playmaking (AST/TO >= 2)")
    if e["pts36"] >= 25:
        out.append("production per minute (PTS per 36 >= 25)")
    if e["reb36"] >= 11:
        out.append("rebounding per minute (REB per 36 >= 11)")
    return out


def red_flags(e):
    out = []
    if (e["poss_pct"] or 0) >= 25 and (e["ortg_full"] or 0) < 100:
        out.append("high usage, low efficiency (%Poss 25+, ORtg_full < 100)")
    if e["to36"] >= 4:
        out.append(f"turnover prone (TO per 36 {e['to36']:.1f})")
    if (e["ast_to"] or 0) < 1.0 and e["ast36"] >= 3:
        out.append("turnover prone relative to assists (AST/TO < 1.0)")
    if e["pf36"] >= 4.5:
        out.append(f"foul prone (PF per 36 {e['pf36']:.1f})")
    if (e["min_pct"] or 0) < 40:
        out.append(f"low minutes / availability risk (%Min {e['min_pct']:.1f})")
    if e["gp"] < 8:
        out.append(f"small sample (GP {e['gp']})")
    # One-dimensional: scores but contributes little else.
    if e["pts36"] >= 20 and e["reb36"] < 5 and e["ast36"] < 3 \
            and e["stl_blk36"] < 1:
        out.append("one-dimensional (scores, little rebounding/assists/defense)")
    if e["gp"] < 13:
        out.append("early-season sample: under 13 games")
    return out


def fit_tags(e):
    return [need for need, (_, pred, _) in NEEDS.items() if pred(e)]


def build(p):
    e = flat(p)
    arch = archetypes(e)
    t, t_rule = tier(e)
    return {
        "athlete_id": e["athlete_id"], "name": e["name"],
        "team": e["team"], "position": e["position"],
        "gp": e["gp"], "min_pct": e["min_pct"], "mpg": e["mpg"],
        "pts_pg": e["pts_pg"], "reb_pg": e["reb_pg"],
        "ast_pg": e["ast_pg"], "stl_pg": e["stl_pg"],
        "blk_pg": e["blk_pg"],
        "pts_per36": e["pts36"], "fg3m_per36": e["fg3m36"],
        "reb_per36": e["reb36"],
        "ast_per36": e["ast36"], "stl_blk_per36": e["stl_blk36"],
        "ortg_simple": e["ortg_simple"], "ortg_full": e["ortg_full"],
        "poss_pct": e["poss_pct"], "rpr": e["rpr"],
        "rpr_components": e["rpr_components"],
        "quadrant": e["quadrant"],
        "archetype": arch[0][0],
        "archetype_rule": arch[0][1],
        "archetype_tags": [a[0] for a in arch],
        "tier": t, "tier_rule": t_rule,
        "value_signals": value_signals(e),
        "red_flags": red_flags(e),
        "fit_tags": fit_tags(e),
        "age": e["age"], "height_in": e["height_in"],
        "weight_lbs": e["weight_lbs"],
        "contract": None,
        "trend_ppg": e["trend_ppg"],
    }


def filter_by_need(board, need, top_n=10):
    if need not in NEEDS:
        raise ValueError(f"unknown need {need!r}; try "
                         f"{sorted(NEEDS)}")
    # Need fits require GP >= 8: a 2-4 game hot streak is not a target.
    # Shooting sorts by made 3s per 36 (volume times accuracy), not
    # raw attempts, so a high-volume low-percentage chucker does not
    # top the list.
    sel = [b for b in board if need in b["fit_tags"] and b["gp"] >= 8]
    key_map = {"shooting": lambda b: b["fg3m_per36"],
               "playmaking": lambda b: b["ast_per36"],
               "rim_protection": lambda b: b["blk_pg"],
               "rebounding": lambda b: b["reb_per36"],
               "perimeter_defense": lambda b: b["stl_blk_per36"],
               "bench_scoring": lambda b: b["pts_per36"]}
    if need == "shooting":
        sel.sort(key=lambda b: (b["fg3m_per36"], b["rpr"] or 0),
                 reverse=True)
    else:
        sel.sort(key=lambda b: (key_map[need](b), b["rpr"] or 0),
                 reverse=True)
    return sel[:top_n]


def write_csv(board, out_path):
    fields = ["athlete_id", "name", "team", "position", "tier",
              "archetype", "archetype_tags", "fit_tags", "gp",
              "min_pct", "pts_pg", "reb_pg", "ast_pg", "pts_per36",
              "reb_per36", "ast_per36", "stl_blk_per36", "ortg_full",
              "poss_pct", "rpr", "age", "height_in", "weight_lbs",
              "value_signals", "red_flags",
              "trend_ppg"]
    with open(out_path, "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for b in board:
            row = {k: b.get(k) for k in fields}
            row["archetype_tags"] = "; ".join(b["archetype_tags"])
            row["fit_tags"] = "; ".join(b["fit_tags"])
            row["value_signals"] = "; ".join(b["value_signals"])
            row["red_flags"] = "; ".join(b["red_flags"])
            for k in ("min_pct", "pts_pg", "reb_pg", "ast_pg",
                      "pts_per36", "reb_per36", "ast_per36",
                      "stl_blk_per36", "ortg_full", "poss_pct", "rpr",
                      "trend_ppg"):
                row[k] = "" if row[k] is None else round(row[k], 2)
            w.writerow(row)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--need", default=None,
                    help="filter: shooting, playmaking, rim_protection, "
                         "rebounding, perimeter_defense, bench_scoring")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    payload = json.loads((DATA / "players_2026_27_eval.json").read_text())
    board = [build(p) for p in payload["players"]]
    # Sort board by RPR for the file, best first.
    board.sort(key=lambda b: b["rpr"] if b["rpr"] is not None else -99,
               reverse=True)
    out = {"season": SEASON,
           "games_included": payload.get("games_included"),
           "player_count": len(board),
           "notes": ("GM board v1, box-score only. Tiers and archetypes "
                     "are threshold rules in gm_board.py, never "
                     "reputation. RPR here is RPR v1.2 (Nov 20, 2026): "
                     "ORtg shrunk toward the league average (K = 200 "
                     "possessions used) and opponent-adjusted, with "
                     "the counting terms centered on the league-"
                     "average per-36 rates, so an average player "
                     "scores about 0. The tier thresholds were "
                     "recalibrated for v1.2's centered scale (see "
                     "gm_board.py tier()): Franchise RPR >= 3.5, "
                     "All-Star >= 2.5, High-value starter >= 1.5. "
                     "Players with GP < 8 or under 150 total minutes "
                     "are labelled Insufficient minutes and are not "
                     "tiered; the Overvalued guard fires only after "
                     "that gate. Age, height (inches) and weight "
                     "(lbs) come from the ESPN roster pull. "
                     "No contracts or salaries: contracts are not free "
                     "anywhere. No tracking or on/off data."),
           "tiers": ["Franchise cornerstone", "All-Star level",
                     "High-value starter", "Undervalued / buy-low",
                     "Elite role player", "Overvalued / avoid",
                     "Rotation / depth", "Insufficient minutes"],
           "players": board}
    (DATA / "gm_board_2026_27.json").write_text(json.dumps(out))
    write_csv(board, DATA / "gm_board_2026_27.csv")
    print(f"Wrote data/gm_board_2026_27.json + .csv: {len(board)} players")
    if args.need:
        print(f"\nTop {args.limit} fits for need: {args.need}")
        for b in filter_by_need(board, args.need, args.limit):
            print(f"  {b['name']} ({b['team']}) {b['tier']} / "
                  f"{b['archetype']} RPR {b['rpr']}")
        return 0
    for t in out["tiers"]:
        members = [b for b in board if b["tier"] == t][:10]
        print(f"\n{t} ({sum(1 for b in board if b['tier'] == t)} players):")
        for b in members:
            print(f"  {b['name']} ({b['team']}) GP {b['gp']} "
                  f"PTS {b['pts_pg']:.1f} REB {b['reb_pg']:.1f} "
                  f"AST {b['ast_pg']:.1f} ORtg_full "
                  f"{b['ortg_full'] and round(b['ortg_full'], 1)} "
                  f"RPR {b['rpr'] and round(b['rpr'], 2)} "
                  f"/ {b['archetype']} / fits: {', '.join(b['fit_tags']) or '-'}"
                  f"{' / FLAGS: ' + '; '.join(b['red_flags']) if b['red_flags'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
