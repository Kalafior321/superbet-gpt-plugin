"""Superbet FUZJA Czytnik 4.0: on-demand, read-only browser and fixture parser.

No automatic scraping schedules, no fake sports routes, no EV, no login and
no betting. A successful HTTP request is not proof of complete market coverage.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import random
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright

from reader_browser import (
    extract_event_cards, extract_extra_stats, extract_markets,
    validated_url,
)
from superbet_value_lab import ALLOWED_SPORTS, SPORTS_SCOPE, parse_feed, season_context

LOG = logging.getLogger("fuzja_czytnik")
WARSAW = ZoneInfo("Europe/Warsaw")
VERSION = "4.1.0"
MAX_TARGETS = 25
MAX_EVENTS_PER_TARGET = 100


class SnapshotPayload(BaseModel):
    sport: str = "all"
    payload: dict


class HTMLFixturePayload(BaseModel):
    html: str
    event_url: str
    sport: str = "all"


def date_now() -> str:
    return datetime.now(WARSAW).isoformat()


def validate_targets(targets: Any) -> list[dict]:
    """Only explicitly verified public sport/league pages, not guessed routes."""
    if not isinstance(targets, list) or not targets or len(targets) > MAX_TARGETS:
        raise ValueError("Targets: provide 1–25 configured Superbet page URLs")
    result = []
    seen: set[str] = set()
    for index, item in enumerate(targets):
        if not isinstance(item, dict):
            raise ValueError(f"Target {index}: expected object")
        sport = item.get("sport", "all")
        url = validated_url(item.get("url", ""))
        if not url:
            raise ValueError(f"Target {index}: invalid or non-Superbet HTTPS URL")
        if not isinstance(sport, str) or sport.lower() not in ALLOWED_SPORTS:
            raise ValueError(f"Target {index}: unsupported sport")
        if url in seen:
            raise ValueError(f"Target {index}: repeated URL (would mislabel different sports)")
        seen.add(url)
        result.append({"sport": sport.lower(), "url": url, "competition": item.get("competition")})
    return result


def targets_from_env() -> list[dict]:
    raw = os.environ.get("SUPERBET_TARGETS_JSON", "")
    if not raw:
        return []  # Never label the one generic homepage as football, basketball and hockey.
    try:
        return validate_targets(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"SUPERBET_TARGETS_JSON invalid: {exc}") from exc


def connection_diagnosis(url: str, resolver=None) -> dict:
    url = validated_url(url)
    if not url:
        return {"status": "INVALID_TARGET", "dns_ok": False, "live_verified": False}
    hostname = urlsplit(url).hostname
    resolver = resolver or socket.getaddrinfo
    try:
        results = resolver(hostname, 443, type=socket.SOCK_STREAM)
        if not results:
            raise OSError("no DNS addresses")
        return {"status": "DNS_OK", "dns_ok": True, "live_verified": False, "host": hostname,
                "note": "DNS resolution is not an HTTP/feed success check"}
    except (OSError, socket.gaierror) as exc:
        return {"status": "DNS_UNAVAILABLE", "dns_ok": False, "live_verified": False,
                "host": hostname, "error_class": type(exc).__name__,
                "note": "Local DNS failure does not prove Superbet is down"}


def classify_read_error(exc: Exception) -> str:
    description = str(exc).lower()
    if any(k in description for k in ("err_name_not_resolved", "getaddrinfo", "dns", "temporary failure in name resolution")):
        return "DNS_UNAVAILABLE"
    if any(k in description for k in ("err_cert", "certificate", "ssl", "tls")):
        return "TLS_FAILED"
    if "429" in description:
        return "HTTP_429"
    if "403" in description:
        return "HTTP_403"
    if "401" in description:
        return "HTTP_401"
    if "timeout" in description:
        return "TIMEOUT"
    return "BROWSER_READ_FAILED"


async def read_market_page(context, event: dict, *, include_stats: bool = True) -> dict:
    """One event, from its validated page. Collect ALL DOM markets before tabs."""
    page = await context.new_page()
    try:
        response = await page.goto(event["event_url"], wait_until="domcontentloaded", timeout=25000)
        http_status = response.status if response is not None else None
        if http_status is None or http_status >= 400:
            raise RuntimeError(f"HTTP_{http_status or 'UNKNOWN'} detail page")
        html = await page.content()
        read_at = date_now()
        m = extract_markets(html, event_url=event["event_url"], sport=event["sport"], observed_at=read_at)
        event.update(m)
        event["read_at"] = read_at  # page capture time; not provider quote timestamp
        event["source_observed_at"] = None  # No provider timestamp confirmed in HTML.
        event["read_mode"] = "LIVE_DOM_UNVERIFIED_SELECTORS"
        event["http_status"] = http_status
        event["full_stats"] = extract_extra_stats(html) if include_stats else {}
        # Optional stats tab: never let absent H2H block course/market readings.
        if include_stats and not any(event["full_stats"].get(k) for k in ("h2h_history", "team_form", "standings")):
            try:
                tab = page.get_by_text("Statystyki", exact=True).first
                if await tab.count():
                    await tab.click(timeout=3500)
                    await asyncio.sleep(0.2)
                    stats_html = await page.content()
                    event["full_stats"] = extract_extra_stats(stats_html)
            except Exception:
                event["stats_note"] = "Stats tab inaccessible; markets read separately"
        return event
    finally:
        await page.close()


async def fetch_browser_markets(targets: list[dict], *, include_stats: bool = True,
                                max_events_per_target: int = MAX_EVENTS_PER_TARGET) -> dict:
    """Manual, bounded scrape for explicit target URLs only.

    Does not attempt login/CAPTCHA/anti-bot workarounds. Page selectors are
    provisional. Client should verify coverage against the site if permitted.
    """
    targets = validate_targets(targets)
    max_events_per_target = max(1, min(MAX_EVENTS_PER_TARGET, int(max_events_per_target)))
    collected: list[dict] = []
    target_reports = []
    errors = []
    incomplete = False
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="pl-PL", viewport={"width": 1440, "height": 900})
            try:
                index_page = await context.new_page()
                try:
                    for target in targets:
                        page_url = target["url"]
                        report = {"url": page_url, "sport": target["sport"], "status": "PENDING",
                                  "cards_seen": 0, "parsed_events": 0, "market_count": 0,
                                  "selection_count": 0, "truncated": False}
                        target_reports.append(report)
                        try:
                            resp = await index_page.goto(page_url, wait_until="domcontentloaded", timeout=35000)
                            code = resp.status if resp is not None else None
                            if code is None or code >= 400:
                                raise RuntimeError(f"HTTP_{code or 'UNKNOWN'} target page")
                            listing = extract_event_cards(await index_page.content(), page_url=page_url,
                                                          sport=target["sport"], limit=max_events_per_target)
                            report["cards_seen"] = listing["cards_seen"]
                            report["truncated"] = listing["truncated"]
                            incomplete |= listing["truncated"]
                            if not listing["events"]:
                                report["status"] = "EMPTY_OR_SELECTORS_CHANGED"
                                incomplete = True
                                continue
                            for event in listing["events"]:
                                try:
                                    await asyncio.sleep(random.uniform(0.1, 0.4))
                                    parsed = await read_market_page(context, event, include_stats=include_stats)
                                    if target.get("competition"):
                                        parsed["competition_hint_unverified"] = target["competition"]
                                    collected.append(parsed)
                                    report["parsed_events"] += 1
                                    report["market_count"] += parsed["market_count"]
                                    report["selection_count"] += parsed["selection_count"]
                                    incomplete |= parsed["truncated"] or parsed["selection_count"] == 0
                                except Exception as exc:
                                    incomplete = True
                                    errors.append({"target_url": page_url, "event_url": event["event_url"],
                                                   "type": classify_read_error(exc)})
                            report["status"] = "READ_WITH_MARKETS" if report["selection_count"] > 0 else "NO_MARKET_PRICES"
                        except Exception as exc:
                            incomplete = True
                            report["status"] = classify_read_error(exc)
                            errors.append({"target_url": page_url, "type": report["status"]})
                finally:
                    await index_page.close()
            finally:
                await context.close()
        finally:
            await browser.close()
    market_count = sum(e.get("market_count", 0) for e in collected)
    selection_count = sum(e.get("selection_count", 0) for e in collected)
    complete = bool(selection_count > 0 and not incomplete and not errors)
    return {
        "version": VERSION, "source": "superbet_public_html", "read_at": date_now(),
        "live_http_read_confirmed": any(t["status"] in ("READ_WITH_MARKETS", "NO_MARKET_PRICES") for t in target_reports),
        "live_market_read_confirmed": selection_count > 0,
        "complete_superbet_catalog": False,  # Not proven by any bounded crawl.
        "status": "PARTIAL_SCOPE" if incomplete else ("DOM_READ" if selection_count else "EMPTY"),
        "event_count": len(collected), "market_count": market_count,
        "selection_count": selection_count, "targets": target_reports,
        "errors": errors, "events": collected,
        "ev_enabled": False,
    }


class ReaderState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.result: dict = {"events": [], "event_count": 0, "market_count": 0,
                             "selection_count": 0, "status": "NOT_RUN", "read_at": None,
                             "live_market_read_confirmed": False, "errors": []}
        self.last_attempt: str | None = None
        self.last_success: str | None = None

    async def sync(self, targets: list[dict], *, include_stats: bool = True) -> dict:
        if self.lock.locked():
            raise RuntimeError("SYNC_ALREADY_RUNNING")
        async with self.lock:
            self.last_attempt = date_now()
            try:
                new = await fetch_browser_markets(targets, include_stats=include_stats)
            except Exception as exc:
                self.result = {**self.result, "status": classify_read_error(exc),
                               "errors": [{"type": classify_read_error(exc)}],
                               "live_market_read_confirmed": False}
                return self.result
            # Failure/partial snapshots never overwrite historical success silently.
            if new.get("live_market_read_confirmed"):
                self.last_success = new["read_at"]
            self.result = new
            return new


STATE = ReaderState()
app = FastAPI(title="Superbet FUZJA Czytnik", version=VERSION,
              description="On-demand read-only reader. No automatic scheduler or betting.")


MAX_REQUEST_BODY_BYTES = 2_000_000


@app.middleware("http")
async def require_reader_api_token(request: Request, call_next):
    """Fail closed outside public /health, including docs and all parser APIs."""
    if request.url.path == "/health" and request.method == "GET":
        return await call_next(request)
    expected = os.environ.get("READER_API_TOKEN", "")
    received = request.headers.get("X-Reader-API-Key", "")
    if len(expected) < 32 or not received or not hmac.compare_digest(expected, received):
        return JSONResponse(status_code=401, content={"error": "UNAUTHORIZED"})
    if request.method not in ("GET", "POST"):
        return JSONResponse(status_code=405, content={"error": "METHOD_NOT_ALLOWED"})
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(status_code=413, content={"error": "PAYLOAD_TOO_LARGE"})
            except ValueError:
                return JSONResponse(status_code=400, content={"error": "BAD_CONTENT_LENGTH"})
        chunks = bytearray()
        async for part in request.stream():
            if len(chunks) + len(part) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(status_code=413, content={"error": "PAYLOAD_TOO_LARGE"})
            chunks.extend(part)
        request._body = bytes(chunks)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/health")
def health():
    return {
        "version": VERSION, "status": STATE.result["status"],
        "last_attempt_at": STATE.last_attempt, "last_success_at": STATE.last_success,
        "cached_events": STATE.result.get("event_count", 0),
        "cached_markets": STATE.result.get("market_count", 0),
        "cached_selections": STATE.result.get("selection_count", 0),
        "live_market_read_confirmed": STATE.result.get("live_market_read_confirmed", False),
        "ev_enabled": False, "automatic_scheduler_enabled": False,
        "api_auth_configured": len(os.environ.get("READER_API_TOKEN", "")) >= 32,
        "season_context": season_context(datetime.now(timezone.utc)),
        "targets_configured": len(os.environ.get("SUPERBET_TARGETS_JSON", "")) > 0,
        "nba_hockey_scope_enabled": True,
        "nba_hockey_live_quotes_verified": bool(STATE.result.get("live_market_read_confirmed", False)) and any(
            event.get("sport", "").lower() in ({"nba", "basketball", "nhl", "ahl", "echl", "ohl", "whl", "qmjhl", "ushl", "hockey"})
            for event in STATE.result.get("events", [])),
    }


@app.get("/scope")
def scope():
    return {"sports": SPORTS_SCOPE, "status": "CONFIGURED_NOT_LIVE_VERIFIED",
            "no_ev": True, "no_automatic_bets": True,
            "note": "Coverage describes parsable markets, not confirmed Superbet listings."}


@app.get("/odds")
def odds(sport: str | None = Query(default=None)):
    events = STATE.result.get("events", [])
    if sport:
        if sport.lower() not in ALLOWED_SPORTS:
            raise HTTPException(status_code=422, detail="Unknown sport")
        events = [item for item in events if item.get("sport", "").lower() == sport.lower()]
    return {"source": STATE.result.get("source"), "status": STATE.result["status"],
            "live_market_read_confirmed": STATE.result.get("live_market_read_confirmed", False),
            "read_at": STATE.result.get("read_at"), "count": len(events), "events": events}


@app.post("/parse-snapshot")
def parse_snapshot(request: SnapshotPayload):
    if request.sport.lower() not in ALLOWED_SPORTS:
        raise HTTPException(422, "Unknown sport")
    try:
        result = parse_feed(request.payload, request.sport, only_future=False)
        result.update({"source": "TEST_OFFLINE", "live_market_read_confirmed": False,
                       "complete_superbet_catalog": False, "ev_enabled": False})
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/parse-html")
def parse_html(request: HTMLFixturePayload):
    target = validated_url(request.event_url)
    if not target:
        raise HTTPException(422, "Invalid Superbet HTTPS event URL")
    if request.sport.lower() not in ALLOWED_SPORTS:
        raise HTTPException(422, "Unknown sport")
    if len(request.html) > 2_000_000:
        raise HTTPException(413, "HTML fixture too large")
    result = extract_markets(request.html, event_url=target, sport=request.sport)
    return {**result, "source": "TEST_OFFLINE", "live_market_read_confirmed": False,
            "ev_enabled": False}


@app.post("/sync")
async def sync(x_reader_token: str | None = Header(default=None)):
    expected = os.environ.get("READER_SYNC_TOKEN")
    if not expected:
        raise HTTPException(403, "Sync disabled: configure READER_SYNC_TOKEN explicitly")
    if not x_reader_token or not hmac.compare_digest(expected, x_reader_token):
        raise HTTPException(403, "Forbidden")
    try:
        targets = targets_from_env()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not targets:
        raise HTTPException(422, "No verified targets configured")
    if STATE.lock.locked():
        raise HTTPException(409, "Sync already running")
    return await STATE.sync(targets)


def cli() -> int:
    p = argparse.ArgumentParser(description="FUZJA Czytnik 4.0 (no automatic scheduler)")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--parse-snapshot", type=Path, help="Offline provider JSON snapshot")
    action.add_argument("--parse-html", type=Path, help="Offline event HTML")
    action.add_argument("--diagnose", action="store_true", help="Local DNS check only")
    action.add_argument("--live", action="store_true", help="Manual bounded browsing; targets required")
    p.add_argument("--sport", default="all")
    p.add_argument("--url", default="https://superbet.pl/events/example", help="Only used for HTML fixture")
    p.add_argument("--targets", type=Path, help="JSON list of explicit, verified sport URLs")
    p.add_argument("--out", type=Path, help="Save JSON result")
    args = p.parse_args()
    if args.parse_snapshot:
        data = json.loads(args.parse_snapshot.read_text(encoding="utf-8"))
        result = parse_feed(data, args.sport, only_future=False)
        result.update({"source": "TEST_OFFLINE", "live_market_read_confirmed": False,
                       "ev_enabled": False})
    elif args.parse_html:
        result = extract_markets(args.parse_html.read_text(encoding="utf-8"),
                                 event_url=args.url, sport=args.sport)
        result.update({"source": "TEST_OFFLINE", "live_market_read_confirmed": False,
                       "ev_enabled": False})
    elif args.diagnose:
        targets = validate_targets(json.loads(args.targets.read_text(encoding="utf-8"))) if args.targets else [
            {"sport": "all", "url": "https://superbet.pl"}]
        result = {"diagnostics": [connection_diagnosis(x["url"]) for x in targets]}
    else:
        if not args.targets:
            p.error("--live requires --targets JSON file with verified URLs")
        targets = validate_targets(json.loads(args.targets.read_text(encoding="utf-8")))
        result = asyncio.run(fetch_browser_markets(targets))
    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(f"Saved {args.out}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
