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
- `fetch_rosters.py` - pulls all 30 team rosters once per season
  (see Rookies below) and writes `data/rosters_2026_27.json`.
- `player_profiles.py` - aggregates box scores into per-player profiles.
- `team_ratings.py` - AdjO / AdjD / Net / AdjT solver (damped Gauss-Seidel).
- `player_eval.py` - deeper evaluation layer (see below).
- `gm_board.py` - GM acquisition board (see below).
- `team_needs.py` - team needs and realistic trade-fit evaluator (see below).
- `build_page.py` - builds `index.html` plus `teams/` (one page per team).
- `daily_update.sh` - daily updater (fetch last 3 days, rebuild, push with approval tap). No cron set up yet.
- `data/games_2026_27.json` - 226 parsed games.
- `data/players_2026_27.json` / `.csv` - base player profiles.
- `data/teams_2026_27.json` - team ratings.
- `data/players_2026_27_eval.json` / `.csv` - eval layer.
- `data/gm_board_2026_27.json` / `.csv` - GM board.
- `data/team_needs_2026_27.json` / `.csv` - team needs and trade targets.
- `data/rosters_2026_27.json` - roster pull: 528 athletes, 75 rookies.
- `data/raw/scoreboard/2026-27/` and `data/raw/summary/` - raw ESPN JSON.
- `data/raw/roster/` - cached raw roster JSON, one file per team.

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

## Team Needs and Trade Fit (team_needs.py)

For each of the 30 teams: strengths and weaknesses, the star, ranked
needs, and the *realistic* players who would fill them. Method follows
`research_notes/nba-team-needs-trade-fit-20261120-1902/report.md`
(Dean Oliver's Four Factors, Nylon Calculus lineup-balance work).

- **Diagnose in percentiles, never raw totals.** AdjO/AdjD plus the
  Four Factors on both ends (eFG%, TOV%, OREB%, FT rate, and the
  defensive mirrors: opp eFG%, opp TOV%, DREB%, opp FT rate), plus a
  minutes-weighted skill profile from the top 9 rotation players:
  shooting (made 3s per 36), playmaking (AST per 36), rebounding
  (REB per 36), rim protection (BLK per 36), perimeter defense
  (STL per 36, a weak proxy: steals reward gambling and miss
  positional defense, flagged as such), bench scoring (PTS per 36
  for players under 60% Min). Strength is 65th percentile or better,
  weakness 35th or worse.
- **Needs are star-anchored.** The star is the highest-RPR player
  with at least 8 games and 40% of team minutes. Need = max(0, 60 -
  team skill percentile), weighted by base weights (shooting .25,
  playmaking .20, rebounding .15, rim protection .15, perimeter
  defense .15, bench scoring .10) times a star factor: 1.5 if the
  star is weak at that skill (35th pct or worse), 1.25 if below
  average, 0.5 if the star already supplies it (75th pct or better).
  A team whose point guard already facilitates gains little from
  another facilitator; a team whose star cannot defend needs a POA
  defender. Bench scoring is not a star skill (a starter's rate is
  0 by construction), so it never counts as a star gap.
- **A trade is scored as three numbers, not one.** The displaced
  player is the worst rotation player at the target's best skill,
  excluding the top 2 RPR players on the team (you do not bench the
  star or co-star for a role player); a bench scorer displaces a
  bench player, not a starter. QualityGain = (RPR target - RPR
  displaced) x (target MPG / 240). FitGain = sum over skills of
  weight x (Need / 60) x sqrt(max(dSkill, 0) / 10), where dSkill is
  the minutes-weighted percentile change: concave, so the first
  rebounder or playmaker counts far more than the second.
  TradeScore = QualityGain + FitGain - usage penalty - availability
  discount. Usage penalty is 0.02 per point of (star usage + target
  usage) over 65. Availability discount is 10% of (QualityGain +
  FitGain) if GP under 10, plus 10% if %Min under 40.
- **Realism / availability filter: no stars, ever.** Targets must be
  Rotation / depth, Elite role player, Undervalued / buy-low, or
  High-value starter; never Franchise cornerstone, All-Star level,
  or Overvalued / avoid. They must not be top-3 RPR on their own
  team, must play under 55% of team minutes and under 30 MPG, under
  26% usage, and under 20 PPG on their own team. Tier alone does not
  prove this: early-season noise puts big names in lower tiers (in
  this run LeBron James lands in Rotation / depth on ORtg_full 97.5
  and James Harden in Elite role player), so the minutes / usage /
  scoring gates do the work. A target must fill a real need
  (FitGain > 0) and the RPR jump over the displaced player is capped
  at 5.0. This run: 240 top targets across 30 teams, 8 per team, and
  no suggested target is a Franchise cornerstone or All-Star player.
  The one need with no realistic target (Detroit rebounding) is
  reported as such, with the reason: the best rebounders
  league-wide either fill another need better (Andre Drummond,
  Oscar Tshiebwe) or are Overvalued / avoid (Tarris Reed Jr.).
  "Realistic" is a data-only proxy for availability, never a claim
  a player is actually available.
- **Star clarity and scrambled teams.** Team assignments are as
  reported by ESPN's box scores and include known errors
  (Giannis listed on Miami, LeBron on Philadelphia); profiles
  are reported as fetched, not corrected, and the site says so
  on the index and every team page. A team whose highest-RPR
  qualifying player is under RPR 5.0 is labelled "No clear star
  in this data" instead of naming a role player as the star
  (six teams in this run: Golden State, Milwaukee, Brooklyn,
  Charlotte, Toronto, Utah).
- **Calibration honesty.** The per-skill FitGain cap (2.0) and
  the usage penalty (0.02 per point over a 65 budget) are
  tiebreakers and safety rails, not fitted effects. On this run
  the largest per-skill FitGain contribution is 0.164 and the
  theoretical maximum is about 0.4, so the cap never binds on
  real data; the largest possible usage penalty is about 0.03
  (highest star usage 40.7% plus the under-26% target gate).
  They only separate near-tied targets. v2 should fit both
  against post-trade Adj Net.
- **The lists barely differentiate.** Only 37 distinct players
  fill the 240 top-overall slots; Ausar Thompson appears on 26
  teams' lists, Ziaire Williams on 25, Paul Reed on 24, Ajay
  Mitchell on 23. That is expected, the best available role
  players are the best fit for every needy team, but the
  per-team lists read as nearly the same list. There is no
  asking-price or salary data to differentiate them; a cap on
  how many teams one player can top was considered and rejected
  because it would hide the true best fit for the later teams.

Usage:

```bash
python3 team_needs.py                              # all 30 teams
python3 team_needs.py --team "Los Angeles Lakers"  # one team
python3 team_needs.py --need playmaking            # top 10 for a need, league-wide
python3 team_needs.py --team "LA Clippers" --need playmaking
```

This run, spot checks: Lakers top need is rebounding (41st pct
rotation skill), star Luka Doncic with no star gaps, top targets
Ausar Thompson, Paul Reed, and Dean Wade, all rebounding role
players displacing Collin Sexton (RPR 0.85). Spurs top need is
shooting, top targets Ziaire Williams, Ajay Mitchell, and Paul
Reed. A playmaking-needy team gets Ajay Mitchell (Oklahoma City
Thunder) as its top playmaking target, not a star PG.

Limits: no contracts or salaries exist in the inputs, so none are
used and none are invented; whether a target's salary fits is
unknown. No lineup, on/off, tracking, or shot-location data in v1,
so this is a box-score fit, a prior that lineup data should update,
not a lineup prediction. Early season, about 15 games per team, so
percentiles are noisy and 3P% is the noisiest stat. Regular-season,
average-opponent only. Weights and the concave shape are hand-set
v1 choices, not fitted; v2 should fit them against post-trade Adj
Net. The ESPN team assignments in this run have known quirks (for
example Giannis is listed on Miami and LeBron on Philadelphia);
profiles are reported as fetched, not corrected.

## Rookies (fetch_rosters.py)

A rookie is a player in his first NBA season. The rule is exact
and comes from the ESPN roster endpoint, not from memory:
`is_rookie` is True exactly when the roster entry shows
`experience.years == 0`. `fetch_rosters.py` pulls all 30 rosters
(528 athletes, cached per team under `data/raw/roster/`) and
writes `data/rosters_2026_27.json`. `player_profiles.py` merges
it by athlete_id, never by name alone, adding `is_rookie`,
`experience_years`, `age`, `height_in`, and `weight_lbs` to the
base profiles and CSV; `player_eval.py` carries `is_rookie` and
`experience_years` into the eval CSV and JSON.

Verified against the live pull: the rule returns the 2026 draft
class (AJ Dybantsa, Cameron Boozer, Darryn Peterson, Nate Ament,
Koa Peat, Labaron Philon Jr., Darius Acuff Jr., Mikel Brown Jr.,
Aday Mara, Thomas Sorber, Yaxel Lendeborg, and the rest), 75
rookies on rosters, 58 of whom have played (57 distinct athletes:
Carson Cooper has one profile per team after changing teams, and
17 roster rookies have not played yet). The 2025 draft class are
second-year players in 2026-27, not rookies: Cooper Flagg and
Cedric Coward both show `experience.years == 2`. No veteran with
5+ years is in the rookie list.

Current best rookie by RPR (Nov 20, 2026, min 2 games and 40%
of team minutes, same floor as every leaderboard): Aday Mara
(Oklahoma City Thunder), RPR 3.72, 9.2 PPG in 15 games. Next are
Yaxel Lendeborg (3.48), Cameron Boozer (3.45), and Meleek Thomas
(3.44). The raw RPR leader among rookies is Norchad Omier
(3.92), but on only 5 games at 9.2% of minutes, so he sits in
the below-floor list, labelled as a small sample, not at the top
of the ranking. The site has a Rookies section (top 10 by RPR
plus notable below-floor rookies) and a Rookies only toggle on
the top-100 players table.

Ambiguous cases, handled conservatively: Johni Broome's ESPN
roster entry carries no experience value, so he is not labelled
a rookie (only an explicit 0 counts). Three players in the box
scores are on no current roster (Tristan Enaruna, Dalen Terry,
Lawson Lovering) and likewise have no experience or bio fields.
One rookie's roster team differs from his box-score team (Carson
Cooper: roster says Lakers, box scores say Memphis, where he
played); the roster never overrides the box-score team. Rosters
are fetched once per season, not by the daily update.

## Known gaps (not invented)

- Contracts and salaries: there is no free source. Spotrac and
  similar are paywalled and are not scraped. Any trade-value or
  salary-cap work needs a contract source added first.
- No tracking or on/off data in v1.

## Site (build_page.py, RonPom-style)

`python3 build_page.py` writes `index.html` at the repo root plus
one page per team under `teams/` (30 pages), from the ratings
files above. No network, stdlib only.

- **Team ratings table**: all 30 teams, rank / team / conf / W-L /
  AdjO / AdjD / Net / AdjT. The small number under each AdjO, AdjD,
  and AdjT value is that team's rank in the metric (1 is best;
  AdjD rank 1 is the best defense). Net rank is the Rk column.
  Click a column header to sort, use the filter box to search team
  names, and use the East / West tabs to filter by conference.
  Conf is East or West from a fixed map of the standard NBA
  alignment (ESPN's NBA teams endpoint carries no conference
  field). W-L is the record in the games the ratings are built
  from, so W+L equals GP.
- **Click a team row** to expand its game log and roster inline;
  **click a team name** to open that team's own page
  (`teams/<slug>.html`) with its ratings, game log, and roster.
  Rosters are sorted by minutes and carry each player's GM board
  tier. Game logs list date, opponent, venue (H/A/N), result, and
  score, in date order.
- **Player leaders**: top 5 by PTS, REB, AST, ORtg, and RPR.
  Same floor as RonPom's 2026-27 leaders: min 2 games and 40% of
  team minutes.
- **Top players**: top 100 by RPR, sortable and filterable, team
  names link to team pages. A Rookies only button filters the
  table to first-year players.
- **Rookies**: top 10 rookies by RPR (same min 2 games and 40%
  minutes floor), plus notable rookies below the floor, labelled
  as small samples. Best rookie so far: Aday Mara, RPR 3.72.
- **GM board**: acquisition tiers, same as `gm_board.py`.
- **Team needs**: one row per team with its star, top need, and top
  realistic target with QualityGain / FitGain / TradeScore, plus the
  needs with no realistic target and why. Team pages carry the same
  block (strengths, weaknesses, top 3 needs, top 3 targets).

## How to re-run daily

```bash
cd ~/workspace/nba-ratings
./daily_update.sh            # fetch last 3 days, rebuild everything, push (waits for approval tap)
./daily_update.sh --no-push  # rebuild locally only, no push
DAILY_UPDATE_PUSH=0 ./daily_update.sh
```

`daily_update.sh` mirrors RonPom's daily updater: it re-fetches
the last 3 days of scoreboards fresh (raw summaries stay cached),
exits without doing anything if no new games arrived, otherwise
rebuilds player profiles, team ratings, eval, GM board, and the
site, then prepares a GitHub push manifest (the rebuilt CSVs and
JSONs, `index.html`, and all 30 team pages) and pushes it, waiting
for Ron's approval tap. A sync-base check stops the push if the
branch head moved since the last sync. No cron is set up yet; the
script is the 6:00 AM Chicago pattern, ready to schedule.

Manual rebuild (same steps the script runs):

```bash
cd ~/workspace/nba-ratings
python3 fetch_nba.py --season 2026-27   # resumes per date; edit SEASONS in fetch_nba.py to extend the end date
python3 fetch_rosters.py                # once per season, not daily
python3 player_profiles.py
python3 team_ratings.py
python3 player_eval.py
python3 gm_board.py
python3 team_needs.py
python3 build_page.py
```

`daily_update.sh` runs the same chain except the roster pull
(rosters change slowly, so they are fetched once per season).
Its push manifest includes the rebuilt CSVs and JSONs,
`index.html`, and all 30 team pages.

## v2 ideas (not built)

- `nba_api` / stats.nba.com for advanced stats: PER, Box Plus/Minus,
  Win Shares, tracking (speed, distance, touches), shot charts, on/off.
  It is free with no key, but it rate-limits aggressively and blocks
  some cloud IPs, so it would be a second source, not a replacement
  for the ESPN fetch. The ESPN pipeline stays the reproducible base.
- Last-season prior for team ratings (like RonPom's noshrink prior).
- Team needs v2: lineup net ratings and RAPM / EPM / LEBRON as the
  quality input instead of RPR, shot-location data, tracking for real
  gravity and rim deterrence, and learned archetype-interaction
  coefficients instead of the hand-set need weights and usage
  penalty. Back-test TradeScore against actual post-trade Adj Net
  changes; if it does not beat RPR difference alone, the fit term
  is decoration and should say so.
