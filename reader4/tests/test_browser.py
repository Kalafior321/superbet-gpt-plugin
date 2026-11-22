import asyncio
import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app, STATE, validate_targets, connection_diagnosis, classify_read_error
from reader_browser import validated_url, parse_odd, maybe_line, extract_event_cards, extract_markets, extract_extra_stats
from superbet_value_lab import season_context
from market_history import EUROPE_SEASONS, MLS_SEASONS, summarize_three_seasons

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'fixtures' / 'sample_event.html').read_text(encoding='utf-8')

@pytest.mark.parametrize('url', [
    'http://superbet.pl/mecz/1',
    'https://superbet.pl.evil.invalid/mecz/1',
    'https://evil.invalid/mecz/1',
    'javascript:alert(1)',
    'https://user:password@superbet.pl/path',
    'https://superbet.pl:444/path',
    '',
])
def test_url_rejects_bad(url):
    assert validated_url(url) is None


def test_url_accepts_relative_https():
    assert validated_url('/mecz/123') == 'https://superbet.pl/mecz/123'
    assert validated_url('https://superbet.pl/mecz/1#tab') == 'https://superbet.pl/mecz/1'


def test_target_config_does_not_duplicate_homepage():
    with pytest.raises(ValueError, match='repeated'):
        validate_targets([{'sport':'football', 'url':'https://superbet.pl'},
                          {'sport':'nba','url':'https://superbet.pl'}])
    assert validate_targets([{'sport':'all','url':'https://superbet.pl'}])[0]['sport'] == 'all'


def test_target_config_rejects_wrong_sport():
    with pytest.raises(ValueError, match='sport'):
        validate_targets([{'sport':'nonsport','url':'https://superbet.pl'}])


def test_price_validation():
    assert parse_odd('1,75') == 1.75
    assert parse_odd('2.50') == 2.5
    for val in ['1.0', '', None, -2, float('nan'), '2.5 goles', '1,95/2']:
        assert parse_odd(val) is None


def test_line_validation():
    assert maybe_line('Powyżej 8,5') == 8.5
    assert maybe_line('Handicap -1.5') == -1.5
    assert maybe_line('1. tercja') is None


def test_event_cards():
    r = extract_event_cards(HTML, page_url='https://superbet.pl', sport='football')
    assert r['cards_seen'] == 1
    assert len(r['events']) == 1
    assert r['events'][0]['event_id'] == 'fixture-123'
    assert r['events'][0]['event_url'] == 'https://superbet.pl/mecz/123'


def test_all_markets_no_15_limit_and_actual_prices():
    r = extract_markets(HTML, event_url='https://superbet.pl/mecz/123', sport='football')
    assert r['market_count'] == 4
    assert r['selection_count'] == 7
    assert r['unclassified_market_count'] == 4
    assert r['markets'][0]['selections'][0]['price'] == 1.89
    assert r['markets'][1]['selections'][0]['line'] == 8.5
    assert r['markets'][1]['market_id_is_provider'] is True
    assert r['markets'][2]['market_id'].startswith('local:')
    assert r['markets'][2]['status'] == 'RAW_UNCLASSIFIED'
    assert r['markets'][3]['selections'][0]['label'] == 'Powyżej 0,5'
    assert not r['markets'][2]['selections'][0]['selection_id_is_provider']
    assert 'ev' not in str(r).lower()


def test_40_markets_do_not_get_truncated():
    group = '<div class="market-group"><h4 class="market-name">Rynek {i}</h4>' \
            '<button class="odd-button"><span class="selection-name">Opcja {i}</span>' \
            '<span class="odd-value">2.15</span></button></div>'
    r = extract_markets(''.join(group.format(i=i) for i in range(40)),
                        event_url='https://superbet.pl/mecz/1')
    assert r['market_count'] == 40
    assert r['selection_count'] == 40
    assert not r['truncated']


def test_nested_market_group_does_not_duplicate():
    html = '<div class="market-group"><div class="market-group"><div class="market-name">A</div>' \
           '<button class="odd-button"><span class="odd-value">1.7</span></button></div></div>'
    r = extract_markets(html, event_url='https://superbet.pl/mecz/1')
    assert r['market_count'] == 1


def test_extract_stats_preserved():
    s = extract_extra_stats(HTML)
    assert s['standings'] == ['Club A 1']


def test_dns_fail_is_not_bookmaker_outage():
    def fail(*args, **kw):
        raise socket.gaierror(-2, 'name unresolved')
    d = connection_diagnosis('https://superbet.pl', resolver=fail)
    assert d['status'] == 'DNS_UNAVAILABLE'
    assert d['live_verified'] is False
    assert 'does not prove' in d['note']
    assert classify_read_error(Exception('net::ERR_NAME_NOT_RESOLVED')) == 'DNS_UNAVAILABLE'


def test_dns_good_does_not_prove_live():
    d = connection_diagnosis('https://superbet.pl', resolver=lambda *a,**k: [('dummy',)])
    assert d['status'] == 'DNS_OK'
    assert d['live_verified'] is False


def test_health_no_false_live_claim():
    with TestClient(app) as c:
        r = c.get('/health')
        assert r.status_code == 200
        body = r.json()
        assert body['ev_enabled'] is False
        assert body['automatic_scheduler_enabled'] is False
        assert body['live_market_read_confirmed'] is False
        assert 'season_context' in body


def test_sync_disabled_without_auth(monkeypatch):
    monkeypatch.delenv('READER_SYNC_TOKEN', raising=False)
    monkeypatch.setenv('READER_API_TOKEN', 'X'*64)
    with TestClient(app) as c:
        assert c.post('/sync',headers={'X-Reader-API-Key':'X'*64}).status_code == 403


def test_sync_requires_valid_token(monkeypatch):
    monkeypatch.setenv('READER_SYNC_TOKEN', 'unit-test-secret')
    monkeypatch.setenv('READER_API_TOKEN', 'X'*64)
    with TestClient(app) as c:
        assert c.post('/sync', headers={'X-Reader-API-Key':'X'*64, 'X-Reader-Token': 'wrong'}).status_code == 403
        assert c.post('/sync', headers={'X-Reader-API-Key':'X'*64, 'X-Reader-Token': 'unit-test-secret'}).status_code == 422


def test_html_offline_endpoint(monkeypatch):
    monkeypatch.setenv('READER_API_TOKEN', 'X'*64)
    with TestClient(app) as c:
        r = c.post('/parse-html', headers={'X-Reader-API-Key':'X'*64}, json={'html':HTML, 'event_url':'https://superbet.pl/mecz/123', 'sport':'nhl'})
        assert r.status_code == 200
        d = r.json()
        assert d['market_count'] == 4
        assert d['source'] == 'TEST_OFFLINE'
        assert d['live_market_read_confirmed'] is False


def test_fixture_offline_endpoint_all_markets(monkeypatch):
    monkeypatch.setenv('READER_API_TOKEN', 'X'*64)
    payload = json.loads((ROOT / 'fixtures' / 'sample_all_markets.json').read_text())
    with TestClient(app) as c:
        r = c.post('/parse-snapshot', headers={'X-Reader-API-Key':'X'*64}, json={'sport':'all','payload':payload})
        assert r.status_code == 200, r.text
        assert r.json()['live_market_read_confirmed'] is False
        assert r.json()['ev_enabled'] is False
        assert r.json()['selections_count'] > 0


def test_bad_fixture_sport(monkeypatch):
    monkeypatch.setenv('READER_API_TOKEN', 'X'*64)
    with TestClient(app) as c:
        assert c.post('/parse-snapshot', headers={'X-Reader-API-Key':'X'*64}, json={'sport':'unknown','payload':{'events':[]}}).status_code == 422


def test_cors_not_wildcard():
    with TestClient(app) as c:
        resp = c.get('/health', headers={'Origin':'https://malicious.invalid'})
        assert resp.headers.get('access-control-allow-origin') != '*'


def test_3_season_context_separate():
    assert EUROPE_SEASONS == ('2023/24','2024/25','2025/26')
    assert MLS_SEASONS == ('2023','2024','2025')
    from datetime import datetime, timezone
    c = season_context(datetime(2026, 11, 21, tzinfo=timezone.utc))
    assert '2026/27' in str(c)


def test_weighted_three_season_corner_average():
    rows = [dict(season=y,sport='football',league='Championship',team='Team A',market='corners',
                 venue='all',matches=n,made=m,conceded=30)
            for y,n,m in [('2023/24',10,60),('2024/25',20,80),('2025/26',10,70)]]
    o = summarize_three_seasons(rows)
    assert o[0]['average_for'] == 5.25
    assert o[0]['complete_three_seasons'] is True
