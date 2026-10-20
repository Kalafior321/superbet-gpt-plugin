"""Read-only FootballData.io client. Never places bets or claims Superbet verification."""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import requests

BASE_URL = "https://footballdata.io/api/v1"


class FootballDataError(RuntimeError):
    pass


def _request(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    key = os.environ.get("FOOTBALLDATA_API_KEY", "").strip()
    if not key:
        raise FootballDataError("FOOTBALLDATA_API_KEY is not configured")
    response = requests.get(
        BASE_URL + path,
        params=params or {},
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=20,
    )
    if response.status_code >= 400:
        raise FootballDataError(f"FootballData HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise FootballDataError("FootballData returned an invalid JSON object")
    return payload


def today_fixtures() -> dict[str, Any]:
    return _request("/fixtures/today")


def match_odds(match_id: str) -> dict[str, Any]:
    if not match_id or len(match_id) > 100:
        raise FootballDataError("Invalid match_id")
    return _request(f"/matches/{match_id}/odds")


def health() -> dict[str, Any]:
    payload = _request("/meta/status")
    return {
        "source_kind": "footballdata.io",
        "provider_observed": True,
        "superbet_prices_verified": False,
        "payload": payload,
    }
