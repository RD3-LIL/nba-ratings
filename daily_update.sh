#!/bin/bash
# NBA Ratings daily updater.
#
# Fetches the last few days of 2026-27 games, rebuilds player
# profiles, team ratings, the eval layer, the GM board, the team
# needs / trade fit evaluator, runs the RPR validator, regenerates
# the run summary and README numbers, and rebuilds the site
# (index.html plus one page per team under teams/), then
# pushes all of those files to GitHub: the rebuilt CSVs and
# JSONs, the validation and run-summary outputs, the README and
# the research page, the index, every teams/*.html page, and
# every players/*.html profile page. The
# sync record lists exactly the paths in the manifest, so it never
# lies about what a push contained.
#
# Why the last 3 days, not just yesterday: a date fetched in
# the morning is marked done, but games played that evening
# are not in yet. Each run re-fetches the last 3 days of
# scoreboards AND summaries fresh (cached scoreboard files for
# those dates are deleted first, the dates are removed from
# done_dates, and the raw summary files for the window's events
# are deleted so the box scores are re-pulled, not reused), so
# evening games and late box-score corrections land on the next
# morning's run. Rosters are re-fetched weekly (see below).
# The validator runs after the rebuild and stops the push on a
# regression; the run summary regenerates the README numbers.
#
# Dates are Chicago dates (TZ=America/Chicago). ESPN's
# scoreboard is keyed by date the same way the original pull
# was done, and a 3-day window absorbs late west-coast games
# that roll to the next date.
#
# Push: github push-files needs Ron's approval on a card each
# time it runs. A scheduled run therefore prepares everything
# and then waits for his one tap to push. If nothing changed
# (no new games), it exits without pushing.
#
# No cron is set up yet; this script is the 6:00 AM Chicago
# pattern from RonPom's daily_update.sh, ready to schedule.
#
# Usage:
#   daily_update.sh [--no-push]
#   DAILY_UPDATE_PUSH=0 daily_update.sh
set -u
cd /home/hatch/workspace/nba-ratings || exit 1

NO_PUSH=0
if [ "${1:-}" = "--no-push" ] || [ "${DAILY_UPDATE_PUSH:-1}" = "0" ]; then
  NO_PUSH=1
fi

export TZ=America/Chicago
TODAY=$(date +%Y%m%d)
START=$(date -d "$TODAY -3 days" +%Y%m%d)
echo "$(date -Iseconds) daily update: window $START..$TODAY"

GAMES_BEFORE=0
if [ -f data/games_2026_27.json ]; then
  GAMES_BEFORE=$(python3 -c "import json; print(len(json.load(open('data/games_2026_27.json'))))")
fi

# Clear the window's cached scoreboards and done_dates so the
# fetch re-pulls those dates fresh. Also delete the raw summary
# files for the window's events, so the box scores are re-fetched
# too (a cached summary would otherwise be reused and a late
# box-score correction would never land). Parsed files for the
# window are rewritten by the fetch, since the dates leave
# done_dates.
python3 - "$START" "$TODAY" <<'PYEOF'
import json, sys
from datetime import date, timedelta
from pathlib import Path
start = date(int(sys.argv[1][:4]), int(sys.argv[1][4:6]), int(sys.argv[1][6:8]))
end = date(int(sys.argv[2][:4]), int(sys.argv[2][4:6]), int(sys.argv[2][6:8]))
days = []
d = start
while d <= end:
    days.append(d.strftime('%Y%m%d'))
    d += timedelta(days=1)
for ds in days:
    p = Path(f'data/raw/scoreboard/2026-27/{ds}.json')
    if p.exists():
        p.unlink()
        print(f'  cleared cached scoreboard {ds}')
# Raw summaries for events in the window (from the games file,
# which is the pre-fetch snapshot; pre-game events have no
# summary file and are skipped silently).
games_path = Path('data/games_2026_27.json')
n_sum = 0
if games_path.exists():
    games = json.loads(games_path.read_text())
    for g in games:
        if g["date"] in days:
            sp = Path(f"data/raw/summary/{g['event_id']}.json")
            if sp.exists():
                sp.unlink()
                n_sum += 1
print(f'  cleared {n_sum} cached summaries in window')
state_path = Path('data/fetch_state.json')
state = json.loads(state_path.read_text())
sstate = state.get('season_2026-27', {})
done = set(sstate.get('done_dates', []))
removed = sorted(set(days) & done)
sstate['done_dates'] = sorted(done - set(days))
state['season_2026-27'] = sstate
state_path.write_text(json.dumps(state, indent=2))
print(f'  removed {len(removed)} dates from done_dates: {removed}')
PYEOF
# The heredoc terminator above must stand alone on its line, so
# the guard is a $? check, not "PYEOF || ...". A corrupt
# fetch_state.json used to make this step raise and the script
# carry on, skipping already-done dates and exiting 0 with
# "no new games" while updating nothing.
if [ "$?" -ne 0 ]; then
  echo "failed to clear scoreboards / fetch state, stopping: fetch_state.json may be corrupt, fix it before re-running"
  exit 1
fi

# Rosters weekly: if the roster file is older than 7 days, clear
# the raw roster cache and re-pull. A roster change (a debut, a
# mid-season signing, a corrected experience value) forces a
# rebuild even with no new games, so the hash is compared below.
ROSTER_HASH_BEFORE=""
if [ -f data/rosters_2026_27.json ]; then
  ROSTER_HASH_BEFORE=$(sha256sum data/rosters_2026_27.json | cut -d' ' -f1)
fi
ROSTERS_CHANGED=0
if [ ! -f data/rosters_2026_27.json ] || [ -n "$(find data/rosters_2026_27.json -mtime +7 -print 2>/dev/null)" ]; then
  echo "rosters older than 7 days (or missing), re-fetching"
  rm -f data/raw/roster/*.json
  python3 fetch_rosters.py || {
    echo "roster fetch failed, stopping (rosters feed is_rookie/age/height/weight)"
    exit 1
  }
  ROSTER_HASH_AFTER=$(sha256sum data/rosters_2026_27.json | cut -d' ' -f1)
  if [ "$ROSTER_HASH_AFTER" != "$ROSTER_HASH_BEFORE" ]; then
    ROSTERS_CHANGED=1
    echo "rosters changed, rebuild will run even with no new games"
  fi
fi

# Fetch failures are logged, not fatal, to fetch_nba.py, so a
# partial fetch would otherwise rebuild and push a partial day.
# Record the failure count before the fetch and stop if it grew.
FAILURES_BEFORE=0
if [ -f data/fetch_failures.jsonl ]; then
  FAILURES_BEFORE=$(wc -l < data/fetch_failures.jsonl)
fi

python3 fetch_nba.py --season 2026-27 --start "$START" --end "$TODAY" || {
  echo "fetch failed, stopping (state is checkpointed, retry tomorrow)"
  exit 1
}

FAILURES_AFTER=0
if [ -f data/fetch_failures.jsonl ]; then
  FAILURES_AFTER=$(wc -l < data/fetch_failures.jsonl)
fi
if [ "$FAILURES_AFTER" -gt "$FAILURES_BEFORE" ]; then
  echo "fetch had $((FAILURES_AFTER - FAILURES_BEFORE)) new failure(s) (see data/fetch_failures.jsonl), stopping before rebuild"
  exit 1
fi

GAMES_AFTER=$(python3 -c "import json; print(len(json.load(open('data/games_2026_27.json'))))")
echo "games: $GAMES_BEFORE -> $GAMES_AFTER"
if [ "$GAMES_AFTER" -eq "$GAMES_BEFORE" ] && [ "$ROSTERS_CHANGED" -eq 0 ]; then
  echo "no new games and no roster change, nothing to rebuild or push"
  exit 0
fi

python3 player_profiles.py --season 2026-27 || exit 1
python3 team_ratings.py || exit 1
python3 player_eval.py || exit 1
python3 sharp_rating.py || exit 1
python3 gm_board.py || exit 1
python3 team_needs.py || exit 1
# Validator gate: stops the run (and the push) on a regression.
# validate_rpr_v11.py exits 1 if a gate fails.
python3 validate_rpr_v11.py || {
  echo "RPR validation failed, stopping before site build and push"
  exit 1
}
python3 generate_run_summary.py || exit 1
python3 build_page.py || exit 1
python3 build_player_pages.py || exit 1
python3 build_distributions.py || exit 1

if [ "$NO_PUSH" -eq 1 ]; then
  echo "push skipped (--no-push), files rebuilt locally"
  exit 0
fi

# The manifest is the rebuilt CSVs and JSONs, the run summary and
# validation outputs, the README and research page (both are
# regenerated from the run), the index, and every team page
# build_page.py just wrote. nullglob so an (impossible after a
# successful build) empty teams/ dir would expand to nothing
# instead of a literal "teams/*.html".
shopt -s nullglob
TEAM_PAGES=(teams/*.html)
PLAYER_PAGES=(players/*.html)
# Player pages are ~8.6 MB across 476 files on the Nov 20 run, so
# the push below is much larger than before; if the single
# push-files call fails with HTTP 413, split the player pages
# into batches of about 90 files / 1.7 MB each, as RonPom does.
printf '%s\0' data/players_2026_27.csv data/players_2026_27.json data/players_2026_27_eval.csv data/players_2026_27_eval.json data/sharp_2026_27.csv data/sharp_2026_27.json data/gm_board_2026_27.csv data/gm_board_2026_27.json data/team_needs_2026_27.csv data/team_needs_2026_27.json data/teams_2026_27.json data/games_2026_27.json data/rosters_2026_27.json data/fetch_state.json data/run_summary.json data/validation.json README.md rpr-research.html distributions.html index.html "${TEAM_PAGES[@]}" "${PLAYER_PAGES[@]}" > /home/hatch/workspace/nba-ratings-push-manifest.bin

# Sync-base check: the branch head must match the last sync
# record entry, or someone else pushed since our last run.
# If it moved, stop and let a human reconcile; never push
# blind over an unknown head.
SYNC_RECORD=/home/hatch/workspace/nba-ratings.muse-github-snapshot-sync.json
LAST_SHA=$(python3 -c "import json; print(json.load(open('$SYNC_RECORD'))[-1]['commit_sha'])")
LIVE_SHA=$(/opt/hatch/bin/github call-read-tool --name list_commits --arguments-json '{"owner":"RD3-LIL","repo":"nba-ratings","branch":"main","perPage":1}' 2>/dev/null | python3 -c "import sys, json; d = json.load(sys.stdin); print(json.loads(d['result']['content'][0]['text'])[0]['sha'])")
if [ "$LIVE_SHA" != "$LAST_SHA" ]; then
  echo "branch head moved ($LAST_SHA -> $LIVE_SHA), stopping without pushing"
  exit 1
fi

PUSH_OUT=$(/opt/hatch/bin/github push-files --owner RD3-LIL --repo nba-ratings --branch main \
  --message "Daily update: $GAMES_AFTER games through $TODAY" \
  --source-dir /home/hatch/workspace/nba-ratings \
  --paths-file /home/hatch/workspace/nba-ratings-push-manifest.bin 2>&1) || {
  echo "push failed or was not approved; files are rebuilt locally"
  echo "$PUSH_OUT"
  exit 1
}
NEW_SHA=$(echo "$PUSH_OUT" | python3 -c "import sys, json; d = json.load(sys.stdin); print(json.loads(d['result']['content'][0]['text'])['object']['sha'])" 2>/dev/null)
if [ -z "$NEW_SHA" ]; then
  echo "push output did not include a commit SHA; sync record NOT updated"
  echo "$PUSH_OUT"
  exit 1
fi
python3 - "$NEW_SHA" <<'PYEOF'
import json, os, sys, uuid
from pathlib import Path
rec_path = '/home/hatch/workspace/nba-ratings.muse-github-snapshot-sync.json'
records = json.load(open(rec_path))
# Paths come from the manifest just pushed, not a hardcoded
# list, so the record matches the commit exactly (including
# all the team pages).
manifest = Path('/home/hatch/workspace/nba-ratings-push-manifest.bin').read_bytes()
paths = [p for p in manifest.decode('utf-8').split('\0') if p]
records.append({
    'owner': 'RD3-LIL', 'repo': 'nba-ratings', 'branch': 'main',
    'source_dir': '/home/hatch/workspace/nba-ratings',
    'commit_sha': sys.argv[1],
    'paths': paths,
})
tmp = rec_path + '.tmp-' + uuid.uuid4().hex[:8]
with open(tmp, 'w') as f:
    json.dump(records, f, indent=2)
os.replace(tmp, rec_path)
print(f'sync record updated, head {sys.argv[1][:8]}')
PYEOF
echo "pushed daily update ($GAMES_AFTER games through $TODAY)"
