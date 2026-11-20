"""GM team-needs and trade-fit evaluator, v1 (box-score only).

Builds, for each of the 30 NBA teams, a profile (strengths,
weaknesses, star, star gaps, ranked needs) and a list of
*realistic* trade targets: players who would typically be
available, not stars.

Method (from research_notes/nba-team-needs-trade-fit-20261120-1902/
report.md, Dean Oliver's Four Factors and Nylon Calculus'
lineup-balance / archetype-interaction work):

1. Diagnose the team in rate / percentile terms. AdjO/AdjD from
   team_ratings.py, plus the Four Factors on both ends from the
   game box scores (eFG%, TOV%, OREB%, FT rate; Oliver weights
   40/25/20/15, re-tested 50/23/20/7 on 2025-26, explaining 94%
   of margin variation), plus a minutes-weighted skill profile
   from the top 9 rotation players. Every number is reported as
   a league percentile (0-100) as well as raw, never as raw
   totals: PPG/RPG/APG reward pace and minutes, not quality.
2. Anchor needs on the star. The star is the highest-RPR player
   on the team. A need the star is weak at gets a higher weight;
   a need the star already supplies (skill percentile >= 75)
   gets a lower weight. Ron's example: a team whose point guard
   is already a great facilitator gains little from another
   facilitator.
3. Score a trade as the change in the team skill profile,
   passed through a concave (diminishing-returns) need
   function, plus the change in quality vs the player actually
   displaced, not vs league average. Reported as three numbers:
   QualityGain, FitGain, TradeScore. A single merged number
   would hide whether a target is better, or just a better fit.
4. REALISM / AVAILABILITY FILTER. The model must not suggest
   trading for a star. Only players in tiers Rotation / depth,
   Elite role player, Undervalued / buy-low, and at most
   High-value starter are eligible, and a player who is one of
   the top 3 RPR players on his own team is excluded even if
   his tier slips through (early-season noise can put a big
   name in a lower tier: in this run LeBron James lands in
   Elite role player on ORtg_full 97.5, and James Harden in
   Rotation / depth on RPR -0.16). Franchise cornerstone and All-Star
   level players are never suggested, and neither are
   Overvalued / avoid players. On top of the tier rule, a
   target must look like a role player on his own team, not a
   featured option: under 55% of team minutes (not a full-time
   starter), under 26% usage, and under 20 points per game.
   A target must also fill a real need (FitGain > 0) and the
   upgrade over the displaced player must be modest
   (RPR_target - RPR_displaced <= 5.0, MAX_RPR_JUMP below): a
   huge jump implies a star-for-star swap, which is not a
   realistic suggestion.
   Example: a team needing playmaking gets a backup PG with
   high assists per 36, not a star PG. These are data-only
   proxies for availability; without contracts or salaries
   they cannot prove a player is gettable, so the output says
   "realistic" and never "available".

   Star clarity: the star is the highest-RPR qualifying player,
   but on a rebuilding team that can be a role player (e.g.
   Myles Turner, RPR 0.28, as Milwaukee's highest-RPR qualifier
   after Milwaukee traded Giannis to Miami). A team whose star's
   RPR is under 1.5 is labelled "No clear star in this data" in
   the output and on the site; the star's numbers are still used
   for the need weights, but no player is presented as the star.
   (1.5 is about the 85th percentile among qualified players on
   the Nov 20 run; see STAR_RPR_MIN.)
   Teams reflect the 2026-27 rosters after a busy 2026 offseason:
   Giannis to Miami, LeBron to Philadelphia, Kessler to the
   Lakers, and Davis to Washington, among others.

Formulas (all weights are hand-set v1 choices, documented here,
not fitted; v2 should fit them against post-trade Adj Net):

  Skill percentiles: per-36 rates, percentile-ranked league-wide
  among players with >= 150 minutes. Skills:
    shooting          = made 3s per 36 (volume x accuracy)
    playmaking        = assists per 36
    rebounding        = rebounds per 36
    rim_protection    = blocks per 36
    perimeter_defense = steals per 36 plus half-weight blocks
                        per 36 (weak proxy: steals reward gambling
                        and miss positional defence; flagged as
                        such, not hidden)
    bench_scoring     = points per 36, for players with %Min < 60
                        (team profile: minutes-weighted pts/36 of
                        the bench only, percentile-ranked bench-
                        only; a starter-heavy team scores low)
  Team skill S_k = minutes-weighted mean of player percentiles
  over the top 9 players by minutes on the team, except bench
  scoring, which is the minutes-weighted mean over the bench
  players in the top 9.
  Need_k = max(0, 60 - S_k). Target level 60th percentile.
  w_k = base weight x star factor, normalised to sum 1.
    base: shooting .25, playmaking .20, rebounding .15,
          rim_protection .15, perimeter_defense .15,
          bench_scoring .10
    star factor: 1.5 if star percentile <= 35 at k, 1.25 if
    <= 50, 0.5 if >= 75, else 1.0.
  Trade delta, replacing the displaced player (the worst
  rotation player at the target's best skill, star excluded)
  with the target at the target's current MPG:
    dS_k = (MPG_target * pct_target,k
            - MPG_out * pct_out,k) / 240
    FitGain = sum_k w_k * (Need_k / 60)
              * sqrt(max(dS_k, 0) / 10), capped per skill at 2
    QualityGain = (RPR_target - RPR_out) * (MPG_target / 240)
    AvailabilityDiscount = 0.10 * (QualityGain + FitGain)
      if GP < 10, plus another 0.10 if %Min < 40
    TradeScore = QualityGain + FitGain - AvailabilityDiscount
    (The usage penalty that used to sit in TradeScore was
    removed in v1.1: it was 0.0 for every target on the Nov 20
    run, so it was decoration, not a cost. See score_target.)

  The per-skill FitGain cap (2.0) is a tiebreaker and safety
  rail, not a calibrated driver, and is documented as such. On
  this run the largest per-skill FitGain contribution is about
  0.16 and the theoretical maximum is about 0.4 (weight
  <= ~0.35, Need/60 <= 1, sqrt term <= ~1.12), so the 2.0 cap
  never binds on real data; it exists so a data error cannot
  blow up FitGain. A real redundancy cost would need a fitted
  coefficient, which is v2 work (see README). Neither number
  is presented as a measured effect.

Limits, stated plainly: no contracts or salaries exist in the
inputs, so none are used and none are invented; a target's
salary fit is unknown. No lineup, on/off, tracking, or shot
location data in v1, so this is a BOX-SCORE fit: a prior that
lineup data should update, never a lineup prediction. Early
season, about 15 games per team, so every percentile is noisy
and 3P% in particular is the noisiest stat in basketball.
Regular-season, average-opponent only: matchup-specific fit
is a v3.

Stdlib only. No network calls.

Usage:
    python3 team_needs.py
        Writes data/team_needs_2026_27.json and .csv, prints a
        one-line summary per team.
    python3 team_needs.py --team "Los Angeles Lakers"
        That team's profile and its top realistic targets.
    python3 team_needs.py --need playmaking
        Top 10 realistic targets for that need, league-wide.
    python3 team_needs.py --team "Los Angeles Lakers" --need shooting
        That team's top targets for that need.
"""
import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"

TARGET_LEVEL = 60.0        # percentile a team wants to reach per skill
ROTATION_N = 9             # top N players by minutes define the profile
MIN_MINUTES_PCT = 150      # minutes gate for percentile ranks
MIN_GP_TARGET = 8          # a 2-4 game hot streak is not a target
MAX_RPR_JUMP = 5.0         # bigger upgrade is a roster overhaul, not a
                           # slight value add. Set at 5, not 3: with the
                           # displaced player picked as the weakest
                           # rotation player at the skill, a good role
                           # player (RPR ~2 in v1.2) replacing a weak
                           # spot (RPR ~-1) jumps ~3 and is exactly
                           # the intended suggestion. Stars are kept
                           # out by the tier / top-3 / minutes / usage
                           # / scoring gates above, not by this cap.
USAGE_BUDGET = 65.0         # retired in v1.1: the usage penalty it
                           # fed never bound (see score_target), so
                           # the penalty was removed, not retuned.
MAX_MIN_PCT_TARGET = 55.0  # target must not be a full-time starter
MAX_MPG_TARGET = 30.0      # ... nor log 30+ minutes a night when he plays
                           # (catches a core piece who missed games and
                           # so slips under the %Min cap: in this run
                           # LeBron James plays 32.4 MPG at 51% Min)
MAX_USAGE_TARGET = 26.0    # ... nor a featured offensive option
MAX_PTS_TARGET = 20.0      # ... nor a 20-point scorer, on his own team
TOP_N_OWN_TEAM = 3         # ... nor top-3 RPR on his own team
STAR_RPR_MIN = 1.5         # a "star" under RPR 1.5 is a role player
                           # on a rebuilding team that traded its
                           # star away, not a star: the team is
                           # labelled "No clear star in this data".
                           # 1.5 is about the 85th percentile among
                           # qualified players on the Nov 20 run and
                           # sits in a natural gap in the team-top
                           # distribution (1.25 to 1.63).

# Tiers that would typically be available in a trade. Franchise
# cornerstone and All-Star level are never suggested; Overvalued /
# avoid is never suggested either (a bad contract is not a target).
ELIGIBLE_TIERS = {"Rotation / depth", "Elite role player",
                  "Undervalued / buy-low", "High-value starter"}

BASE_WEIGHTS = {"shooting": 0.25, "playmaking": 0.20,
                "rebounding": 0.15, "rim_protection": 0.15,
                "perimeter_defense": 0.15, "bench_scoring": 0.10}

SKILLS = list(BASE_WEIGHTS)

# How each skill is measured per player (per-36 rates from eval).
# bench_scoring uses pts/36 but only counts for players with
# %Min < 60; a starter's scoring does not fill a bench need.
# perimeter_defense is STL per 36 plus half-weight BLK per 36:
# steals are the only perimeter signal in a box score, and the
# half-weight blocks credit help defence without duplicating
# rim protection in full. It is still a weak proxy, flagged as
# such, not hidden.
def skill_rates(ev_player):
    ev = ev_player["eval"]
    p36 = ev["per36"]
    min_pct = (ev_player.get("ratings") or {}).get("min_pct") or 0
    fg3m36 = (ev_player["totals"]["fg3m"] / ev_player["totals"]["min"] * 36
              if ev_player["totals"]["min"] else 0.0)
    return {
        "shooting": fg3m36,
        "playmaking": p36["ast"],
        "rebounding": p36["reb"],
        "rim_protection": p36["blk"],
        "perimeter_defense": p36["stl"] + 0.5 * p36["blk"],
        "bench_scoring": p36["pts"] if min_pct < 60 else 0.0,
    }


def percentile_ranks(values):
    """League percentile per value: 100 * (below + 0.5 * equal) / n."""
    n = len(values)
    out = {}
    for i, v in enumerate(values):
        below = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out[i] = 100.0 * (below + 0.5 * equal) / n if n else 50.0
    return out


def _split_made_att(s):
    try:
        m, _, a = str(s).partition("-")
        return int(m), int(a)
    except (TypeError, ValueError):
        return 0, 0


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def four_factors(games):
    """Per-team Four Factors, offence and defence, from game box
    scores. Offence sums a team's own stats; defence sums what its
    opponents did against it. Returns team -> dict of raw factors
    plus league percentiles per factor (computed by caller)."""
    agg = defaultdict(lambda: defaultdict(int))
    for g in games:
        if g["home_score"] == 0 and g["away_score"] == 0:
            continue
        if g.get("stats_missing"):
            continue
        for side, opp_side in (("home", "away"), ("away", "home")):
            team, opp = g[side], g[opp_side]
            all_stats = g.get(f"{side}_stats", {}).get("all", {}) or {}
            opp_all = g.get(f"{opp_side}_stats", {}).get("all", {}) or {}
            fgm, fga = _split_made_att(
                all_stats.get("fieldGoalsMade-fieldGoalsAttempted", "0-0"))
            fg3m, _ = _split_made_att(all_stats.get(
                "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                "0-0"))
            _ftm, fta = _split_made_att(all_stats.get(
                "freeThrowsMade-freeThrowsAttempted", "0-0"))
            oreb = _int(all_stats.get("offensiveRebounds", 0))
            dreb = _int(all_stats.get("defensiveRebounds", 0))
            tov = _int(all_stats.get("totalTurnovers", all_stats.get("turnovers", 0)))  # totalTurnovers, not player turnovers: team turnovers count in possessions, matching the solver/fetcher/player_profiles
            o_oreb = _int(opp_all.get("offensiveRebounds", 0))
            o_dreb = _int(opp_all.get("defensiveRebounds", 0))
            a = agg[team]
            a["fgm"] += fgm
            a["fga"] += fga
            a["fg3m"] += fg3m
            a["fta"] += fta
            a["oreb"] += oreb
            a["dreb"] += dreb
            a["tov"] += tov
            a["opp_oreb"] += o_oreb
            a["opp_dreb"] += o_dreb
            a["games"] += 1
    out = {}
    for team, a in agg.items():
        poss = a["fga"] - a["oreb"] + a["tov"] + 0.44 * a["fta"]
        out[team] = {
            "efg_pct": ((a["fgm"] + 0.5 * a["fg3m"]) / a["fga"]
                        if a["fga"] else None),
            "tov_pct": (a["tov"] / poss if poss else None),
            "oreb_pct": (a["oreb"] / (a["oreb"] + a["opp_dreb"])
                         if a["oreb"] + a["opp_dreb"] else None),
            "ft_rate": (a["fta"] / a["fga"] if a["fga"] else None),
            # Defence = what opponents did: opp eFG% is not in the
            # parsed stats (opp FGM is), so use opp OREB% against
            # us and opp FT rate from the opponent's own line in
            # the same game is not stored here; opp eFG% and opp
            # TOV% are derived below from the opponent aggregates.
            "games": a["games"],
        }
    # Opponent factors: for each team, the factors of its opponents
    # are the mirror of the opponent's own offensive factors, so
    # recompute from the per-game lines directly.
    opp = defaultdict(lambda: defaultdict(int))
    for g in games:
        if g["home_score"] == 0 and g["away_score"] == 0:
            continue
        if g.get("stats_missing"):
            continue
        for side, opp_side in (("home", "away"), ("away", "home")):
            team = g[side]
            all_stats = g.get(f"{opp_side}_stats", {}).get("all", {}) or {}
            fgm, fga = _split_made_att(
                all_stats.get("fieldGoalsMade-fieldGoalsAttempted", "0-0"))
            fg3m, _ = _split_made_att(all_stats.get(
                "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                "0-0"))
            _ftm, fta = _split_made_att(all_stats.get(
                "freeThrowsMade-freeThrowsAttempted", "0-0"))
            oreb = _int(all_stats.get("offensiveRebounds", 0))
            tov = _int(all_stats.get("totalTurnovers", all_stats.get("turnovers", 0)))  # totalTurnovers, not player turnovers: team turnovers count in possessions, matching the solver/fetcher/player_profiles
            o = opp[team]
            o["fgm"] += fgm
            o["fga"] += fga
            o["fg3m"] += fg3m
            o["fta"] += fta
            o["oreb"] += oreb
            o["tov"] += tov
    for team, o in opp.items():
        poss = o["fga"] - o["oreb"] + o["tov"] + 0.44 * o["fta"]
        out[team]["opp_efg_pct"] = ((o["fgm"] + 0.5 * o["fg3m"]) / o["fga"]
                                    if o["fga"] else None)
        out[team]["opp_tov_pct"] = (o["tov"] / poss if poss else None)
        out[team]["opp_ft_rate"] = (o["fta"] / o["fga"]
                                    if o["fga"] else None)
        # Defensive rebound %: our DREB over total rebounds available
        # (our DREB + opp OREB), from the offence aggregates.
        a = agg[team]
        out[team]["dreb_pct"] = (a["dreb"] / (a["dreb"] + a["opp_oreb"])
                                 if a["dreb"] + a["opp_oreb"] else None)
    return out


def load_inputs():
    teams_doc = json.loads((DATA / "teams_2026_27.json").read_text())
    eval_doc = json.loads((DATA / "players_2026_27_eval.json").read_text())
    board_doc = json.loads((DATA / "gm_board_2026_27.json").read_text())
    games = json.loads((DATA / "games_2026_27.json").read_text())
    # Tier keyed by (athlete_id, team): a player who changed teams
    # mid-season has one profile per team with a different tier
    # (Carson Cooper: Rotation / depth on the Lakers, Overvalued /
    # avoid on Memphis). Keying by athlete_id alone lets one
    # profile overwrite the other.
    tier_by_id = {(b["athlete_id"], b["team"]): b["tier"]
                  for b in board_doc["players"]}
    return teams_doc, eval_doc["players"], tier_by_id, games


def build_profiles(teams_doc, players, tier_by_id, games):
    """Team profiles: Four Factors, skill profile, star, needs."""
    ff = four_factors(games)
    # League percentiles for the Four Factors (higher is better,
    # except TOV% and opp eFG% where lower is better).
    ff_keys = [("efg_pct", True), ("tov_pct", False),
               ("oreb_pct", True), ("ft_rate", True),
               ("opp_efg_pct", False), ("opp_tov_pct", True),
               ("dreb_pct", True), ("opp_ft_rate", False)]
    ff_pct = {}
    for key, high_good in ff_keys:
        vals = [ff[t][key] for t in ff if ff[t].get(key) is not None]
        teams_sorted = [t for t in ff if ff[t].get(key) is not None]
        ranks = percentile_ranks(vals)
        for team, r in zip(teams_sorted, ranks.values()):
            pct = r if high_good else 100.0 - r
            ff_pct.setdefault(team, {})[key] = round(pct, 1)
    # Player skill percentiles, league-wide, minutes-gated.
    # bench_scoring is percentile-ranked bench-only: ranking bench
    # scorers against starters (whose bench rate is 0 by
    # construction) would put every bench player near the top and
    # make every team's bench skill look low. Starters keep a
    # bench percentile against the bench distribution (about 0),
    # but it is never used for a team skill or a target's bench
    # fit: those use bench players only.
    gated = [p for p in players if p["totals"]["min"] >= MIN_MINUTES_PCT]
    bench_gated = [p for p in gated
                   if ((p.get("ratings") or {}).get("min_pct") or 0) < 60]
    rates = {k: [skill_rates(p)[k] for p in gated] for k in SKILLS}
    rates["bench_scoring"] = [skill_rates(p)["bench_scoring"]
                              for p in bench_gated]
    pct_by_player = {}
    for k in SKILLS:
        pool = bench_gated if k == "bench_scoring" else gated
        ranks = percentile_ranks(rates[k])
        for p, r in zip(pool, ranks.values()):
            pct_by_player.setdefault((p["athlete_id"], p["team"]), {})[k] = r
    # Ungated players get a percentile from the same scale anyway
    # (their rate placed against the gated distribution).
    for p in players:
        key = (p["athlete_id"], p["team"])
        if key in pct_by_player and "bench_scoring" in pct_by_player[key]:
            continue
        sr = skill_rates(p)
        d = pct_by_player.setdefault(key, {})
        for k in SKILLS:
            if k in d:
                continue
            vals = rates[k]
            below = sum(1 for x in vals if x < sr[k])
            equal = sum(1 for x in vals if x == sr[k])
            d[k] = 100.0 * (below + 0.5 * equal) / len(vals) if vals else 50.0
        pct_by_player[key] = d

    by_team = defaultdict(list)
    for p in players:
        by_team[p["team"]].append(p)
    teams = teams_doc["teams"]
    profiles = {}
    for team, t in teams.items():
        roster = sorted(by_team.get(team, []),
                        key=lambda p: p["totals"]["min"], reverse=True)
        rotation = roster[:ROTATION_N]
        rot_min = sum(p["totals"]["min"] for p in rotation) or 1
        skills = {}
        skill_raw = {}
        for k in SKILLS:
            if k == "bench_scoring":
                # Bench skill only: minutes-weighted mean over the
                # bench players in the rotation, not over starters
                # scored 0. A team with no bench player in its top
                # 9 gets a neutral 50, not a fake hole.
                bench_rot = [p for p in rotation
                             if ((p.get("ratings") or {}).get("min_pct")
                                 or 0) < 60]
                bench_min = sum(p["totals"]["min"] for p in bench_rot)
                if bench_rot and bench_min:
                    skills[k] = round(sum(
                        p["totals"]["min"] / bench_min
                        * pct_by_player[(p["athlete_id"], p["team"])][k]
                        for p in bench_rot), 1)
                    skill_raw[k] = round(sum(
                        p["totals"]["min"] / bench_min
                        * skill_rates(p)[k] for p in bench_rot), 2)
                else:
                    skills[k] = 50.0
                    skill_raw[k] = None
                continue
            skills[k] = round(sum(
                p["totals"]["min"] / rot_min
                * pct_by_player[(p["athlete_id"], p["team"])][k]
                for p in rotation), 1)
            skill_raw[k] = round(sum(
                p["totals"]["min"] / rot_min
                * skill_rates(p)[k] for p in rotation), 2)
        # Star: highest RPR on the team with at least 8 games and
        # at least 20 minutes per game when he plays. MPG, not
        # %Min, is the role test: %Min conflates availability
        # with role, so a star who missed games (Curry, 34.5%
        # Min at 26.5 MPG) read as a bench player under the old
        # %Min floor and his team read as starless.
        cands = [p for p in roster
                 if p["totals"]["gp"] >= MIN_GP_TARGET
                 and p["per_game"]["min"] >= 20]
        star = max(cands, key=lambda p: (p.get("ratings") or {}).get("rpr")
                   if (p.get("ratings") or {}).get("rpr") is not None
                   else -99) if cands else (roster[0] if roster else None)
        star_pct = (pct_by_player[(star["athlete_id"], star["team"])]
                    if star else {})
        star_rpr = (star.get("ratings") or {}).get("rpr") if star else None
        star_usage = (star.get("ratings") or {}).get("poss_pct") if star else None
        # Need weights: base x star factor, normalised.
        weights = {}
        for k in SKILLS:
            # bench_scoring is not a star skill: a star is a
            # starter by definition, so his bench-scoring rate
            # is 0 and would read as a gap on every team. The
            # star factor for it stays neutral.
            if k == "bench_scoring":
                weights[k] = BASE_WEIGHTS[k]
                continue
            sp = star_pct.get(k, 50.0)
            if sp <= 35:
                f = 1.5
            elif sp <= 50:
                f = 1.25
            elif sp >= 75:
                f = 0.5
            else:
                f = 1.0
            weights[k] = BASE_WEIGHTS[k] * f
        wsum = sum(weights.values()) or 1.0
        weights = {k: round(v / wsum, 3) for k, v in weights.items()}
        needs = {}
        for k in SKILLS:
            need = max(0.0, TARGET_LEVEL - skills[k])
            needs[k] = {"team_skill_pct": skills[k],
                        "need": round(need, 1),
                        "weight": weights[k],
                        "weighted_need": round(weights[k] * need, 1)}
        ranked_needs = sorted(SKILLS,
                              key=lambda k: needs[k]["weighted_need"],
                              reverse=True)
        # Strengths / weaknesses from AdjO/AdjD, Four Factors and
        # skill percentiles. Strength >= 65th, weakness <= 35th.
        adj_o_pct = _pct_of([x["adj_o"] for x in teams.values()],
                            t["adj_o"])
        # AdjD: lower is better, so percentile of the reversed value.
        adj_d_pct = _pct_of([-x["adj_d"] for x in teams.values()],
                            -t["adj_d"])
        strengths, weaknesses = [], []
        if adj_o_pct >= 65:
            strengths.append(f"offense (AdjO {t['adj_o']:.1f}, "
                             f"{_ord(adj_o_pct)} pct)")
        if adj_o_pct <= 35:
            weaknesses.append(f"offense (AdjO {t['adj_o']:.1f}, "
                              f"{_ord(adj_o_pct)} pct)")
        if adj_d_pct >= 65:
            strengths.append(f"defense (AdjD {t['adj_d']:.1f}, "
                             f"{_ord(adj_d_pct)} pct)")
        if adj_d_pct <= 35:
            weaknesses.append(f"defense (AdjD {t['adj_d']:.1f}, "
                              f"{_ord(adj_d_pct)} pct)")
        ff_labels = {"efg_pct": "shooting (eFG%)",
                     "tov_pct": "ball security (TOV%)",
                     "oreb_pct": "offensive rebounding (OREB%)",
                     "ft_rate": "getting to the line (FT rate)",
                     "opp_efg_pct": "opponent shooting (opp eFG%)",
                     "opp_tov_pct": "forcing turnovers (opp TOV%)",
                     "dreb_pct": "defensive rebounding (DREB%)",
                     "opp_ft_rate": "opponent FT rate"}
        for key, label in ff_labels.items():
            pct = ff_pct.get(team, {}).get(key)
            if pct is None:
                continue
            if pct >= 65:
                strengths.append(f"{label} ({_ord(pct)} pct)")
            elif pct <= 35:
                weaknesses.append(f"{label} ({_ord(pct)} pct)")
        for k in SKILLS:
            if skills[k] >= 65:
                strengths.append(f"{k} ({_ord(skills[k])} pct, "
                                 "rotation skill)")
            elif skills[k] <= 35:
                weaknesses.append(f"{k} ({_ord(skills[k])} pct, "
                                  "rotation skill)")
        star_gaps = [k for k in SKILLS if k != "bench_scoring"
                     and star_pct.get(k, 50.0) <= 35]
        star_strengths = [k for k in SKILLS if k != "bench_scoring"
                          and star_pct.get(k, 50.0) >= 75]
        profiles[team] = {
            "team": team,
            "adj_o": round(t["adj_o"], 1), "adj_d": round(t["adj_d"], 1),
            "net": round(t["net"], 1), "adj_t": round(t["adj_t"], 1),
            "gp": t["games"],
            "four_factors": {k: (round(ff[team][k], 3)
                                 if ff[team].get(k) is not None else None)
                             for k in ("efg_pct", "tov_pct", "oreb_pct",
                                       "ft_rate", "opp_efg_pct",
                                       "opp_tov_pct", "dreb_pct",
                                       "opp_ft_rate")},
            "four_factor_pct": ff_pct.get(team, {}),
            "skills": skills,
            "skill_raw": skill_raw,
            "star": ({"name": star["name"], "rpr": star_rpr,
                      "is_clear_star": (star_rpr is not None
                                        and star_rpr >= STAR_RPR_MIN),
                      "poss_pct": (round(star_usage, 1)
                                   if star_usage is not None else None),
                      "skill_pct": {k: round(v, 1)
                                    for k, v in star_pct.items()},
                      "strengths": star_strengths,
                      "gaps": star_gaps}
                     if star else None),
            "needs": needs,
            "ranked_needs": ranked_needs,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "rotation": [{"athlete_id": p["athlete_id"],
                          "name": p["name"],
                          "min": p["totals"]["min"],
                          "rpr": (p.get("ratings") or {}).get("rpr"),
                          "tier": tier_by_id.get((p["athlete_id"], p["team"]))}
                         for p in rotation],
        }
    return profiles, pct_by_player



def star_display_name(profile):
    """Label for a team's star in output.

    A star under RPR 1.5 is a role player on a rebuilding team
    that traded its star away, not a star, so the team is labelled
    "No clear star in this data" instead of naming him."""
    star = profile.get("star") or {}
    if star.get("is_clear_star"):
        return star.get("name", "")
    return "No clear star in this data"


def _ord(v):
    """1st, 2nd, 3rd, 4th... for a percentile value."""
    n = int(round(v))
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def _pct_of(vals, value):
    """Percentile of value within a list of numbers."""
    below = sum(1 for x in vals if x < value)
    equal = sum(1 for x in vals if x == value)
    return 100.0 * (below + 0.5 * equal) / len(vals) if vals else 50.0


def score_target(target, target_pct, target_tier, team, profile,
                 pct_by_player, players_by_team):
    """QualityGain / FitGain / TradeScore for one target vs one team.
    Returns None if the target is not a realistic suggestion."""
    if target["team"] == team:
        return None
    if target_tier not in ELIGIBLE_TIERS:
        return None
    if target["totals"]["gp"] < MIN_GP_TARGET:
        return None
    t_rpr = (target.get("ratings") or {}).get("rpr")
    # Target must be at least a league-average player by RPR.
    # In v1.2 the center is 0, so the floor is 0.0, not the 0.5
    # that was set on the v1.1 scale (where 0.5 was below
    # average): with 0.5 on the v1.2 scale only 10 players in
    # the league were eligible targets at all, and the same 10
    # topped every team's list. At 0.0 the eligible pool is 43.
    if t_rpr is None or t_rpr < 0.0:
        return None
    # A player who is one of the top 3 RPR players on his own team
    # is that team's star or co-star: not typically available.
    own = sorted(players_by_team.get(target["team"], []),
                 key=lambda p: ((p.get("ratings") or {}).get("rpr")
                                if (p.get("ratings") or {}).get("rpr")
                                is not None else -99), reverse=True)
    own_top = {p["athlete_id"] for p in own[:TOP_N_OWN_TEAM]}
    if target["athlete_id"] in own_top:
        return None
    # Role-player shape on his own team: not a full-time starter,
    # not a featured offensive option, not a 20-point scorer.
    # Tier alone does not prove this: early-season noise puts big
    # names in lower tiers (see the module docstring).
    if ((target.get("ratings") or {}).get("min_pct") or 0) >= MAX_MIN_PCT_TARGET:
        return None
    if target["per_game"]["min"] >= MAX_MPG_TARGET:
        return None
    if ((target.get("ratings") or {}).get("poss_pct") or 0) >= MAX_USAGE_TARGET:
        return None
    if target["per_game"]["pts"] >= MAX_PTS_TARGET:
        return None
    rotation = profile["rotation"]
    star = profile["star"] or {}
    star_usage = star.get("poss_pct") or 0
    target_usage = (target.get("ratings") or {}).get("poss_pct") or 0
    target_mpg = target["per_game"]["min"]
    # Best skill: the skill where this target most helps this team,
    # i.e. highest weighted need x target percentile.
    needs = profile["needs"]
    best_skill = max(
        SKILLS,
        key=lambda k: needs[k]["weight"] * (needs[k]["need"] / 60.0)
        * target_pct.get(k, 0))
    # Displaced player: worst rotation player at that skill, with
    # the top 2 RPR players on the team excluded (you do not bench
    # the star or the co-star for a role player: picking the worst
    # shooter on Miami would displace Bam Adebayo, RPR 7.42, a
    # top rotation player who happens to shoot few threes, and
    # make every shooting target look worthless).
    top2_names = {p["name"] for p in sorted(
        players_by_team.get(team, []),
        key=lambda p: ((p.get("ratings") or {}).get("rpr")
                       if (p.get("ratings") or {}).get("rpr") is not None
                       else -99), reverse=True)[:2]}
    rot_players = []
    for r in rotation:
        p = next((x for x in players_by_team.get(team, [])
                  if x["athlete_id"] == r.get("athlete_id")), None)
        if p is None:
            # Rotation entries written before athlete_id existed.
            p = next((x for x in players_by_team.get(team, [])
                      if x["name"] == r["name"]), None)
        if p is None or p["name"] in top2_names:
            continue
        rot_players.append(p)
    # A bench scorer takes bench minutes, not a starter's: every
    # starter's bench-scoring rate is 0 by construction, so without
    # this the displaced player for a bench-scoring target would
    # always be a starter (e.g. P.J. Washington for Dallas).
    if best_skill == "bench_scoring":
        bench = [p for p in rot_players
                 if ((p.get("ratings") or {}).get("min_pct") or 0) < 60]
        if bench:
            rot_players = bench
    if not rot_players:
        return None
    displaced = min(
        rot_players,
        key=lambda p: pct_by_player[(p["athlete_id"], p["team"])]
        .get(best_skill, 50.0))
    d_pct = pct_by_player[(displaced["athlete_id"], displaced["team"])]
    d_rpr = (displaced.get("ratings") or {}).get("rpr")
    if d_rpr is None:
        return None
    # Modest upgrade only: a big RPR jump is a star-for-star swap.
    if t_rpr - d_rpr > MAX_RPR_JUMP:
        return None
    d_mpg = displaced["per_game"]["min"]
    # FitGain over all six skills, concave in the delta.
    fit_gain = 0.0
    per_skill = {}
    for k in SKILLS:
        d_s = (target_mpg * target_pct.get(k, 0)
               - d_mpg * d_pct.get(k, 0)) / 240.0
        contrib = (needs[k]["weight"] * (needs[k]["need"] / 60.0)
                   * min(2.0, math.sqrt(max(d_s, 0.0) / 10.0)))
        per_skill[k] = round(contrib, 4)
        fit_gain += contrib
    if fit_gain <= 0:
        return None
    quality_gain = (t_rpr - d_rpr) * (target_mpg / 240.0)
    # Usage penalty: removed in v1.1. It never bound on real data
    # (0.0 for every target on the Nov 20 run: the target-usage
    # gate caps targets under 26% and the highest star usage was
    # 40.7%, so the largest possible penalty was about 0.03
    # against TradeScores of roughly 0.3-0.5, and it was 0.0 in
    # practice). A penalty that never binds is decoration, so it
    # is gone; usage_penalty is kept at 0.0 in the output for
    # backward compatibility. If a real redundancy cost is ever
    # fitted against post-trade Adj Net, it can come back with a
    # fitted coefficient.
    usage_penalty = 0.0
    avail = 0.0
    if target["totals"]["gp"] < 10:
        avail += 0.10 * (quality_gain + fit_gain)
    if ((target.get("ratings") or {}).get("min_pct") or 0) < 40:
        avail += 0.10 * (quality_gain + fit_gain)
    trade_score = quality_gain + fit_gain - avail
    if trade_score <= 0:
        return None
    return {
        "name": target["name"], "team": target["team"],
        "position": target["position"], "tier": target_tier,
        "gp": target["totals"]["gp"],
        "min_pct": (round((target.get("ratings") or {}).get("min_pct"), 1)
                    if (target.get("ratings") or {}).get("min_pct")
                    is not None else None),
        "rpr": round(t_rpr, 2),
        "poss_pct": (round(target_usage, 1)
                     if target_usage is not None else None),
        "ast_per36": round(target["eval"]["per36"]["ast"], 1),
        "pts_per36": round(target["eval"]["per36"]["pts"], 1),
        "reb_per36": round(target["eval"]["per36"]["reb"], 1),
        "blk_per36": round(target["eval"]["per36"]["blk"], 1),
        "fg3m_per36": round(target["totals"]["fg3m"]
                            / target["totals"]["min"] * 36, 2)
        if target["totals"]["min"] else 0.0,
        "fills_need": best_skill,
        "per_skill_fit": per_skill,
        "displaced": {"name": displaced["name"], "rpr": round(d_rpr, 2),
                      "mpg": round(d_mpg, 1)},
        "quality_gain": round(quality_gain, 3),
        "fit_gain": round(fit_gain, 3),
        "usage_penalty": round(usage_penalty, 3),
        "availability_discount": round(avail, 3),
        "trade_score": round(trade_score, 3),
    }


def blocking_reason(target, tier, players_by_team):
    """Why the best player at a skill is not a realistic target."""
    r = (target.get("ratings") or {}).get("rpr")
    if tier not in ELIGIBLE_TIERS:
        return f"{tier} tier"
    own = sorted(players_by_team.get(target["team"], []),
                 key=lambda p: ((p.get("ratings") or {}).get("rpr")
                                if (p.get("ratings") or {}).get("rpr")
                                is not None else -99), reverse=True)
    if target["athlete_id"] in {p["athlete_id"]
                                for p in own[:TOP_N_OWN_TEAM]}:
        return "top-3 RPR on his own team"
    if ((target.get("ratings") or {}).get("min_pct") or 0) >= MAX_MIN_PCT_TARGET:
        return "full-time starter on his own team"
    if target["per_game"]["min"] >= MAX_MPG_TARGET:
        return "30+ MPG on his own team"
    if ((target.get("ratings") or {}).get("poss_pct") or 0) >= MAX_USAGE_TARGET:
        return "featured offensive option on his own team"
    if target["per_game"]["pts"] >= MAX_PTS_TARGET:
        return "20+ PPG scorer on his own team"
    if target["totals"]["gp"] < MIN_GP_TARGET:
        return "under 8 games"
    return "scored but fills another need better"


def build_targets(profiles, players, tier_by_id, pct_by_player):
    players_by_team = defaultdict(list)
    for p in players:
        players_by_team[p["team"]].append(p)
    out = {}
    for team, profile in profiles.items():
        scored = []
        for target in players:
            if target["team"] == team:
                continue
            t_pct = pct_by_player[(target["athlete_id"], target["team"])]
            tier = tier_by_id.get((target["athlete_id"], target["team"]), "Rotation / depth")
            s = score_target(target, t_pct, tier, team, profile,
                             pct_by_player, players_by_team)
            if s is not None:
                scored.append(s)
        scored.sort(key=lambda s: s["trade_score"], reverse=True)
        by_need = {}
        unfilled = {}
        for need in profile["ranked_needs"][:3]:
            sel = [s for s in scored if s["fills_need"] == need][:3]
            # If nothing fills the top need as its best skill, fall
            # back to targets with a positive contribution there.
            if not sel:
                sel = [s for s in scored
                       if s["per_skill_fit"].get(need, 0) > 0][:3]
            by_need[need] = sel
            if not sel:
                # Honest empty, not a missing list: rim protection
                # is scarce, and the best shot-blockers are stars
                # or core players on their own teams. Say who the
                # best at the skill are and why they are blocked.
                best = sorted(
                    (p for p in players
                     if p["team"] != team
                     and p["totals"]["gp"] >= MIN_GP_TARGET),
                    key=lambda p: pct_by_player[
                        (p["athlete_id"], p["team"])][need],
                    reverse=True)[:3]
                reasons = "; ".join(
                    f"{p['name']} ({p['team']}): "
                    f"{blocking_reason(p, tier_by_id.get((p['athlete_id'], p['team']), 'Rotation / depth'), players_by_team)}"
                    for p in best)
                unfilled[need] = ("No realistic target fills this need. "
                                  f"Best at the skill league-wide: {reasons}.")
        out[team] = {"top_overall": scored[:8], "by_need": by_need,
                     "unfilled_needs": unfilled, "_scored": scored}
    # Top distinct targets: the same best role players top many
    # teams' lists (Paul Reed topped 26 on the Nov 20 run), so
    # each team also gets its best targets who do NOT top many
    # other teams' lists. This does not cap repetition or hide
    # the best fit: top_overall is unchanged and still shown
    # first. It is a second list for readers who want a target
    # that is not on every other team's list.
    _top_counts = defaultdict(int)
    for team in out:
        if out[team]["top_overall"]:
            _top_counts[out[team]["top_overall"][0]["name"]] += 1
    for team in out:
        distinct = [s for s in
                    sorted(out[team].get("_scored", []),
                           key=lambda s: s["trade_score"], reverse=True)
                    if _top_counts.get(s["name"], 0) <= 2][:3]
        out[team]["top_distinct"] = distinct
        out[team].pop("_scored", None)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default=None)
    ap.add_argument("--need", default=None,
                    help="shooting, playmaking, rebounding, "
                         "rim_protection, perimeter_defense, "
                         "bench_scoring")
    args = ap.parse_args(argv)
    if args.need and args.need not in SKILLS:
        raise SystemExit(f"unknown need {args.need!r}; try {SKILLS}")
    teams_doc, players, tier_by_id, games = load_inputs()
    profiles, pct_by_player = build_profiles(teams_doc, players,
                                             tier_by_id, games)
    targets = build_targets(profiles, players, tier_by_id, pct_by_player)
    payload = {
        "season": SEASON,
        "games_included": teams_doc.get("info", {}).get("n_games"),
        "player_count": len(players),
        "method": ("Team needs v1.1, box-score only. Four Factors "
                   "(Oliver) + minutes-weighted rotation skill "
                   "profile, star-anchored need weights, concave "
                   "FitGain, QualityGain vs the displaced player. "
                   "RPR here is RPR v1.2 (Nov 20, 2026): ORtg "
                   "shrunk toward the league average (K = 200 "
                   "possessions used) and opponent-adjusted, with "
                   "the counting terms centered on the league-"
                   "average per-36 rates; "
                   "rpr_v1 and rpr_v1_1 are the old values. "
                   "Bench scoring is percentile-ranked bench-only "
                   "and the team bench skill is bench-only; "
                   "perimeter defence is STL plus half-weight BLK "
                   "per 36. The usage penalty was removed in v1.1 "
                   "(it never bound). Each team also gets a "
                   "top_distinct list: its best targets who do not "
                   "top many other teams' lists. "
                   "Availability filter: only Rotation / depth, "
                   "Elite role player, Undervalued / buy-low and "
                   "High-value starter tiers; top-3 RPR players on "
                   "their own team excluded; target under 55% Min, "
                   "under 30 MPG, under 26% usage, under 20 PPG; "
                   "RPR jump over the displaced player capped at 5.0. "
                   "Teams whose highest-RPR qualifying player is under "
                   "RPR 1.5 are labelled no clear star in this data. "
                   "No contracts "
                   "or salaries: none exist in the inputs and none "
                   "are invented. No lineup/tracking data: this is "
                   "a box-score fit, a prior for lineup data, not "
                   "a lineup prediction."),
        "skills": SKILLS,
        "target_level_pct": TARGET_LEVEL,
        "eligible_tiers": sorted(ELIGIBLE_TIERS),
        "teams": {team: {**profiles[team], "targets": targets[team]}
                  for team in profiles},
    }
    (DATA / "team_needs_2026_27.json").write_text(json.dumps(payload))
    # Flat CSV: one row per suggested target.
    fields = ["team", "team_net", "star", "top_need", "target",
              "target_team", "target_tier", "fills_need",
              "target_rpr", "displaced", "displaced_rpr",
              "quality_gain", "fit_gain", "usage_penalty",
              "availability_discount", "trade_score"]
    with open(DATA / "team_needs_2026_27.csv", "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for team in sorted(profiles):
            prof = profiles[team]
            for s in targets[team]["top_overall"]:
                w.writerow({
                    "team": team, "team_net": prof["net"],
                    "star": star_display_name(prof),
                    "top_need": prof["ranked_needs"][0],
                    "target": s["name"], "target_team": s["team"],
                    "target_tier": s["tier"], "fills_need": s["fills_need"],
                    "target_rpr": s["rpr"],
                    "displaced": s["displaced"]["name"],
                    "displaced_rpr": s["displaced"]["rpr"],
                    "quality_gain": s["quality_gain"],
                    "fit_gain": s["fit_gain"],
                    "usage_penalty": s["usage_penalty"],
                    "availability_discount": s["availability_discount"],
                    "trade_score": s["trade_score"]})
    print(f"Wrote data/team_needs_2026_27.json + .csv: "
          f"{len(profiles)} teams, "
          f"{sum(len(targets[t]['top_overall']) for t in targets)} "
          f"top-overall targets")
    if args.team:
        team = next((t for t in profiles
                     if t.lower() == args.team.lower()), None)
        if team is None:
            raise SystemExit(f"no team matching {args.team!r}")
        prof = profiles[team]
        print(f"\n{team} (Net {prof['net']}, AdjO {prof['adj_o']}, "
              f"AdjD {prof['adj_d']})")
        print(f"Star: {star_display_name(prof)} "
              f"(RPR {(prof['star'] or {}).get('rpr')}, "
              f"gaps: {', '.join((prof['star'] or {}).get('gaps') or []) or 'none'})")
        print(f"Strengths: {'; '.join(prof['strengths']) or 'none flagged'}")
        print(f"Weaknesses: {'; '.join(prof['weaknesses']) or 'none flagged'}")
        print("Needs (ranked): " + ", ".join(
            f"{k} (skill {prof['needs'][k]['team_skill_pct']:.0f}th, "
            f"need {prof['needs'][k]['need']:.0f}, "
            f"w {prof['needs'][k]['weight']})"
            for k in prof["ranked_needs"]))
        lst = targets[team]["top_overall"]
        if args.need:
            lst = [s for s in lst if s["fills_need"] == args.need]
            if not lst:
                lst = targets[team]["by_need"].get(args.need, [])
            print(f"\nTop realistic targets for {args.need}:")
        else:
            print("\nTop realistic targets:")
        for s in lst[:8]:
            print(f"  {s['name']} ({s['team']}, {s['tier']}, "
                  f"RPR {s['rpr']}) fills {s['fills_need']}, "
                  f"displaces {s['displaced']['name']} "
                  f"(RPR {s['displaced']['rpr']}), "
                  f"Q {s['quality_gain']:+.3f} F {s['fit_gain']:+.3f} "
                  f"-> {s['trade_score']:.3f}")
        return 0
    if args.need:
        all_targets = []
        for team in profiles:
            for s in targets[team]["top_overall"]:
                if s["fills_need"] == args.need:
                    all_targets.append((team, s))
        all_targets.sort(key=lambda ts: ts[1]["trade_score"], reverse=True)
        print(f"\nTop 10 realistic targets for {args.need}, league-wide:")
        for team, s in all_targets[:10]:
            print(f"  {s['name']} ({s['team']}, {s['tier']}, "
                  f"RPR {s['rpr']}) -> {team}, fills {s['fills_need']}, "
                  f"Q {s['quality_gain']:+.3f} F {s['fit_gain']:+.3f} "
                  f"-> {s['trade_score']:.3f}")
        return 0
    for team in sorted(profiles, key=lambda t: profiles[t]["net"],
                       reverse=True):
        prof = profiles[team]
        top = targets[team]["top_overall"][0] if targets[team]["top_overall"] else None
        print(f"{team}: top need {prof['ranked_needs'][0]}, "
              f"star {star_display_name(prof)}, "
              f"top target "
              f"{top['name'] + ' (' + top['team'] + ')' if top else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
