"""Opt-in read-only FUZJA adapters. These are not Superbet price feeds."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

REEP_MANIFEST_URL = "https://data.reep.football/releases/latest.json"
STRIKER_URL = "https://mcp.strikerapi.com/v1"
ASA_LEAGUES = {"mls", "nwsl", "uslc", "usl1", "usls", "nasl", "mlsnp"}
STRIKER_LEAGUES = {
    "soccer", "mls", "laliga", "seriea", "bundesliga", "ligue1", "ucl",
    "championship", "nwsl", "ligamx", "uel", "uecl", "eredivisie",
    "primeira", "scotland", "brazil", "argentina"
}
STRIKER_COST = {"get_standings": 2, "get_schedule": 2, "get_team_stats": 5, "get_odds": 5}


class SourceError(RuntimeError):
    """A source is unavailable or not authorized; no secrets in messages."""


def _enabled(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "yes", "true"}


def status() -> list[dict[str, Any]]:
    return [
        {"source": "reep_manifest", "configured": True, "connection_verified": False,
         "purpose": "identity_crosswalk_release_only", "charges_credits": False},
        {"source": "american_soccer_analysis",
         "configured": _enabled("FUZJA_ASA_DATA_RIGHTS_CONFIRMED"),
         "connection_verified": False, "purpose": "mls_nwsl_games_xg",
         "charges_credits": False},
        {"source": "strikerapi",
         "configured": bool(os.getenv("STRIKERAPI_KEY")) and _enabled("FUZJA_STRIKER_ALLOW_FREE_CREDITS"),
         "connection_verified": False, "purpose": "xg_stats_or_third_party_odds",
         "charges_credits": True},
    ]


def fetch(source: str, *, league: str = "mls", season: int = 2026,
          dataset: str = "games", team: str | None = None,
          limit: int = 50, session: Any = requests, asa_client: Any = None) -> dict[str, Any]:
    if not 2000 <= season <= 2100 or not 1 <= limit <= 100:
        raise SourceError("Nieprawidlowy sezon lub limit.")
    if source == "reep_manifest":
        try:
            response = session.get(REEP_MANIFEST_URL, timeout=15,
                                   headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"Reep: {type(exc).__name__}") from None
        if not isinstance(data, dict):
            raise SourceError("Reep: niepoprawny manifest.")
        return {"source": source, "kind": "identity_release", "data": data,
                "superbet_prices_verified": False, "contains_match_statistics": False}

    if source == "american_soccer_analysis":
        if not _enabled("FUZJA_ASA_DATA_RIGHTS_CONFIRMED"):
            raise SourceError("ASA: potwierdz prawa do danych w FUZJA_ASA_DATA_RIGHTS_CONFIRMED.")
        if league not in ASA_LEAGUES or dataset not in {"games", "team_xgoals"}:
            raise SourceError("ASA: nieobslugiwana liga lub zbior.")
        if asa_client is None:
            try:
                from itscalledsoccer import AmericanSoccerAnalysis
                asa_client = AmericanSoccerAnalysis()
            except ImportError:
                raise SourceError("ASA: zainstaluj opcjonalnie itscalledsoccer.") from None
        try:
            if dataset == "games":
                frame = asa_client.get_games(leagues=league, seasons=str(season))
            else:
                frame = asa_client.get_team_xgoals(leagues=league, split_by_seasons=True)
            records = frame.to_dict(orient="records")
        except Exception as exc:
            raise SourceError(f"ASA: {type(exc).__name__}") from None
        if not isinstance(records, list):
            raise SourceError("ASA: niepoprawny schemat.")
        if dataset == "team_xgoals":
            records = [r for r in records if isinstance(r, dict) and
                       str(r.get("season_name", r.get("season", ""))).startswith(str(season))]
        return {"source": source, "kind": dataset, "league": league,
                "records": records[:limit], "data_as_of": None,
                "superbet_prices_verified": False}

    if source == "strikerapi":
        if not _enabled("FUZJA_STRIKER_ALLOW_FREE_CREDITS"):
            raise SourceError("StrikerAPI: brak swiadomej zgody na zuzycie kredytow.")
        key = os.getenv("STRIKERAPI_KEY", "")
        if not key:
            raise SourceError("StrikerAPI: brak STRIKERAPI_KEY.")
        if league not in STRIKER_LEAGUES or dataset not in STRIKER_COST:
            raise SourceError("StrikerAPI: nieobslugiwana liga lub narzedzie.")
        if team and (len(team) > 80 or not team.strip()):
            raise SourceError("StrikerAPI: niepoprawna druzyna.")
        try:
            limit_cost = int(os.getenv("FUZJA_STRIKER_MAX_CREDITS_PER_CALL", "5"))
        except ValueError:
            raise SourceError("StrikerAPI: niepoprawny limit kredytow.") from None
        if limit_cost not in range(0, 6) or STRIKER_COST[dataset] > limit_cost:
            raise SourceError("StrikerAPI: koszt przekracza limit.")
        body: dict[str, Any] = {"season": season, "league": league}
        if team:
            body["team"] = team
        try:
            response = session.post(f"{STRIKER_URL}/{dataset}", json=body,
                                    headers={"x-api-key": key, "Content-Type": "application/json"},
                                    timeout=20)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"StrikerAPI: {type(exc).__name__}") from None
        if not isinstance(data, dict) or "data" not in data or data.get("error"):
            raise SourceError("StrikerAPI: brak poprawnych danych.")
        return {"source": source, "kind": dataset, "league": league,
                "credit_cost": STRIKER_COST[dataset],
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "data": data["data"], "data_as_of": None,
                "bookmaker": "third_party_not_superbet" if dataset == "get_odds" else None,
                "superbet_prices_verified": False}
    raise SourceError("Nieznane zrodlo.")
