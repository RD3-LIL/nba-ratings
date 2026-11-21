"""Fetch NBA player salaries for 2026-27, v1 (display only).

Primary source: Basketball-Reference contracts page, free to view,
no key. One HTML table, 464 contracts for 2026-27 on the Nov 21
run, with each season's salary, guaranteed money, and the team.
The fetch is polite: one request, Mozilla user agent, 30 second
timeout, raw HTML cached under data/raw/salaries/ so a re-run
within 7 days reuses the cache instead of hitting the site again.
Contracts move on trade timescales, not daily, so this is a
weekly job at most, like the roster pull, not a daily one.

Matching: our players come from data/players_2026_27.csv (ESPN
box scores). Match by folded name (accents stripped, case and
punctuation ignored) plus team. Team is the BBRef team
abbreviation mapped to the full name; where BBRef lists a
different team than our box-score team (a trade BBRef has
recorded and our game data has not caught up with, or vice
versa), the row is still matched by name and the mismatch is
recorded, never silently overwritten. Players who changed teams
mid-season have one profile per team in our data and one row
in the BBRef table, so both of our profiles point at the same
BBRef row and the team mismatch is flagged for the profile whose
team differs. Salaries are never invented: an unmatched player
gets null, not a guess.

Output: data/salaries_2026_27.json and .csv, one row per player
in our data (475 on the Nov 21 run), plus per-team payrolls
(sums of matched 2026-27 salaries only, so they are partial
when coverage is incomplete) and a coverage report. The known
salary cross-checks (Curry, Jokic, Tatum) must pass or the
script exits 1: a wrong parse is worse than no salaries.

Cap lines for 2026-27, from the NBA announcement: salary cap
$164,961,000, luxury tax $200,428,000, first apron $209,015,000,
second apron $221,686,000, minimum team salary $148,465,000.
These are constants for the season, not fetched.

Stdlib only. No network calls except the one BBRef fetch.

Usage:
    python3 fetch_salaries.py
"""
import csv
import json
import re
import unicodedata
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
SEASON = "2026-27"
URL = "https://www.basketball-reference.com/contracts/players.html"
RAW_DIR = DATA / "raw" / "salaries"
RAW_FILE = RAW_DIR / "bbref_contracts_2026-27.html"
CACHE_DAYS = 7

CAP = 164961000
TAX = 200428000
FIRST_APRON = 209015000
SECOND_APRON = 221686000
SALARY_FLOOR = 148465000

# BBRef team abbreviation to full name, standard NBA alignment.
TEAM_MAP = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics",
    "BRK": "Brooklyn Nets", "CHO": "Charlotte Hornets",
    "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons", "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers", "PHO": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs", "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}

# Known salaries, cross-checked against the BBRef table and
# independent reporting. A mismatch means the parse is wrong.
# LeBron James and the $2,449,421 cluster were added Nov 21,
# 2026 after the audit that flagged them as looking like a
# scrape error: both are verbatim in the cached raw BBRef
# HTML (data/raw/salaries/bbref_contracts_2026-27.html).
# LeBron is listed at $3,876,529 for Philadelphia in 2026-27
# ($4,070,355 in 2027-28, guaranteed $3,876,529), and 32
# BBRef rows list exactly $2,449,421 for 2026-27 (DeRozan,
# Drummond, Conley among them), all single-year and fully
# guaranteed as listed. Those are BBRef's figures, used as
# listed; no salary is ever invented or adjusted here.
KNOWN = {
    "Stephen Curry": 62587158,
    "Nikola Jokic": 59033114,
    "Jayson Tatum": 58456566,
    "Anthony Davis": 58456566,
    "Giannis Antetokounmpo": 58456566,
    "LeBron James": 3876529,
    "DeMar DeRozan": 2449421,
    "Andre Drummond": 2449421,
    "Mike Conley": 2449421,
}


def fold(name):
    """Fold a name for matching: accents off, lower case, keep
    letters and digits only. Jokić and Jokic then match."""
    norm = unicodedata.normalize("NFKD", name or "")
    ascii_name = norm.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


def _money(text):
    """'$62,587,158' to int, '' to None."""
    if text is None:
        return None
    text = text.strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


class ContractsParser(HTMLParser):
    """Parse the BBRef player-contracts table (id player-contracts).

    Each row: rank, player (with data-append-csv id), team, y1..y6
    (one column per season, labelled in the header), guaranteed.
    Only td/th elements with a data-stat attribute are read, so
    layout changes elsewhere on the page do not break the parse.
    """

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_header = False
        self.headers = {}
        self.rows = []
        self._row = None
        self._stat = None
        self._text = ""
        self._player_id = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "player-contracts":
            self.in_table = True
            return
        if not self.in_table:
            return
        if tag == "thead":
            self.in_header = True
        if tag == "tr":
            self._row = {}
            self._player_id = None
        if tag in ("td", "th") and self._row is not None:
            self._stat = attrs.get("data-stat")
            self._text = ""
            if attrs.get("data-append-csv"):
                self._player_id = attrs["data-append-csv"]

    def handle_data(self, data):
        if self.in_table and self._stat is not None:
            self._text += data

    def handle_endtag(self, tag):
        if tag == "table" and self.in_table:
            self.in_table = False
            return
        if not self.in_table:
            return
        if tag == "thead":
            self.in_header = False
        if tag in ("td", "th") and self._stat is not None:
            if self.in_header:
                self.headers[self._stat] = self._text.strip()
            elif self._row is not None:
                self._row[self._stat] = self._text.strip()
                if self._player_id:
                    self._row["_bbref_id"] = self._player_id
            self._stat = None
            self._text = ""
        if tag == "tr" and self._row is not None:
            if self._row.get("player"):
                self.rows.append(self._row)
            self._row = None


def fetch_html():
    """Return the contracts page HTML, from cache if fresh."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_FILE.exists():
        age = (date.today() - date.fromtimestamp(
            RAW_FILE.stat().st_mtime)).days
        if age < CACHE_DAYS:
            print(f"Using cached {RAW_FILE} ({age} days old)")
            return RAW_FILE.read_text(encoding="utf-8")
    print(f"Fetching {URL} (one request, Mozilla UA)")
    req = urllib.request.Request(URL, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36"),
        "Referer": "https://www.basketball-reference.com/",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise SystemExit(f"BBRef fetch failed: HTTP {resp.status}")
        html_text = resp.read().decode("utf-8", errors="ignore")
    RAW_FILE.write_text(html_text, encoding="utf-8")
    print(f"Cached raw HTML to {RAW_FILE} ({len(html_text)} bytes)")
    return html_text


def main():
    html_text = fetch_html()
    parser = ContractsParser()
    parser.feed(html_text)
    # Season labels come from the header (y1 is 2026-27, and so
    # on), never hardcoded: if BBRef adds a column the mapping
    # follows the header.
    seasons = {}
    for stat, label in parser.headers.items():
        if re.match(r"^y\d$", stat) and re.match(r"^\d{4}-\d{2}$", label):
            seasons[stat] = label
    if "y1" not in seasons:
        raise SystemExit("could not read season labels from the "
                         "contracts table header, parse failed")
    season_2026_27 = seasons["y1"]
    if season_2026_27 != "2026-27":
        raise SystemExit(f"first salary column is {season_2026_27}, "
                         "expected 2026-27, table layout changed")
    bbref_rows = []
    for r in parser.rows:
        salaries = {}
        for stat, label in seasons.items():
            salaries[label] = _money(r.get(stat))
        bbref_rows.append({
            "name": r.get("player", ""),
            "bbref_id": r.get("_bbref_id"),
            "bbref_team_abbr": r.get("team_id", ""),
            "bbref_team": TEAM_MAP.get(r.get("team_id", ""), r.get("team_id", "")),
            "salaries": salaries,
            "salary_2026_27": salaries.get("2026-27"),
            "guaranteed": _money(r.get("remain_gtd")),
        })
    print(f"Parsed {len(bbref_rows)} BBRef contract rows")
    # Known salary cross-checks: fail loudly on a wrong parse.
    by_folded = {fold(r["name"]): r for r in bbref_rows}
    bad = []
    for name, expected in KNOWN.items():
        row = by_folded.get(fold(name))
        if row is None or row["salary_2026_27"] != expected:
            got = row["salary_2026_27"] if row else None
            bad.append(f"{name}: expected ${expected:,}, got {got}")
    if bad:
        raise SystemExit("known salary cross-check failed: " + "; ".join(bad))
    print("Known salary cross-checks passed: Curry $62,587,158, "
          "Jokic $59,033,114, Tatum / Davis / Giannis $58,456,566, "
          "LeBron $3,876,529, DeRozan / Drummond / Conley $2,449,421")

    # Match to our players, by folded name plus team first.
    with open(DATA / "players_2026_27.csv", newline="") as f:
        ours = list(csv.DictReader(f))
    by_name_team = {}
    by_name = {}
    for r in bbref_rows:
        by_name_team.setdefault((fold(r["name"]), r["bbref_team"]), []).append(r)
        by_name.setdefault(fold(r["name"]), []).append(r)
    matched, unmatched, team_mismatch = [], [], []
    bbref_matched_ids = set()
    for p in ours:
        key = (fold(p["name"]), p["team"])
        cands = by_name_team.get(key) or by_name.get(fold(p["name"]), [])
        row = cands[0] if cands else None
        entry = {
            "athlete_id": p["athlete_id"],
            "name": p["name"],
            "team": p["team"],
            "bbref_team": row["bbref_team"] if row else None,
            "bbref_team_abbr": row["bbref_team_abbr"] if row else None,
            "bbref_id": row["bbref_id"] if row else None,
            "salary_2026_27": row["salary_2026_27"] if row else None,
            "salaries": row["salaries"] if row else {},
            "guaranteed": row["guaranteed"] if row else None,
            "source": "basketball-reference.com/contracts/players.html" if row else None,
        }
        if row:
            bbref_matched_ids.add(id(row))
            matched.append(entry)
            if row["bbref_team"] != p["team"]:
                team_mismatch.append(
                    f"{p['name']}: our team {p['team']}, BBRef {row['bbref_team']}")
        else:
            unmatched.append(entry)
    print(f"Matched {len(matched)} of {len(ours)} players, "
          f"{len(unmatched)} unmatched, "
          f"{len(team_mismatch)} team mismatches")
    for m in team_mismatch:
        print(f"  team mismatch: {m}")
    for p in unmatched:
        print(f"  unmatched: {p['name']} ({p['team']})")
    bbref_only = [r for r in bbref_rows if id(r) not in bbref_matched_ids]
    print(f"{len(bbref_only)} BBRef rows match no player in our data "
          "(mostly players who have not played yet this season)")

    # Team payrolls: two versions, both labelled. The BBRef team
    # total sums every BBRef row for the team, including players
    # who have not played yet this season, so it is the fuller
    # payroll. The matched-only sum covers just the players in
    # our box-score data and understates the real payroll (for
    # example Golden State, where a high earner has not played
    # yet in our game data). team_needs.py uses the BBRef total.
    bbref_payrolls = {}
    for r in bbref_rows:
        if r["salary_2026_27"] is not None:
            bbref_payrolls.setdefault(r["bbref_team"], {"rows": 0, "total": 0})
            bbref_payrolls[r["bbref_team"]]["rows"] += 1
            bbref_payrolls[r["bbref_team"]]["total"] += r["salary_2026_27"]
    payrolls = {}
    for p in ours:
        payrolls.setdefault(p["team"], {"matched": 0, "total": 0})
    for e in matched:
        if e["salary_2026_27"] is not None:
            payrolls[e["team"]]["matched"] += 1
            payrolls[e["team"]]["total"] += e["salary_2026_27"]
    for team in sorted(bbref_payrolls, key=lambda t: bbref_payrolls[t]["total"], reverse=True):
        tot = bbref_payrolls[team]["total"]
        line = "under cap"
        if tot >= SECOND_APRON:
            line = "over second apron"
        elif tot >= FIRST_APRON:
            line = "over first apron"
        elif tot >= TAX:
            line = "over tax"
        elif tot >= CAP:
            line = "over cap"
        print(f"  payroll (BBRef total) {team}: ${tot:,} "
              f"({bbref_payrolls[team]['rows']} rows, {line})")

    payload = {
        "season": SEASON,
        "source": URL,
        "fetch_date": date.today().isoformat(),
        "bbref_rows": len(bbref_rows),
        "player_count": len(ours),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "team_mismatches": team_mismatch,
        "unmatched_players": [u["name"] + " (" + u["team"] + ")"
                              for u in unmatched],
        "bbref_only_rows": len(bbref_only),
        "cap_lines": {
            "salary_cap": CAP, "luxury_tax": TAX,
            "first_apron": FIRST_APRON, "second_apron": SECOND_APRON,
            "salary_floor": SALARY_FLOOR,
        },
        "method": (
            "Salaries from the Basketball-Reference contracts table, "
            "one polite fetch, cached raw under data/raw/salaries/. "
            "Matched by folded name plus team; salaries are never "
            "invented, unmatched players are null. Team payrolls in "
            "team_payrolls are BBRef team totals (every contract row "
            "for the team, including players who have not played yet); "
            "team_payrolls_matched sums only the players in our "
            "box-score data and understates the real payroll. Display "
            "only: salaries do not change any rating or TradeScore in v1."
        ),
        "team_payrolls": {
            team: {"bbref_rows": bbref_payrolls[team]["rows"],
                   "payroll_2026_27": bbref_payrolls[team]["total"]}
            for team in bbref_payrolls
        },
        "team_payrolls_matched": {
            team: {"matched_players": payrolls[team]["matched"],
                   "payroll_2026_27": payrolls[team]["total"]}
            for team in payrolls
        },
        "players": matched + unmatched,
    }
    (DATA / "salaries_2026_27.json").write_text(json.dumps(payload, indent=2))
    fields = ["athlete_id", "name", "team", "bbref_team", "bbref_id",
              "salary_2026_27", "salary_2027_28", "salary_2028_29",
              "salary_2029_30", "salary_2030_31", "salary_2031_32",
              "guaranteed", "source"]
    with open(DATA / "salaries_2026_27.csv", "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for e in matched + unmatched:
            s = e["salaries"] or {}
            w.writerow({
                "athlete_id": e["athlete_id"], "name": e["name"],
                "team": e["team"], "bbref_team": e["bbref_team"] or "",
                "bbref_id": e["bbref_id"] or "",
                "salary_2026_27": e["salary_2026_27"] if e["salary_2026_27"] is not None else "",
                "salary_2027_28": s.get("2027-28") if s.get("2027-28") is not None else "",
                "salary_2028_29": s.get("2028-29") if s.get("2028-29") is not None else "",
                "salary_2029_30": s.get("2029-30") if s.get("2029-30") is not None else "",
                "salary_2030_31": s.get("2030-31") if s.get("2030-31") is not None else "",
                "salary_2031_32": s.get("2031-32") if s.get("2031-32") is not None else "",
                "guaranteed": e["guaranteed"] if e["guaranteed"] is not None else "",
                "source": e["source"] or "",
            })
    print("Wrote data/salaries_2026_27.json + .csv: "
          f"{len(matched)} matched, {len(unmatched)} unmatched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
