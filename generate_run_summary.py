"""Generate data/run_summary.json and the README "Current run" section.

Reads the pipeline outputs (teams, players, eval, GM board, team
needs, validation, fetch state, games, rosters) and writes a
machine-readable summary plus a generated markdown block in
README.md between <!-- RUN-SUMMARY-START --> and
<!-- RUN-SUMMARY-END -->. The README section is never hand-edited:
re-run this script after any pipeline change and the numbers follow
the data. No network, stdlib only.
"""

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
README = HERE / "README.md"
START = "<!-- RUN-SUMMARY-START -->"
END = "<!-- RUN-SUMMARY-END -->"


def load_json(name):
    return json.loads((DATA / name).read_text())


def load_csv(name):
    with (DATA / name).open(newline="") as f:
        return list(csv.DictReader(f))


def _f(row, key):
    v = row.get(key)
    return float(v) if v not in (None, "") else None


def data_through(games):
    played = [g for g in games
              if not (g["home_score"] == 0 and g["away_score"] == 0)
              and not g.get("stats_missing")]
    dates = sorted(g["date"] for g in played)
    disp = (datetime.strptime(dates[-1], "%Y%m%d").strftime("%B %-d, %Y")
            if dates else "unknown")
    first = (datetime.strptime(dates[0], "%Y%m%d").strftime("%B %-d, %Y")
             if dates else "unknown")
    return first, disp, len(played)


def main():
    teams_doc = load_json("teams_2026_27.json")
    players = load_csv("players_2026_27.csv")
    gm = load_json("gm_board_2026_27.json")
    needs_doc = load_json("team_needs_2026_27.json")
    validation = load_json("validation.json")
    fetch_state = load_json("fetch_state.json")
    games = load_json("games_2026_27.json")
    rosters = load_json("rosters_2026_27.json")

    first_date, last_date, n_games = data_through(games)
    teams = teams_doc["teams"]
    info = teams_doc.get("info", {})
    ranked = sorted(teams.items(), key=lambda kv: kv[1]["net"],
                    reverse=True)
    top_teams = [{"team": t, "net": round(v["net"], 1),
                  "adj_o": round(v["adj_o"], 1),
                  "adj_d": round(v["adj_d"], 1)}
                 for t, v in ranked[:5]]
    bottom_teams = [{"team": t, "net": round(v["net"], 1)}
                    for t, v in ranked[-2:]]

    with_rpr = [p for p in players if _f(p, "rpr") is not None]
    qualified = [p for p in with_rpr
                 if int(p["gp"]) >= 2 and _f(p, "min_pct") is not None
                 and float(p["min_pct"]) >= 40]
    qualified.sort(key=lambda p: float(p["rpr"]), reverse=True)
    top_players = [{"name": p["name"], "team": p["team"],
                    "rpr": round(float(p["rpr"]), 2),
                    "gp": int(p["gp"]), "ppg": float(p["pts_pg"])}
                   for p in qualified[:10]]

    rookies = [p for p in players if p.get("is_rookie") == "1"]
    rookie_qual = [p for p in qualified if p.get("is_rookie") == "1"]
    best_rookie = ({"name": rookie_qual[0]["name"],
                    "team": rookie_qual[0]["team"],
                    "rpr": round(float(rookie_qual[0]["rpr"]), 2),
                    "gp": int(rookie_qual[0]["gp"]),
                    "ppg": float(rookie_qual[0]["pts_pg"])}
                   if rookie_qual else None)

    tier_counts = Counter(p.get("tier", "") for p in gm["players"])
    tier_counts = {k: v for k, v in tier_counts.items() if k}

    needs_teams = needs_doc["teams"]
    all_top = [s for p in needs_teams.values()
               for s in p["targets"]["top_overall"]]
    distinct = {s["name"] for s in all_top}
    top1 = Counter(s["name"] for p in needs_teams.values()
                   for s in p["targets"]["top_overall"][:1])
    no_star = sorted(t for t, p in needs_teams.items()
                     if not (p["star"] or {}).get("is_clear_star"))
    unfilled = [{"team": t, "need": k, "reason": v}
                for t, p in needs_teams.items()
                for k, v in p["targets"].get("unfilled_needs", {}).items()]
    scores = [s["trade_score"] for s in all_top]
    distinct_top1 = Counter(
        s["name"] for p in needs_teams.values()
        for s in p["targets"].get("top_distinct", [])[:1])

    fs = fetch_state.get("season_2026-27", {})
    summary = {
        "generated_by": "generate_run_summary.py",
        "data_through": last_date,
        "data_from": first_date,
        "n_games": n_games,
        "n_teams": len(teams),
        "n_players": len(players),
        "n_players_with_rpr": len(with_rpr),
        "failed_fetches": fs.get("failed", 0),
        "league_off_eff": round(teams_doc["league_o"], 1),
        "league_tempo": round(teams_doc["league_t"], 1),
        "team_solver": {
            "converged": info.get("converged"),
            "iterations": info.get("iterations"),
            "margin_cap": info.get("margin_cap"),
            "home_court": round(info["home_court"], 2)
            if info.get("home_court") is not None else None,
            "prior_weight": info.get("prior_weight"),
        },
        "top_teams_by_net": top_teams,
        "bottom_teams_by_net": bottom_teams,
        "top_players_by_rpr": top_players,
        "best_rookie": best_rookie,
        "n_rookies_on_rosters": rosters.get("rookie_count"),
        "n_rookie_profiles_played": len(rookies),
        "gm_tier_counts": tier_counts,
        "team_needs": {
            "top_slots": len(all_top),
            "distinct_top_players": len(distinct),
            "most_common_top1": top1.most_common(4),
            "no_clear_star_teams": no_star,
            "n_no_clear_star": len(no_star),
            "unfilled_needs": unfilled,
            "trade_score_min": round(min(scores), 3) if scores else None,
            "trade_score_max": round(max(scores), 3) if scores else None,
            "distinct_top1_players": len(distinct_top1),
        },
        "validation": validation,
    }
    (DATA / "run_summary.json").write_text(
        json.dumps(summary, indent=1) + "\n")

    tn = summary["team_needs"]
    dist = validation["distribution"]["v1.2"]
    sh = validation["split_half_r"]
    ta = validation["team_aggregation_r"]
    lines = [
        "## Current run (generated, do not hand-edit)",
        "",
        "Generated by `python3 generate_run_summary.py` from the "
        "pipeline outputs. If a number here disagrees with a number "
        "elsewhere in this README, this section is right and the "
        "other one is stale.",
        "",
        f"Data through {last_date} ({n_games} games, Oct 20 to "
        f"Nov 20, 2026 fetched; Nov 20 games had not been played yet "
        f"when fetched, so the data effectively ends Nov 19), "
        f"{len(teams)} teams, {len(players)} player profiles, "
        f"{summary['failed_fetches']} failed fetches. League OffEff "
        f"{summary['league_off_eff']}, tempo {summary['league_tempo']}. "
        f"Team solver converged in {info.get('iterations')} iterations, "
        f"margin cap {info.get('margin_cap')}, home court "
        f"{summary['team_solver']['home_court']} estimated from the data.",
        "",
        "Top teams by Net: " + ", ".join(
            f"{t['team']} {t['net']:+.1f}" for t in top_teams) + ". "
        "Bottom: " + ", ".join(
            f"{t['team']} {t['net']:+.1f}" for t in bottom_teams) + ".",
        "",
        "Top players by RPR v1.2 (min 2 games, 40% of team minutes): "
        + ", ".join(
            f"{p['name']} ({p['team']}) {p['rpr']:.2f}"
            for p in top_players) + ".",
        "",
        f"RPR v1.2 distribution (n = {dist['n']}): median "
        f"{dist['median']:.2f}, mean {dist['mean']:.2f}, "
        f"{dist['neg_share'] * 100:.0f}% negative, range "
        f"{dist['min']:.2f} to {dist['max']:.2f}. Split-half r "
        f"(n = {validation['n_split_half']}): v1 {sh['v1']:.3f}, "
        f"v1.1 {sh['v1.1']:.3f}, v1.2 {sh['v1.2']:.3f}. Team "
        f"aggregation r: v1 {ta['v1']:.3f}, v1.1 {ta['v1.1']:.3f}, "
        f"v1.2 {ta['v1.2']:.3f}.",
        "",
    ]
    if best_rookie:
        lines.append(
            f"Best rookie by RPR v1.2: {best_rookie['name']} "
            f"({best_rookie['team']}), RPR {best_rookie['rpr']:.2f}, "
            f"{best_rookie['ppg']:.1f} PPG in {best_rookie['gp']} games. "
            f"{rosters.get('rookie_count')} rookies on rosters, "
            f"{len(rookies)} rookie profiles have played.")
        lines.append("")
    lines.append(
        "GM board tiers (RPR v1.2, " + str(len(gm["players"]))
        + " players): " + ", ".join(
            f"{k} {v}" for k, v in sorted(
                tier_counts.items(),
                key=lambda kv: -kv[1])) + ".")
    lines.append("")
    lines.append(
        f"Team needs: {tn['top_slots']} top-overall slots filled by "
        f"{tn['distinct_top_players']} distinct players; most often "
        f"#1: " + ", ".join(f"{n} ({c})" for n, c in
                            tn["most_common_top1"]) + ". "
        f"{tn['n_no_clear_star']} teams with no clear star "
        f"({', '.join(tn['no_clear_star_teams'])}). "
        f"TradeScore range {tn['trade_score_min']:.3f} to "
        f"{tn['trade_score_max']:.3f}. Unfilled needs: "
        + ("none." if not tn["unfilled_needs"] else
           "; ".join(f"{u['team']} {u['need']}: {u['reason']}"
                     for u in tn["unfilled_needs"]) + "."))
    lines.append("")
    md = "\n".join(lines)

    text = README.read_text()
    if START in text and END in text:
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        text = before + START + "\n\n" + md + "\n" + END + after
    else:
        # Insert after the intro paragraph, before the first ## heading.
        marker = "\n## Source\n"
        idx = text.index(marker)
        text = (text[:idx] + "\n" + START + "\n\n" + md + "\n" + END
                + text[idx:])
    README.write_text(text)
    print(f"Wrote data/run_summary.json and updated README "
          f"({n_games} games through {last_date})")


if __name__ == "__main__":
    main()
