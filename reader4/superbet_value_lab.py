"""Superbet FUZJA v3: read-only, all-market prematch parser.

Parses a Superbet-like /v3/.../events feed WITHOUT assuming unknown sport IDs,
market IDs, API schema changes, or actual live access. Nothing here places bets.
Raw observations do not prove market freshness; check provider timestamps separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

LOG = logging.getLogger("superbet_reader")
WARSAW = ZoneInfo("Europe/Warsaw")
API_URL = "https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL/events"
EXPECTED_HOST = "production-superbet-offer-pl.freetls.fastly.net"
ORIGIN = "https://superbet.pl"
MAX_EVENTS = 2000
MAX_MARKETS_PER_EVENT = 1000
MAX_ODDS_PER_MARKET = 500
ALLOWED_SPORTS = {"football", "soccer", "nba", "nhl", "ahl", "hockey", "basketball", "all"}


def textnorm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _price(odd: dict) -> float | None:
    value = odd.get("price")
    if isinstance(value, bool):
        return None
    try:
        price = float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None
    return price if math.isfinite(price) and 1 < price <= 10000 else None


def _flag_active(data: dict) -> bool:
    # Do not require a particular field if provider omitted it, but never
    # publish explicitly suspended / inactive market selections.
    for key in ("active", "isActive", "marketActive", "bookmakerIsActive", "selectionActive"):
        if data.get(key) is False:
            return False
    for key in ("suspended", "isSuspended", "blocked", "closed"):
        if data.get(key) is True:
            return False
    status = textnorm(data.get("status"))
    return status not in {"suspended", "inactive", "closed", "settled", "cancelled", "canceled", "removed"}


def _first(value: dict, *keys: str) -> Any:
    for key in keys:
        v = value.get(key)
        if v is not None and v != "":
            return v
    return None


def _datetime(raw: Any) -> datetime | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        t = float(raw)
        if t > 1e12:  # milliseconds
            t /= 1000
        if t < 0:
            return None
        try:
            return datetime.fromtimestamp(t, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        try:
            t = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            return t.astimezone(timezone.utc) if t.tzinfo else None
        except ValueError:
            return None
    return None


def _extract_line(market: dict, odd: dict, market_name: str, outcome: str) -> float | None:
    # Explicit provider line fields have precedence over text, unlike odds price.
    for obj in (odd, odd.get("metadata") or {}, market, market.get("metadata") or {}):
        if not isinstance(obj, dict):
            continue
        for field in ("line", "handicap", "total", "points", "special", "specifier", "threshold"):
            val = obj.get(field)
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)) and math.isfinite(val) and val >= 0:
                return float(val)
            if isinstance(val, str):
                m = re.search(r"(?:^|[=:\s])(-?\d+(?:[.,]\d+)?)\s*$", val.strip())
                if m:
                    num = float(m.group(1).replace(",", "."))
                    if num >= 0:
                        return num
    for txt in (outcome, market_name):
        # Decimals only: don't confuse "1st period" / "1. tercja" with 1 point.
        found = re.findall(r"(?<!\d)(\d{1,3}[.,]\d{1,2})(?!\d)", txt)
        if found:
            return float(found[-1].replace(",", "."))
    return None


def _side(outcome: str) -> str | None:
    s = textnorm(outcome)
    if re.search(r"\b(over|powyzej|wiecej|above|ponad)\b", s) or s.startswith("o "):
        return "over"
    if re.search(r"\b(under|ponizej|mniej|below)\b", s) or s.startswith("u "):
        return "under"
    return None


def _period(name: str) -> str:
    s = textnorm(name)
    if re.search(r"\b(1\s*\.?\s*tercja|pierwsza tercja|1st period|first period|period 1|1\s*\.?\s*period)\b", s):
        return "p1"
    if re.search(r"\b(2\s*\.?\s*tercja|druga tercja|2nd period|second period|period 2)\b", s):
        return "p2"
    if re.search(r"\b(3\s*\.?\s*tercja|trzecia tercja|3rd period|third period|period 3)\b", s):
        return "p3"
    if re.search(r"\b(1st half|first half|1\s*\.?\s*polowa|pierwsza polowa)\b", s):
        return "h1"
    return "full"


def _market_kind(name: str, sport: str) -> str | None:
    s = textnorm(name)
    if s in {"1x2", "mecz", "zwyciezca meczu", "full time result", "match result"}:
        return "1x2"
    if any(w in s for w in ("kartki", "cards", "rozn", "corners", "faule", "fouls", "strzaly", "shots", "asysty", "assists", "rebound", "zbiork", "zawodnik", "player", "handicap", "spread")):
        return None
    # Scope O/U to identifiable team/total markets, not all numeric price names.
    total_words = ("over/under", "under/over", "suma", "laczna", "liczba", "total", "gole", "goli", "bramki", "bramek", "goals", "punkty", "punktow", "points")
    if not any(w in s for w in total_words):
        return None
    if sport in ("nba", "basketball") or (sport == "all" and any(w in s for w in ("points", "punkty", "punktow"))):
        if any(w in s for w in ("gole", "goli", "goals", "bramki", "bramek")):
            return None
        return "points"
    if sport in ("nhl", "ahl", "hockey", "football", "soccer") or (sport == "all" and any(w in s for w in ("goals", "gole", "goli", "bramki", "bramek"))):
        if any(w in s for w in ("punkty", "punktow", "points")):
            return None
        return "goals"
    return None


CATEGORY_RULES = (
    ("player_props", ("zawodnik", "player", "strzelec", "scorer", "anytime goalscorer", "pts+reb", "punkty zawodnika")),
    ("corners", ("rozn", "corner")),
    ("cards", ("kartk", "yellow card", "red card", "booking")),
    ("shots", ("strzal", "shot", "shots on target")),
    ("fouls", ("faul", "foul")),
    ("offsides", ("spalon", "offside")),
    ("assists", ("asyst", "assist")),
    ("rebounds", ("zbiork", "rebound")),
    ("three_pointers", ("three-pointer", "three pointer", "trojk", "3-punkt")),
    ("saves", ("interwenc", "save")),
    ("penalties", ("rzut karn", "penalty", "karne")),
    ("handicap", ("handicap", "spread", "azjatycki", "asian")),
    ("btts", ("btts", "obie druzyny", "both teams to score")),
    ("double_chance", ("podwojna szansa", "double chance")),
    ("exact_score", ("dokladny wynik", "correct score", "exact score")),
    ("period", ("tercja", "period", "quarter", "kwarta", "polowa", "half")),
    ("goals", ("bramk", "gol", "goal")),
    ("points", ("punkt", "point")),
    ("winner", ("zwyciezca", "winner", "result", "1x2")),
)


def _category(market_name: str, kind: str | None) -> str:
    """Best-effort *tag* for navigation, not a proven interpretation of odds."""
    if kind == "1x2":
        return "winner"
    text = textnorm(market_name)
    for category, hints in CATEGORY_RULES:
        if any(hint in text for hint in hints):
            return category
    return kind or "other"


def _generic_key(market: dict, odd: dict, name: str, outcome: str, line: float | None) -> str:
    """Opaque stable identity. Never infer a bet outcome from unfamiliar text."""
    mid = _first(market, "id", "marketId")
    oid = _first(odd, "id", "oddId")
    ident = [str(mid) if mid is not None else textnorm(name),
             str(oid) if oid is not None else textnorm(outcome),
             str(line) if line is not None else ""]
    token = hashlib.sha256(json.dumps(ident, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    return f"raw.{token}"


def _competition(event: dict, fixture: dict) -> dict:
    """Preserve provider competition and tier evidence; unknown remains unknown."""
    comp = _first(event, "competition", "tournament", "league")
    if comp is None:
        comp = _first(fixture, "competition", "tournament", "league")
    if isinstance(comp, dict):
        name = _first(comp, "name", "title", "competitionName")
        country = _first(comp, "country", "countryName")
        tier = _first(comp, "tier", "level", "division")
    else:
        name, country, tier = comp, None, None
    if name is None:
        name = _first(event, "competitionName", "tournamentName", "leagueName") or _first(fixture, "competitionName", "leagueName")
    if country is None:
        country = _first(event, "countryName", "country")
    if not isinstance(tier, int) or isinstance(tier, bool) or tier not in (1, 2, 3):
        tier = None
    return {"name": str(name) if name is not None else None,
            "country": str(country) if country is not None else None,
            "tier_from_provider": tier,
            "is_mls": textnorm(name) in {"mls", "major league soccer", "usa mls"} if name else False}


def _which_team(market: dict, name: str, home: str, away: str) -> str | None:
    s = textnorm(name)
    metadata = market.get("metadata") or {}
    # Explicit numeric identifier only when unambiguous, no guesses on random IDs.
    target = _first(metadata, "team", "participant", "teamName") if isinstance(metadata, dict) else None
    if target:
        s += " " + textnorm(target)
    home_norm, away_norm = textnorm(home), textnorm(away)
    if home_norm and home_norm in s and (not away_norm or away_norm not in s):
        return "home"
    if away_norm and away_norm in s and (not home_norm or home_norm not in s):
        return "away"
    # Match dedicated team-total naming; do NOT treat "1. tercja" as team 1.
    if re.search(r"\b(team|druzyn[ay]|zespolu|participant)\s*(1|home|gospodarz(?:e|y)?)\b", s):
        return "home"
    if re.search(r"\b(team|druzyn[ay]|zespolu|participant)\s*(2|away|goscie|gosci)\b", s):
        return "away"
    return None


def _participants(event: dict) -> tuple[str, str, str]:
    fix = event.get("fixture") or {}
    if not isinstance(fix, dict):
        fix = {}
    title = str(_first(fix, "event_name", "name", "eventName") or _first(event, "name", "eventName") or "")
    home = _first(fix, "home", "homeName", "homeTeamName", "participant1Name") or _first(event, "participant1Name", "homeTeamName")
    away = _first(fix, "away", "awayName", "awayTeamName", "participant2Name") or _first(event, "participant2Name", "awayTeamName")
    if not home or not away:
        parts = re.split(r"\s+(?:vs\.?|v\.?|[-–—])\s+", title, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            home = home or parts[0]
            away = away or parts[1]
    return str(home or "").strip(), str(away or "").strip(), title


def season_context(now: datetime) -> dict:
    """Calendar-based context, NOT an official season assigned to a specific event.

    The European July rollover is a reporting convention. League calendars and
    competitions may differ. Explicit provider season metadata takes precedence.
    MLS uses calendar years. Three completed seasons exclude the current one.
    """
    if now.tzinfo is None:
        raise ValueError("Season reference time requires timezone")
    local = now.astimezone(WARSAW)
    start_year = local.year if local.month >= 7 else local.year - 1
    europe_current = f"{start_year}/{str(start_year + 1)[-2:]}"
    europe_history = [f"{year}/{str(year + 1)[-2:]}" for year in range(start_year - 3, start_year)]
    mls_history = [str(year) for year in range(local.year - 3, local.year)]
    return {"as_of_local_date": local.date().isoformat(),
            "current_european": europe_current,
            "last_three_completed_european": europe_history,
            "current_mls": str(local.year),
            "last_three_completed_mls": mls_history,
            "inferred_calendar_context_only": True}


def parse_feed(payload: dict, sport: str, *, read_at: datetime | None = None,
               only_future: bool = True) -> dict:
    """Normalize verified *structural* matches and market lines; fail on unknown schema.

    Each selection keeps explicit evidence. Missing source timestamps are flagged,
    never disguised as fresh observations. `read_at` is the read clock, not quote time.
    """
    sport = sport.lower()
    if sport not in ALLOWED_SPORTS:
        raise ValueError(f"Nieobsługiwany sport: {sport}")
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("Nieznany schemat odpowiedzi: brak listy events")
    if len(payload["events"]) > MAX_EVENTS:
        raise ValueError("Za dużo wydarzeń w odpowiedzi")
    now = read_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("read_at musi zawierać strefę czasową")
    observed = now.astimezone(timezone.utc).isoformat()
    rows, warnings = [], []
    for idx, event in enumerate(payload["events"]):
        if not isinstance(event, dict) or not _flag_active(event):
            continue
        fixture = event.get("fixture") or {}
        if not isinstance(fixture, dict):
            fixture = {}
        home, away, title = _participants(event)
        if not home or not away:
            warnings.append(f"event[{idx}]: brak pary drużyn")
            continue
        event_time = _datetime(_first(fixture, "startTime", "start_time", "startDate", "date") or
                               _first(event, "startTime", "start_time", "startDate", "date"))
        if only_future and (event_time is None or event_time <= now):
            # A prematch quote requires a verifiable future start time.
            continue
        if not isinstance(event.get("markets", []), list):
            warnings.append(f"event[{idx}]: markets nie jest listą")
            continue
        markets = event.get("markets") or []
        if len(markets) > MAX_MARKETS_PER_EVENT:
            warnings.append(f"event[{idx}]: za dużo rynków")
            continue
        selections, collision = {}, set()
        for market in markets:
            if not isinstance(market, dict) or not _flag_active(market):
                continue
            name = str(_first(market, "name", "marketName", "label") or "")
            kind = _market_kind(name, sport)
            # FUZJA v3.1: unknown markets are *kept as raw selections* rather
            # than being silently dropped or misinterpreted as totals.
            category = _category(name, kind)
            scope = _period(name)
            team = _which_team(market, name, home, away) if kind in {"points", "goals"} else None
            # If team name appears only in one selection, we'll infer per selection.
            offer_list = market.get("odds") or []
            if not isinstance(offer_list, list):
                warnings.append(f"{title}: nieznany format odds rynku {name!r}")
                continue
            if len(offer_list) > MAX_ODDS_PER_MARKET:
                warnings.append(f"{title}: za wiele kursów rynku {name!r}")
                continue
            for odd in offer_list:
                if not isinstance(odd, dict) or not _flag_active(odd):
                    continue
                metadata = odd.get("metadata") if isinstance(odd.get("metadata"), dict) else {}
                outcome = str(_first(metadata, "name", "label") or
                              _first(odd, "name", "label", "outcomeName") or "")
                price = _price(odd)
                if price is None:
                    continue
                effective_kind = kind
                if effective_kind == "1x2":
                    label = textnorm(outcome)
                    if label in {"1", "home", "gospodarze"}:
                        side = "1"
                    elif label in {"x", "draw", "remis"}:
                        side = "X"
                    elif label in {"2", "away", "goscie"}:
                        side = "2"
                    else:
                        side = None
                    line = None
                    team_side = None
                    if side:
                        key = f"1x2.full.{side}"
                    else:
                        effective_kind = None
                        key = _generic_key(market, odd, name, outcome, line)
                elif effective_kind in {"points", "goals"}:
                    side = _side(outcome)
                    line = _extract_line(market, odd, name, outcome)
                    team_side = team or _which_team(market, outcome, home, away)
                    is_team = any(x in textnorm(name) for x in ("druzyn", "zespol", "team", "participant"))
                    if side and line is not None and line >= 0 and (not is_team or team_side):
                        which = "team_" + team_side if team_side else "total"
                        key = f"{effective_kind}.{scope}.{which}.{side}.{line:g}"
                    else:
                        # An ambiguous offer is retained, but never assigned an
                        # interpreted key that would imply a verified market.
                        key = _generic_key(market, odd, name, outcome, line)
                        effective_kind = None
                else:
                    side = _side(outcome)
                    line = _extract_line(market, odd, name, outcome)
                    team_side = _which_team(market, name, home, away)
                    if not name and _first(market, "id", "marketId") is None:
                        warnings.append(f"{title}: rynek bez nazwy i ID, pominięto")
                        continue
                    if not outcome and _first(odd, "id", "oddId") is None:
                        warnings.append(f"{title}: kurs bez nazwy i ID, pominięto")
                        continue
                    key = _generic_key(market, odd, name, outcome, line)
                item = {"key": key, "market": effective_kind or "raw", "category": category,
                        "interpreted": bool(effective_kind), "period": scope,
                        "team": team_side, "side": side, "line": line,
                        "price": price, "market_name": name, "outcome_name": outcome,
                        "market_id": _first(market, "id", "marketId"),
                        "outcome_id": _first(odd, "id", "oddId"),
                        "provider_changed_at": _first(odd, "changedAt", "updatedAt") or
                                               _first(metadata, "changedAt", "updatedAt")}
                if key in selections and selections[key]["price"] != price:
                    collision.add(key)
                else:
                    selections.setdefault(key, item)
        for key in collision:
            selections.pop(key, None)
            warnings.append(f"{title}: sprzeczne ceny {key}, odrzucono")
        rows.append({"event_id": _first(event, "id", "eventId", "fixtureId"),
                     "event_name": title or f"{home} vs {away}",
                     "home": home, "away": away,
                     "start_utc": event_time.isoformat() if event_time else None,
                     "sport_requested": sport, "competition": _competition(event, fixture),
                     "quote_time_verified": False,
                     "observed_at_utc": observed,
                     "selections": sorted(selections.values(), key=lambda s:s["key"])})
    return {"source": "Superbet PL feed (only if verified by fetch_feed)",
            "observed_at_utc": observed, "quote_time_verified": False,
            "sport_requested": sport, "events": rows, "warnings": warnings,
            "season_context": season_context(now),
            "event_count": len(rows), "selections_count": sum(len(x["selections"]) for x in rows),
            "raw_selections_count": sum(not sel["interpreted"] for ev in rows for sel in ev["selections"]),
            "categories": sorted({sel["category"] for ev in rows for sel in ev["selections"]})}


def _dates(days: int = 2) -> tuple[str, str]:
    if not 1 <= days <= 7:
        raise ValueError("days: 1..7")
    today = datetime.now(WARSAW).date()
    start = datetime(today.year, today.month, today.day, tzinfo=WARSAW)
    end = start + timedelta(days=days)
    return (start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))


def http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "FUZJA-Reader/3.1 (read-only)",
                            "Origin": ORIGIN, "Referer": ORIGIN + "/"})
    client_id = os.getenv("SUPERBET_PUBLIC_CLIENT_ID")
    if client_id:
        session.headers["X-Client-Id"] = client_id
    return session


def sport_ids_config() -> dict[str, int]:
    """Use observed sport IDs only. Earlier code observed football=5, not NBA/AHL."""
    raw = os.getenv("SUPERBET_SPORT_IDS_JSON", "{}")
    cfg = json.loads(raw)
    if not isinstance(cfg, dict):
        raise ValueError("SUPERBET_SPORT_IDS_JSON musi być obiektem JSON")
    out = {"football": 5}
    for key, value in cfg.items():
        if key not in ALLOWED_SPORTS - {"all"} or isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"Nieprawidłowe sport ID: {key}")
        out[key] = value
    return out


def fetch_feed(*, sport: str, sport_id: int | None = None, days: int = 2,
               session: requests.Session | None = None) -> dict:
    sport = sport.lower()
    if sport not in ALLOWED_SPORTS:
        raise ValueError("Nieobsługiwany sport")
    if sport_id is None and sport != "all":
        sport_id = sport_ids_config().get(sport)
        if sport_id is None:
            raise ValueError(f"Brak zweryfikowanego sport ID dla {sport}; ustaw SUPERBET_SPORT_IDS_JSON")
    if sport_id is not None and (isinstance(sport_id, bool) or not isinstance(sport_id, int) or sport_id < 1):
        raise ValueError("sport_id musi być dodatnią liczbą całkowitą")
    if urlparse(API_URL).hostname != EXPECTED_HOST:
        raise ValueError("Nieoczekiwany host feedu")
    start, end = _dates(days)
    params = {"startDate": start, "endDate": end, "index": "active-prematch"}
    if sport_id is not None:
        params["sports"] = str(sport_id)
    s = session or http_session()
    response = s.get(API_URL, params=params, timeout=12, allow_redirects=False)
    if response.status_code in (401, 403, 429):
        raise RuntimeError(f"Feed nieudostępniony: HTTP {response.status_code}; bez ponawiania/obchodzenia blokady")
    if 300 <= response.status_code < 400:
        raise RuntimeError("Niespodziewane przekierowanie feedu")
    response.raise_for_status()
    ctype = response.headers.get("Content-Type", "application/json")
    if "json" not in ctype.lower():
        raise RuntimeError(f"Niepoprawny content-type: {ctype}")
    payload = response.json()
    result = parse_feed(payload, sport, only_future=True)
    result.update({"request_url": API_URL, "sport_id_used": sport_id, "http_status": response.status_code,
                   "source": "Superbet public offer feed (direct read)",
                   "live_fetch_confirmed": True})
    return result



def fetch_superbet_odds(sport_id: int = 5) -> list[dict]:
    """Compatibility interface for legacy football scans (read-only)."""
    fetched = fetch_feed(sport="football", sport_id=sport_id)
    matches = []
    for ev in fetched["events"]:
        prices = {k: None for k in ("1", "X", "2", "Over_2.5", "Under_2.5")}
        for sel in ev["selections"]:
            if sel["key"].startswith("1x2.full."):
                prices[sel["side"]] = sel["price"]
            elif sel["key"] in ("goals.full.total.over.2.5", "goals.full.total.under.2.5"):
                prices["Over_2.5" if sel["side"] == "over" else "Under_2.5"] = sel["price"]
        matches.append({"home_raw": ev["home"], "away_raw": ev["away"],
                        "match_name": ev["event_name"], "odds": prices})
    return matches


def rank_by_probability(selections: list[dict], estimated_probabilities: dict[str, float],
                        *, min_probability: float = 0.70) -> list[dict]:
    """Sort by externally estimated probabilities; no EV or unverified confidence.

    Odds never generate probabilities. Raw markets remain in scope but cannot
    receive made-up probabilities merely because their prices are available.
    """
    if not 0 <= min_probability <= 1:
        raise ValueError("min_probability musi być w zakresie 0..1")
    result = []
    for sel in selections:
        p = estimated_probabilities.get(sel["key"])
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p < 1:
            continue
        if p >= min_probability:
            result.append({"key": sel["key"], "probability_pct": round(100 * p, 2),
                           "market_category": sel.get("category", sel.get("market", "raw")),
                           "price": sel["price"], "probability_source": "external_model",
                           "needs_quote_verification": True,
                           "status": "DO_WALIDACJI"})
    return sorted(result, key=lambda r: (-r["probability_pct"], r["key"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Superbet FUZJA read-only reader")
    parser.add_argument("--sport", choices=sorted(ALLOWED_SPORTS), default="football")
    parser.add_argument("--sport-id", type=int, default=None, help="Tylko ID zweryfikowane w publicznym feedzie")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--fixture", type=Path, help="Offline: ścieżka do próbki JSON z feedu")
    parser.add_argument("--out", type=Path, default=Path("superbet_quotes.json"))
    args = parser.parse_args()
    try:
        if args.fixture:
            payload = json.loads(args.fixture.read_text(encoding="utf-8"))
            result = parse_feed(payload, args.sport, only_future=False)
            result["source"] = "offline_fixture_not_live"
            result["live_fetch_confirmed"] = False
        else:
            result = fetch_feed(sport=args.sport, sport_id=args.sport_id, days=args.days)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Zapisano: {args.out} | mecze: {result['event_count']} | kursy: {result['selections_count']} | live: {result['live_fetch_confirmed']}")
    except Exception as exc:
        LOG.error("Nie udało się odczytać: %s", exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
