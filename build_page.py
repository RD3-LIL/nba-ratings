"""Build index.html for GitHub Pages from the Nov 20, 2026 ratings files.
Reads data/players_2026_27.csv, data/gm_board_2026_27.csv and
data/teams_2026_27.json. Stdlib only.
"""
import csv
import html
import json
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"

players = list(csv.DictReader(open(DATA / "players_2026_27.csv")))
gm = list(csv.DictReader(open(DATA / "gm_board_2026_27.csv")))
teams_doc = json.loads((DATA / "teams_2026_27.json").read_text())
teams = teams_doc["teams"]

def fnum(x, nd=1):
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "-"

# Top 20 RPR, 40% minutes floor, same as the delivered leaderboard.
ranked = [p for p in players if p.get("rpr") and float(p.get("min_pct") or 0) >= 40]
ranked.sort(key=lambda p: float(p["rpr"]), reverse=True)
top20 = ranked[:20]

team_ranked = sorted(teams.items(), key=lambda kv: kv[1]["net"], reverse=True)

tiers = {}
for row in gm:
    tiers.setdefault(row["tier"], []).append(row)

def esc(s):
    return html.escape(str(s))

rows = "\n".join(
    f"<tr><td>{i}</td><td>{esc(p['name'])}</td><td>{esc(p['team'])}</td>"
    f"<td>{p['gp']}</td><td>{fnum(p['pts_pg'])}</td><td>{fnum(p['reb_pg'])}</td>"
    f"<td>{fnum(p['ast_pg'])}</td><td>{fnum(p['ortg'])}</td>"
    f"<td>{fnum(p['poss_pct'])}</td><td><b>{fnum(p['rpr'], 2)}</b></td></tr>"
    for i, p in enumerate(top20, 1)
)

team_rows = "\n".join(
    f"<tr><td>{i}</td><td>{esc(name)}</td><td>{t['games']}</td>"
    f"<td>{fnum(t['adj_o'])}</td><td>{fnum(t['adj_d'])}</td>"
    f"<td><b>{fnum(t['net'])}</b></td><td>{fnum(t['adj_t'])}</td></tr>"
    for i, (name, t) in enumerate(team_ranked[:10], 1)
)

tier_order = ["Franchise cornerstone", "All-Star level", "High-value starter",
              "Undervalued / buy-low", "Elite role player", "Overvalued / avoid"]
tier_html = ""
for tier in tier_order:
    members = sorted(tiers.get(tier, []), key=lambda r: float(r["rpr"] or 0), reverse=True)[:10]
    if not members:
        continue
    items = ", ".join(f"{esc(m['name'])} ({esc(m['team'])}, RPR {fnum(m['rpr'], 2)})" for m in members)
    tier_html += f"<h3>{esc(tier)} ({len(tiers.get(tier, []))})</h3><p>{items}</p>\n"

page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NBA Ratings - 2026-27</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 960px; margin: 0 auto; padding: 24px 16px; line-height: 1.5; color: #111; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; font-size: 14px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
th {{ background: #f2f2f2; }}
h1 {{ margin-bottom: 4px; }} .sub {{ color: #555; margin-top: 0; }}
</style>
</head>
<body>
<h1>NBA Ratings</h1>
<p class="sub">2026-27 season through Nov 19, 2026. 226 games, 30 teams, 475 players. Free ESPN box scores, no key. Same ORtg, %Poss, and RPR formulas as RonPom, adapted to 48-minute games.</p>
<p>Data: <a href="data/players_2026_27.csv">player ratings CSV</a> | <a href="data/players_2026_27_eval.csv">deep eval CSV</a> | <a href="data/gm_board_2026_27.csv">GM board CSV</a> | <a href="README.md">README and method</a></p>
<h2>Top 20 players by RPR (40% minutes floor)</h2>
<p>RPR = (ORtg - 100) x possessions used per game / 100 + 0.5 x (AST - TO) per game + (STL + BLK) per game + 0.25 x REB per game. ORtg here is the simplified version, points per 100 possessions used, no assist credit, so it underrates playmakers. Read it with %Poss.</p>
<table><tr><th>Rk</th><th>Player</th><th>Team</th><th>GP</th><th>PTS</th><th>REB</th><th>AST</th><th>ORtg</th><th>%Poss</th><th>RPR</th></tr>
{rows}
</table>
<p>Note: Stephen Curry would rank #2 by RPR (9.78, ORtg 132.1) but is at 34.5% of minutes, under the 40% floor.</p>
<h2>Top 10 teams by Net</h2>
<table><tr><th>Rk</th><th>Team</th><th>GP</th><th>AdjO</th><th>AdjD</th><th>Net</th><th>AdjT</th></tr>
{team_rows}
</table>
<p>League average OffEff {fnum(teams_doc['league_o'])}, tempo {fnum(teams_doc['league_t'])}. League-average prior only, no last-season prior yet, so early-season team order is noisy.</p>
<h2>GM acquisition board</h2>
<p>Tiers are set by thresholds on RPR, efficiency at usage, defense, and availability, not by reputation. No contracts, salaries, age, or height/weight in v1.</p>
{tier_html}
<p><small>Built Nov 20, 2026. Box-score only: no tracking, no on/off, no opponent adjustment at player level. Full method in the README.</small></p>
</body>
</html>
"""
(HERE / "index.html").write_text(page)
print(f"Wrote index.html: top20={len(top20)}, teams={len(teams)}, tiers={ {k: len(v) for k, v in tiers.items()} }")
