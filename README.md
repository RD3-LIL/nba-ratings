# NBA Ratings (RonPom-style, for the NBA)

KenPom-style player and team ratings for the NBA, built the same way as
RonPom (College Ratings) but for the 2026-27 NBA season. Free ESPN data
only, no key, no paywall, no stats.nba.com in v1.

## Source

Free ESPN public endpoints, no API key, no authentication:

- Scoreboard: `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD`
- Game summary (box score): `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}`
- Teams list: `.../basketball/nba/teams?limit=1000`

Headers: Mozilla user agent plus `Referer: https://www.espn.com/`.
Rate limit: 0.9s between summary calls, 0.3s between scoreboards.
On 403/429 the fetcher backs off long (10s, 60s, 180s) and retries.
Checkpointing is per date, so re-runs resume where they left off.
Failures are logged to `data/fetch_failures.jsonl`, never invented.

This run: Oct 20 to Nov 20, 2026. 226 games, 30 teams, 475 players,
0 failed fetches. Nov 20 games had not been played yet when fetched
(9 events, all pre-game), so the data effectively ends Nov 19.

## Files

- `fetch_nba.py` - pulls scoreboards and full raw summaries, parses games.
- `player_profiles.py` - aggregates box scores into per-player profiles.
- `team_ratings.py` - AdjO / AdjD / Net / AdjT solver (damped Gauss-Seidel).
- `player_eval.py` - deeper evaluation layer (see below).
- `gm_board.py` - GM acquisition board (see below).
- `data/games_2026_27.json` - 226 parsed games.
- `data/players_2026_27.json` / `.csv` - base player profiles.
- `data/teams_2026_27.json` - team ratings.
- `data/players_2026_27_eval.json` / `.csv` - eval layer.
- `data/gm_board_2026_27.json` / `.csv` - GM board.
- `data/raw/scoreboard/2026-27/` and `data/raw/summary/` - raw ESPN JSON.

## Base ratings (same formulas as RonPom)

- Possessions used = FGA + 0.44 * FTA + TO.
- ORtg (approx) = 100 * PTS / possessions used. Simplified Dean Oliver:
  points produced divided by possessions used, with no assist credit
  and no offensive-rebound adjustment, so it underrates playmakers.
  Labelled approx in the output. Read it with %Poss.
- %Poss (usage) = Basketball-Reference usage formula:
  100 * (player poss used * (team MIN / 5)) / (player MIN * team poss used).
  20% is average, 25% a go-to guy.
- %Min = player MIN / (team games x 48). NBA games are 48 minutes,
  not 40. Slightly high in overtime games.
- RPR = our own single-number rating, in the spirit of KenPom's kPOY
  but a different, documented formula (kPOY is proprietary):
  (ORtg - 100) * poss used per game / 100
  + 0.5 * (AST - TO) per game
  + (STL + BLK) per game
  + 0.25 * REB per game.
  Rough scale: a star is about +6 to +10, an average rotation player
  about +1 to +3, below average about 0 or negative.
- Leaderboards use a minutes floor: at least 40% of team minutes and
  at least 1 game, so a two-minute cameo cannot top the ORtg list.
- Team ratings: damped Gauss-Seidel solve (damping 0.5, prior weight
  300 possessions, league-average prior, home court 0), same as
  RonPom's solver.py. No D1 filtering: all 30 NBA teams are included.
  No prior from last season yet, so early-season team ratings lean on
  the league average.
- Per-36 is the NBA standard and is included alongside per-40
  (kept for comparability with RonPom).
- Two players changed teams mid-season in this data (Nique Clifford,
  OKC/Sacramento; Carson Cooper, Lakers/Memphis). They get one profile
  per team, as in RonPom.

## Evaluation (player_eval.py)

On top of the base ORtg / %Poss / RPR, per player:

- ORtg_full: a Dean Oliver / Basketball-Reference style offensive
  rating in points form (see `_oliver_ortg` in player_eval.py).
  Points produced = PTS, minus the FG credit transferred to passers
  (qAST), plus assist credit (2 x BR ASTPart), plus OREB credit
  (OREB x team OREB% x team points per possession). Possessions are
  the same as the simplified ORtg, so the two are directly comparable.
  Two deliberate choices: assist credit is scaled per team so total
  assist credit equals total FG debit (assists transfer points, they
  do not create them; team points produced ends up 2-5% above team
  points scored, the OREB bonus only), and an OREB is credited in
  points only, not also subtracted from possessions (that would
  double-count it). Kept next to ORtg_simple so the difference is
  visible. Jokic is the textbook case: ORtg_simple 102.6, ORtg_full
  108.7, because the simplified version gives him no assist credit.
- Shooting profile: TS%, eFG%, FG/3P/FT, plus points breakdown
  (points from 2s vs 3s vs FTs and their shares).
- Playmaking: AST/TO ratio, AST per 100 possessions used, AST per
  100 team possessions (assist-rate proxy).
- Defense/rebounding: STL+BLK per game and per 36, REB per 36 split
  OREB/DREB, PF per 36.
- Volume vs efficiency quadrant: %Poss vs ORtg_full. High usage is
  25%+, efficient is ORtg_full 115+. The high-usage efficient
  quadrant is the rare and valuable one.
- RPR component breakdown: scoring term, playmaking term, defense
  term, rebounding term. They sum to RPR, so any RPR can be explained.
- Per-36 for all counting stats.
- Consistency from game logs (computed from raw summaries, never
  invented): standard deviation of points per game, games with 20+
  and 30+ points.
- Minutes/durability: GP, %Min, MPG.
- Trending: last-7-days PPG vs season PPG (window Nov 13-19, 2026),
  with games in the window. Needs at least 3 games in the window to
  rank.

Top 10s printed by `python3 player_eval.py`: most efficient
high-usage, best playmakers, best defenders (STL+BLK per 36), best
rebounders (REB per 36), trending up, biggest ORtg lift from assist
credit.

Limits: box-score only. No tracking data, no on/off, no lineup data,
no opponent adjustment at player level. ORtg_full runs hot for
low-usage players (a hyper-efficient bench big on few possessions
can post 130+), so always read it with %Poss; the high-usage lens
is the meaningful one. Early season, about 15 games per team, so
everything is noisy.

## GM Board (gm_board.py)

A GM acquisition board built on the eval output. Every label is a
threshold rule in `gm_board.py`, never reputation.

Archetypes (first match wins as primary; all matches are listed):
primary scorer (%Poss >= 26 and PTS per 36 >= 24), playmaker
(AST per 36 >= 7 and AST/TO >= 1.8), rim protector (BLK per 36 >= 2
and REB per 36 >= 8), rebounder (REB per 36 >= 11 and PTS per 36 < 20),
defensive playmaker (STL per 36 >= 2 and AST per 36 >= 4), two-way big
(REB per 36 >= 8 and STL+BLK per 36 >= 2 and PTS per 36 >= 12),
3-and-D (%Poss < 22 and 3PA per 36 >= 5 and 3P% >= 0.36 and STL+BLK
per 36 >= 1.5), specialist shooter (%Poss < 22 and 3P% >= 0.40 and
3PA per 36 >= 6), secondary scorer (%Poss 22-28 and PTS per 36 >= 18),
bench scorer (PTS per 36 >= 20 and %Min < 50), glue / role player
(%Min >= 50 and RPR 1-3 and STL+BLK per 36 >= 1). The usage gate on
3-and-D and specialist shooter exists so a high-usage ball-handler
is never labelled 3-and-D.

Acquisition tiers, checked in order:
- Franchise cornerstone: RPR >= 8, ORtg_full >= 108, %Min >= 55, GP >= 10.
- All-Star level: RPR >= 6, ORtg_full >= 104, %Min >= 50.
- High-value starter: RPR >= 4, %Min >= 50, ORtg_full >= 100.
- Undervalued / buy-low: GP >= 8 and (RPR >= 4 but %Min < 50, or
  RPR >= 3 with ORtg_full >= 120 at under 22% usage).
- Elite role player: RPR 2-4, %Min >= 55.
- Overvalued / avoid: high usage (%Poss >= 25) with ORtg_full < 100,
  or RPR < 0 at 20%+ usage, or 4+ turnovers per 36 with AST/TO < 1.2.
  This guard is checked first, so a high scorer with low efficiency
  and high turnovers cannot land in a top tier.
- Rotation / depth: everything else, including small-sample players
  (GP under 8 cannot be tiered; they sit here with flags).

Value signals: efficient at high usage (%Poss 25+ with ORtg_full
115+), two-way (STL+BLK per 36 >= 2 with PF per 36 <= 3.5),
availability (%Min 65+, GP 13+), shooting gravity (3PA per 36 >= 6,
3P% >= 0.37), playmaking (AST/TO >= 2), production per minute
(PTS per 36 >= 25), rebounding per minute (REB per 36 >= 11).

Red flags: high usage low efficiency, turnover prone (TO per 36 >= 4
or AST/TO < 1.0), foul prone (PF per 36 >= 4.5), low minutes /
availability risk (%Min < 40), small sample (GP < 8),
one-dimensional (scores 20+ per 36 with little rebounding, assists
or defense), early-season sample (under 13 games).

Fit lens (generic team needs, v1, not team-specific): shooting,
playmaking, rim protection, rebounding, perimeter defense, bench
scoring. `python3 gm_board.py --need shooting` returns the top 10
fits for a need (GP >= 8 required; shooting sorts by made 3s per 36,
not raw attempts).

This run: 6 Franchise cornerstone, 15 All-Star level, 41 High-value
starter, 31 Undervalued / buy-low, 24 Elite role player, 56
Overvalued / avoid, 302 Rotation / depth.

Limits: box-score only, no tracking, no on/off, no opponent
adjustment at player level, early season (about 15 games per team).
Tiers are a starting point for a target list, not a trade machine.

## Known gaps (not invented)

- Age, height, weight: not fetched in v1. ESPN roster data has them,
  but no roster pull was run for this build, so the fields are blank
  in the GM board rather than guessed.
- Contracts and salaries: there is no free source. Spotrac and
  similar are paywalled and are not scraped. Any trade-value or
  salary-cap work needs a contract source added first.
- No tracking or on/off data in v1.

## How to re-run daily

```bash
cd ~/workspace/nba-ratings
python3 fetch_nba.py --season 2026-27   # resumes per date; edit SEASONS in fetch_nba.py to extend the end date
python3 player_profiles.py
python3 team_ratings.py
python3 player_eval.py
python3 gm_board.py
```

## v2 ideas (not built)

- `nba_api` / stats.nba.com for advanced stats: PER, Box Plus/Minus,
  Win Shares, tracking (speed, distance, touches), shot charts, on/off.
  It is free with no key, but it rate-limits aggressively and blocks
  some cloud IPs, so it would be a second source, not a replacement
  for the ESPN fetch. The ESPN pipeline stays the reproducible base.
- Last-season prior for team ratings (like RonPom's noshrink prior).
- ESPN roster pull for age / height / weight.
- Team-need fit against a specific roster (v1 fit tags are generic).
