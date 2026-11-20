"""Fetch NBA team rosters from free ESPN endpoints (no key).

Pulls one roster per team from:
  https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{id}/roster
and writes data/rosters_2026_27.json: one slim record per athlete
(athlete_id, name, team, team_id, position, jersey, age, height_in,
weight_lbs, experience_years, is_rookie).

Rookie rule: is_rookie is True exactly when experience.years == 0,
i.e. this is the player's first NBA season. Verified against the
live roster pull: it returns the 2026 draft class (AJ Dybantsa,
Cameron Boozer, Darryn Peterson, Nate Ament, Koa Peat, and the
rest), while 2025 draftees are already at years 2 (Cooper Flagg
and Cedric Coward both show experience.years == 2 in 2026-27,
so they are second-year players, not rookies). ESPN's
experience.years counts completed seasons, so a player in his
first season shows 0. No draft list is hardcoded and no rookie
is labelled from memory; the roster payload is the source.

Matching: match players to profiles by athlete_id, never by name
alone. The box-score data has ESPN's scrambled team assignments
in places (Giannis on Miami, LeBron on Philadelphia, reported
as fetched), so a roster's team name must never override the
team a player actually played for in the box scores; the roster
file only supplies experience / age / height / weight.

Checkpointing: raw roster JSON is cached per team under
data/raw/roster/{team_id}.json and reused on re-run, so a
re-run is free. Run once per season (rosters change slowly);
the daily update does not re-fetch them.

Stdlib only. Same headers and backoff style as fetch_nba.py:
0.3s between roster calls, long backoff on 403/429.

Usage:
    python3 fetch_rosters.py
        Writes data/rosters_2026_27.json and prints the rookie count.
    python3 fetch_rosters.py --team 6
        Fetch (or reuse the cache for) one team only, for testing.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
RAW_ROSTER = DATA / "raw" / "roster"
SEASON = "2026-27"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.espn.com/',
}
ROSTER_BASE = ('https://site.api.espn.com/apis/site/v2/sports/'
               'basketball/nba/teams')
ROSTER_SLEEP = 0.3


def get_json(url, retries=4):
    """GET JSON with backoff (same policy as fetch_nba.py)."""
    backoffs_403 = [10, 60, 180, 300]
    backoffs_other = [2, 5, 10, 30]
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last_err = f'HTTP {e.code}'
            if e.code in (403, 429) and attempt < retries:
                time.sleep(backoffs_403[attempt])
                continue
            if e.code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(backoffs_other[attempt])
                continue
            raise
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(backoffs_other[attempt])
                continue
            raise
    raise RuntimeError(f'get failed after retries: {last_err}')


def load_teams():
    """(team_id, displayName) pairs from data/teams_parsed.json."""
    teams = json.loads((DATA / "teams_parsed.json").read_text())
    return [(str(t["id"]), t["displayName"]) for t in teams]


def slim_athlete(a, team_id, team_name):
    """One slim roster record. experience_years None if ESPN omits it."""
    exp = a.get("experience") or {}
    years = exp.get("years")
    pos = a.get("position") or {}
    return {
        "athlete_id": str(a.get("id", "")),
        "name": a.get("fullName", "") or a.get("displayName", ""),
        "team": team_name,
        "team_id": team_id,
        "position": pos.get("abbreviation", ""),
        "jersey": a.get("jersey", ""),
        "age": a.get("age"),
        "height_in": a.get("height"),
        "weight_lbs": a.get("weight"),
        "experience_years": years,
        "is_rookie": (years == 0),
    }


def fetch_rosters(team_filter=None):
    RAW_ROSTER.mkdir(parents=True, exist_ok=True)
    teams = load_teams()
    if team_filter:
        teams = [t for t in teams if t[0] == str(team_filter)]
        if not teams:
            raise SystemExit(f"no team with id {team_filter}")
    roster, seen, failures = [], set(), []
    for team_id, team_name in teams:
        cache = RAW_ROSTER / f"{team_id}.json"
        if cache.exists():
            try:
                payload = json.loads(cache.read_text())
            except ValueError:
                payload = None
        else:
            payload = None
        if payload is None:
            try:
                payload = get_json(f"{ROSTER_BASE}/{team_id}/roster")
            except Exception as e:  # noqa: BLE001 - logged, not hidden
                print(f"  {team_name} ({team_id}): FAILED {e}")
                failures.append(team_id)
                continue
            cache.write_text(json.dumps(payload))
            time.sleep(ROSTER_SLEEP)
        for a in payload.get("athletes", []) or []:
            rec = slim_athlete(a, team_id, team_name)
            if not rec["athlete_id"] or rec["athlete_id"] in seen:
                continue
            seen.add(rec["athlete_id"])
            roster.append(rec)
    return roster, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default=None,
                    help="fetch one team id only (testing)")
    args = ap.parse_args()
    roster, failures = fetch_rosters(args.team)
    out = {
        "season": SEASON,
        "source": ("ESPN roster endpoint, experience.years == 0 "
                   "marks a rookie (first NBA season)."),
        "athlete_count": len(roster),
        "rookie_count": sum(1 for r in roster if r["is_rookie"]),
        "roster": roster,
    }
    if not args.team:
        (DATA / "rosters_2026_27.json").write_text(json.dumps(out))
        print(f"Wrote data/rosters_2026_27.json: {len(roster)} athletes, "
              f"{out['rookie_count']} rookies "
              f"(experience.years == 0), {len(failures)} failures")
        for name in sorted(r["name"] for r in roster if r["is_rookie"]):
            print(f"  rookie: {name}")
    else:
        print(f"Team {args.team}: {len(roster)} athletes, "
              f"{sum(1 for r in roster if r['is_rookie'])} rookies")
    if failures:
        print(f"Failed teams: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
