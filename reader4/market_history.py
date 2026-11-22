"""Weighted, auditable 3-season market averages. No automatic data fabrication.

Supports any market category (corners, cards, shots, player props, goals, etc.)
when the user supplies counts and match exposure for a team, league and season.
Input: list of rows with fields season, sport, league, team, market,
venue (home|away|all), matches, made, conceded. `made` and `conceded`
are historical observation totals, not rates; they can be fractional if
aggregating modeled metrics such as xG.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

EUROPE_SEASONS = ("2023/24", "2024/25", "2025/26")
MLS_SEASONS = ("2023", "2024", "2025")
VENUES = {"home", "away", "all"}


def summarize_three_seasons(rows: list[dict], *, seasons: tuple[str, str, str] = EUROPE_SEASONS) -> list[dict]:
    if len(set(seasons)) != 3:
        raise ValueError("Wymagane dokładnie trzy różne sezony")
    combined = defaultdict(lambda: {"matches": 0, "made": 0.0, "conceded": 0.0, "seasons": set()})
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Rekord {i} nie jest obiektem")
        required = ("season", "sport", "league", "team", "market", "venue", "matches", "made", "conceded")
        if any(k not in row for k in required):
            raise ValueError(f"Rekord {i}: brak pól {sorted(set(required)-set(row))}")
        if row["season"] not in seasons:
            continue
        if row["venue"] not in VENUES:
            raise ValueError(f"Rekord {i}: venue musi być home, away lub all")
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ("sport", "league", "team", "market")):
            raise ValueError(f"Rekord {i}: puste nazwy")
        games = row["matches"]
        if isinstance(games, bool) or not isinstance(games, int) or games <= 0:
            raise ValueError(f"Rekord {i}: matches musi być dodatnią liczbą całkowitą")
        for k in ("made", "conceded"):
            n = row[k]
            if isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) or n < 0:
                raise ValueError(f"Rekord {i}: {k} musi być nieujemną skończoną liczbą")
        key = tuple(row[k].strip() for k in ("sport", "league", "team", "market")) + (row["venue"],)
        a = combined[key]
        a["matches"] += games
        a["made"] += row["made"]
        a["conceded"] += row["conceded"]
        a["seasons"].add(row["season"])
    out = []
    for (sport, league, team, market, venue), v in combined.items():
        out.append({"sport": sport, "league": league, "team": team, "market": market,
                    "venue": venue, "matches": v["matches"],
                    "average_for": round(v["made"] / v["matches"], 4),
                    "average_against": round(v["conceded"] / v["matches"], 4),
                    "seasons_present": sorted(v["seasons"]),
                    "complete_three_seasons": set(v["seasons"]) == set(seasons)})
    return sorted(out, key=lambda x: (-x["average_for"], x["league"], x["team"], x["market"]))


def main():
    parser = argparse.ArgumentParser(description="FUZJA: średnie z trzech sezonów dla wszystkich kategorii")
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--mls", action="store_true", help="Dla MLS analizuj trzy pełne lata 2023–2025")
    parser.add_argument("--out", type=Path, default=Path("market_history_summary.json"))
    args = parser.parse_args()
    records = json.loads(args.input_json.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        parser.error("Wejście musi być listą JSON")
    result = summarize_three_seasons(records, seasons=MLS_SEASONS if args.mls else EUROPE_SEASONS)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Zapisano {len(result)} wyników -> {args.out}")


if __name__ == "__main__":
    main()
