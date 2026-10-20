"""Read-only Superbet proposal bridge with FootballData diagnostics."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from footballdata import FootballDataError, health as football_health, match_odds, today_fixtures

app = FastAPI(
    title="Superbet Read-only Offer Bridge",
    description="Importowane oferty i diagnostyka FootballData, bez obstawiania.",
)
SECRET_API_KEY = os.environ.get("SECRET_API_KEY", "")
GLOBAL_EVENTS_CACHE: List[Dict[str, Any]] = []
CACHE_METADATA: Dict[str, Any] = {
    "last_update": None, "last_attempt": None, "status": "BRAK_AUTORYZOWANEGO_ZRODLA",
    "total_events": 0, "errors": [], "successful_requests": 0,
    "source_kind": None, "source_verified": False,
    "automatic_polling_active": False,
    "schedule": "09:00 / 17:00 Europe/Warsaw; brak pollingu w tym bridge",
}


def weryfikuj_klucz(x_api_key: Optional[str] = Header(None)) -> None:
    if not SECRET_API_KEY or not x_api_key or not secrets.compare_digest(x_api_key, SECRET_API_KEY):
        raise HTTPException(status_code=401, detail="Nieprawidłowy lub brakujący X-API-Key")


class Snapshot(BaseModel):
    source_kind: Literal["manual_superbet_export", "footballdata.io"]
    observed_at: datetime
    mecze: List[Dict[str, Any]] = Field(default_factory=list, max_length=100)


def normalize_record(record: Dict[str, Any], source_kind: str) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("Mecz musi być obiektem")
    event_id, match_name, league = record.get("mecz_id"), record.get("mecz"), record.get("liga")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 150:
        raise ValueError("Każdy mecz wymaga prawdziwego meczu_id")
    if not isinstance(match_name, str) or not match_name.strip() or len(match_name) > 250:
        raise ValueError("Każdy mecz wymaga nazwy")
    if not isinstance(league, str) or not league.strip():
        raise ValueError("Każdy mecz wymaga ligi")
    markets, main_odds = record.get("pelna_oferta_rynkowa", []), record.get("kursy_glowne_1X2", {})
    if not isinstance(markets, list) or not isinstance(main_odds, dict):
        raise ValueError("Nieprawidłowe pola rynków/kursów")
    if source_kind == "footballdata.io" and (markets or main_odds):
        raise ValueError("Nie wolno oznaczać kursów FootballData jako potwierdzonych kursów Superbet")
    return {
        "mecz_id": event_id, "mecz": match_name, "liga": league,
        "dyscyplina": str(record.get("dyscyplina", "Pilka Nozna")),
        "data_meczu": str(record.get("data_meczu", "Brak daty")),
        "tryb": str(record.get("tryb", "PREMATCH")),
        "kursy_glowne_1X2": dict(main_odds), "pelna_oferta_rynkowa": list(markets),
        "source_kind": source_kind, "superbet_prices_verified": False,
    }


@app.get("/")
@app.get("/healthz")
def healthz():
    return {
        "service": "ok", "automatic_polling_active": False,
        "import_auth_configured": bool(SECRET_API_KEY),
        "snapshot_loaded": bool(GLOBAL_EVENTS_CACHE),
        "storage_mode": "ephemeral_memory", "status": CACHE_METADATA["status"],
    }


@app.get("/api/football-data/test")
def football_data_test(_: None = Depends(weryfikuj_klucz)):
    try:
        result = football_health()
        return {"status": "ok", "source_kind": "footballdata.io",
                "provider_observed": True, "superbet_prices_verified": False,
                "result": result.get("payload")}
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/football-data/fixtures/today")
def football_data_fixtures(_: None = Depends(weryfikuj_klucz)):
    try:
        payload = today_fixtures()
        return {"status": "ok", "source_kind": "footballdata.io",
                "superbet_prices_verified": False, "data": payload}
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/football-data/matches/{match_id}/odds")
def football_data_odds(match_id: str, _: None = Depends(weryfikuj_klucz)):
    try:
        payload = match_odds(match_id)
        return {"status": "ok", "source_kind": "footballdata.io",
                "bookmaker": "superbet.pl", "superbet_prices_verified": False,
                "data": payload}
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/import-snapshot")
def import_snapshot(payload: Snapshot, _: None = Depends(weryfikuj_klucz)):
    if payload.observed_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="observed_at wymaga strefy czasowej")
    try:
        records = [normalize_record(x, payload.source_kind) for x in payload.mecze]
        ids = [r["mecz_id"] for r in records]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplikat mecz_id")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    GLOBAL_EVENTS_CACHE[:] = records
    CACHE_METADATA.update({
        "last_update": datetime.now(timezone.utc).isoformat(),
        "last_attempt": datetime.now(timezone.utc).isoformat(),
        "status": "IMPORT_OPERATORA_NIEZWERYFIKOWANY", "total_events": len(records),
        "source_kind": payload.source_kind, "source_verified": False,
        "provider_observed_at": payload.observed_at.isoformat(),
        "automatic_polling_active": False, "errors": [], "successful_requests": 0,
    })
    return {"status": CACHE_METADATA["status"], "count": len(records)}


@app.get("/api/kursy")
def pobierz_liste_meczow(
    szukaj: Optional[str] = Query(None),
    dyscyplina: Optional[str] = Query(None),
    _: None = Depends(weryfikuj_klucz),
):
    results = GLOBAL_EVENTS_CACHE
    if dyscyplina:
        results = [e for e in results if e["dyscyplina"].lower() == dyscyplina.lower()]
    if szukaj:
        query = szukaj.lower()
        results = [e for e in results if query in e["mecz"].lower() or query in e["liga"].lower()]
    summary = []
    for event in results[:40]:
        item = dict(event)
        item.pop("pelna_oferta_rynkowa", None)
        summary.append(item)
    return {"metadane": dict(CACHE_METADATA), "liczba_meczow": len(summary), "mecze": summary}


@app.get("/api/mecz-detale")
def pobierz_wszystkie_rynki_meczu(mecz_id: str, _: None = Depends(weryfikuj_klucz)):
    for event in GLOBAL_EVENTS_CACHE:
        if event["mecz_id"] == mecz_id:
            return {
                "mecz": event["mecz"], "liga": event["liga"], "data_meczu": event["data_meczu"],
                "wszystkie_rynki_oferty": event["pelna_oferta_rynkowa"],
                "source_kind": event["source_kind"], "superbet_prices_verified": False,
            }
    raise HTTPException(status_code=404, detail="Nie znaleziono meczu")
