"""Security smoke checks for server deployment. No network calls or bets."""
from fastapi.testclient import TestClient
from main import app
import pytest

client=TestClient(app)


def test_public_health_and_version(monkeypatch):
    monkeypatch.delenv("READER_API_TOKEN", raising=False)
    res=client.get("/health")
    assert res.status_code==200
    assert res.json()["version"]=="4.1.0"
    assert res.json()["api_auth_configured"] is False


def test_fail_closed_without_secret(monkeypatch):
    monkeypatch.delenv("READER_API_TOKEN", raising=False)
    for method,route in [("get","/odds"),("get","/docs"),("get","/openapi.json"),("post","/parse-snapshot"),("post","/sync")]:
        r=getattr(client,method)(route)
        assert r.status_code==401, (method,route,r.text)


def test_auth_for_read(monkeypatch):
    monkeypatch.setenv("READER_API_TOKEN","X"*64)
    assert client.get("/odds").status_code==401
    r=client.get("/odds",headers={"X-Reader-API-Key":"X"*64})
    assert r.status_code==200
    assert r.json()["count"]==0


def test_sync_still_disabled(monkeypatch):
    monkeypatch.setenv("READER_API_TOKEN","X"*64)
    monkeypatch.delenv("READER_SYNC_TOKEN",raising=False)
    r=client.post("/sync",headers={"X-Reader-API-Key":"X"*64})
    assert r.status_code==403


def test_payload_too_large(monkeypatch):
    monkeypatch.setenv("READER_API_TOKEN","X"*64)
    r=client.post("/parse-html",headers={"X-Reader-API-Key":"X"*64},json={"html":"A"*2_000_010,"sport":"nba","event_url":"https://superbet.pl/mecz/test"})
    assert r.status_code==413


def test_snapshot_authorized(monkeypatch):
    monkeypatch.setenv("READER_API_TOKEN","X"*64)
    r=client.post("/parse-snapshot",headers={"X-Reader-API-Key":"X"*64},json={"sport":"nba","payload":{"events":[]}})
    assert r.status_code==200
    assert r.json()["live_market_read_confirmed"] is False
