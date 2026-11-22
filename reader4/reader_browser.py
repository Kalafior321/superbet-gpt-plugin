"""Conservative, read-only HTML adapters for PUBLIC Superbet pages.

HTML layouts are unverified. Selectors must be tested against a permitted live page.
Unknown markets survive as raw records; synthetic local IDs are never provider IDs.
No anti-bot circumvention, account automation, or bet placement.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

WARSAW = ZoneInfo("Europe/Warsaw")
ALLOWED_HOSTS = {"superbet.pl", "www.superbet.pl"}
MAX_MARKETS = 1000
MAX_SELECTIONS = 5000
MAX_CARDS = 500

EVENT_SELECTORS = ".event-card, .event-row, [data-testid='event-row']"
TEAM_SELECTORS = ".event-card__team-name, .team-name, [data-testid='team-name']"
MARKET_SELECTORS = ".market-group, [data-testid='market-group'], [data-qa='market-group'], [class~='market-group']"
MARKET_NAME_SELECTORS = ".market-group__title, .market-name, [data-testid='market-name'], [class*='market-name']"
SELECTION_SELECTORS = ".odd-button, .selection-button, [data-testid='odd-button'], [data-testid='selection-button'], button[data-price], button[data-odd]"
PRICE_SELECTORS = ".odd-button__value, .odd-value, [data-testid='odd-value'], [data-testid='price'], [data-price], [data-odd]"
LABEL_SELECTORS = ".odd-button__label, .selection-name, [data-testid='selection-name'], [data-testid='outcome-name']"


def text(value: Any) -> str:
    return " ".join(str(value or "").split())


def validated_url(raw: str, base: str = "https://superbet.pl/") -> str | None:
    """Allow only https Superbet links; protect crawler from cross-domain hrefs."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = urljoin(base, raw.strip())
    try:
        u = urlsplit(candidate)
        if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
            return None
        if u.username or u.password or u.port not in (None, 443):
            return None
        # Prevent confusion with numeric IPs and js/data/mailto refs.
        try:
            ipaddress.ip_address(u.hostname)
            return None
        except ValueError:
            pass
        if u.fragment:
            candidate = candidate.split("#", 1)[0]
        return candidate
    except ValueError:
        return None


def parse_odd(raw: Any) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*", str(raw))
    if match is None:
        return None
    n = float(match.group(1).replace(",", "."))
    return n if math.isfinite(n) and 1.0 < n <= 10000 else None


def maybe_line(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    found = re.search(r"(?<!\d)(-?\d+(?:[.,]\d+))(?!\d)", str(raw))
    if not found:
        return None
    n = float(found.group(1).replace(",", "."))
    return n if math.isfinite(n) else None


def _attr(tag: Tag, *attrs: str) -> str | None:
    for attr in attrs:
        val = tag.get(attr)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _local_id(prefix: str, *values: str) -> str:
    return "local:" + prefix + ":" + hashlib.sha256("|".join(values).encode()).hexdigest()[:16]


def _deepest_group_candidates(soup: BeautifulSoup) -> list[Tag]:
    # If a UI wraps one market inside a container with the same CSS class,
    # the outer element must not duplicate inner selections.
    groups: list[Tag] = []
    for group in soup.select(MARKET_SELECTORS):
        if group.select_one(MARKET_SELECTORS):
            continue
        groups.append(group)
    return groups


def extract_markets(html: str, *, event_url: str, sport: str = "all", observed_at: str | None = None) -> dict:
    """Extract every market with identifiable DOM selections, not just title text.

    Unknown name/category remains RAW_UNCLASSIFIED. Browser HTML has no
    guaranteed market IDs and therefore cannot prove provider ID coverage.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    groups = _deepest_group_candidates(soup)
    markets = []
    selected_total = 0
    truncated = len(groups) > MAX_MARKETS
    observed = observed_at or datetime.now(WARSAW).isoformat()
    for index, group in enumerate(groups[:MAX_MARKETS]):
        title = group.select_one(MARKET_NAME_SELECTORS)
        market_name = text(title.get_text(" ", strip=True) if title else "") or "Nieznany rynek"
        market_id = _attr(group, "data-market-id", "data-marketid", "data-id")
        provider_market_id = bool(market_id)
        market_id = market_id or _local_id("market", event_url, str(index), market_name)
        choices = group.select(SELECTION_SELECTORS)
        market_choices = []
        for ci, button in enumerate(choices):
            if selected_total >= MAX_SELECTIONS:
                truncated = True
                break
            classes = set(button.get("class") or [])
            if (button.get("disabled") is not None or button.get("aria-disabled") == "true"
                    or "suspended" in classes or "disabled" in classes):
                continue
            price_tag = button.select_one(PRICE_SELECTORS)
            direct_price = _attr(button, "data-price", "data-odd")
            if price_tag is not None:
                direct_price = _attr(price_tag, "data-price", "data-odd") or text(price_tag.get_text(" ", strip=True))
            if not direct_price:
                # Some pages render '<label>Over 2.5</label> <span>1.95</span>'.
                # Restrict fallback to a terminal token so 2.5 is never
                # mistaken for odds in a complete label.
                fallback = re.search(r"(\d+(?:[.,]\d+)?)\s*$", text(button.get_text(" ", strip=True)))
                direct_price = fallback.group(1) if fallback else None
            price = parse_odd(direct_price)
            if price is None:
                continue
            label_tag = button.select_one(LABEL_SELECTORS)
            raw_text = text(button.get_text(" ", strip=True))
            if label_tag is not None:
                label = text(label_tag.get_text(" ", strip=True))
            else:
                label = raw_text
                trailing = str(direct_price or "").strip()
                if trailing and label.endswith(trailing):
                    label = text(label[:-len(trailing)])
                label = label or "Nienazwana selekcja"
            line = maybe_line(_attr(button, "data-line", "data-handicap"))
            if line is None:
                line = maybe_line(label)
            selection_id = _attr(button, "data-selection-id", "data-outcome-id", "data-id")
            selection_provider_id = bool(selection_id)
            selection_id = selection_id or _local_id("selection", str(market_id), str(ci), label)
            market_choices.append({
                "selection_id": selection_id, "selection_id_is_provider": selection_provider_id,
                "label": label, "price": price, "line": line,
                "period_label": market_name, "raw_text": raw_text,
                "observed_at": observed,
            })
            selected_total += 1
        markets.append({
            "market_id": market_id, "market_id_is_provider": provider_market_id,
            "name": market_name, "status": "RAW_UNCLASSIFIED",
            "sport": sport, "selections": market_choices,
            "selection_count": len(market_choices),
        })
    return {
        "markets": markets,
        "market_count": len(markets),
        "selection_count": selected_total,
        "unclassified_market_count": len(markets),
        "groups_seen": len(groups), "truncated": truncated,
        "selectors_verified_live": False,
    }


def extract_event_cards(html: str, *, page_url: str, sport: str = "all", limit: int = MAX_CARDS) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    found = soup.select(EVENT_SELECTORS)
    cards = []
    seen = set()
    for node in found:
        if len(cards) >= limit:
            break
        teams = [text(t.get_text(" ", strip=True)) for t in node.select(TEAM_SELECTORS)]
        teams = [n for n in teams if n]
        if len(teams) < 2:
            continue
        hrefs = [a.get("href") for a in node.select("a[href]")]
        links = [validated_url(x, page_url) for x in hrefs]
        event_url = next((x for x in links if x and x != page_url), None)
        if not event_url or event_url in seen:
            continue
        seen.add(event_url)
        provider_id = _attr(node, "data-event-id", "data-fixture-id")
        cards.append({
            "event_id": provider_id or _local_id("event", event_url, *teams[:2]),
            "event_id_is_provider": bool(provider_id), "sport": sport,
            "home": teams[0], "away": teams[1],
            "match_name": f"{teams[0]} - {teams[1]}", "event_url": event_url,
        })
    return {"events": cards, "cards_seen": len(found), "truncated": len(found) > limit,
            "skipped_unparsed": max(0, min(len(found), limit) - len(cards))}


def extract_extra_stats(html: str) -> dict:
    """Preserve original user's H2H/form/table parsing with modest size limits."""
    soup = BeautifulSoup(html or "", "html.parser")
    def values(selector: str, cap: int) -> list[str]:
        return [text(x.get_text(" ", strip=True)) for x in soup.select(selector)[:cap]
                if text(x.get_text(" ", strip=True))]
    return {
        "h2h_history": values("[class*='h2h'] tr, .h2h-match-row", 20),
        "team_form": values("[class*='form-badge'], [class*='form'] span", 20),
        "standings": values("table tbody tr", 20),
        "stats_metrics": {f"stat_{i+1}": v for i, v in enumerate(values("[class*='stat-row'], [class*='comparison']", 50))},
    }
