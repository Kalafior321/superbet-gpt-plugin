"""Deterministic extraction of explicit public sports markup, no external API."""
import json
from datetime import datetime
from bs4 import BeautifulSoup

MAX_HTML = 2_000_000
MAX_EVENTS = 100
MAX_TABLES = 20
MAX_ROWS = 200


def _name(value):
    value = value.get('name') if isinstance(value, dict) else value
    return value.strip()[:160] if isinstance(value, str) and value.strip() else None


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _nodes(value, depth=0):
    if depth > 12:
        return
    if isinstance(value, list):
        for item in value[:1000]:
            yield from _nodes(item, depth + 1)
    elif isinstance(value, dict):
        yield value
        for key in ('@graph', 'itemListElement', 'item', 'mainEntity', 'subEvent'):
            if key in value:
                yield from _nodes(value[key], depth + 1)


def parse_public_html(html, source_url):
    if len(html) > MAX_HTML:
        return {'events': [], 'tables': [], 'extraction_status': 'document_too_large',
                'structured_data_verified': False, 'paid_api_used': False}
    soup = BeautifulSoup(html, 'html.parser')
    events, tables, seen = [], [], set()
    for script in soup.select('script[type="application/ld+json"]')[:40]:
        try:
            value = json.loads(script.get_text())
        except (ValueError, RecursionError):
            continue
        for node in _nodes(value):
            types = node.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list) or not any(t in ('SportsEvent', 'https://schema.org/SportsEvent', 'http://schema.org/SportsEvent') for t in types):
                continue
            home, away = _name(node.get('homeTeam')), _name(node.get('awayTeam'))
            start = _time(node.get('startDate'))
            if not home or not away or not start or home == away:
                continue
            key = (home, away, start)
            if key in seen or len(events) >= MAX_EVENTS:
                continue
            seen.add(key)
            events.append({'home_team': home, 'away_team': away, 'start_at': start,
                           'source_url': source_url, 'source_format': 'json_ld',
                           'odds_verified_as_superbet': False})
    for table in soup.find_all('table', limit=MAX_TABLES):
        rows = []
        for tr in table.find_all('tr', limit=MAX_ROWS):
            cells = [' '.join(td.stripped_strings)[:400] for td in tr.find_all(['th', 'td'], recursive=False)[:40]]
            if cells:
                rows.append(cells)
        if rows:
            tables.append({'rows': rows, 'source_format': 'html_table', 'source_url': source_url})
    return {'events': events, 'tables': tables,
            'extraction_status': 'explicit_markup' if events or tables else 'no_structured_markup',
            'structured_data_verified': False, 'paid_api_used': False}
