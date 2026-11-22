from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from main import app, validate_targets
from superbet_value_lab import parse_feed, _period, sport_family, SPORTS_SCOPE, season_context, ALLOWED_SPORTS

NOW = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)


def sample(market_name, line, sport, league):
    return parse_feed({'events':[{'id':'synthetic-1','competition':{'name':league},
        'fixture':{'name':'Home vs Away','startTime':'2026-11-24T20:00:00Z'},
        'markets':[{'id':'m-1','name':market_name,'line':line,
                    'odds':[{'id':'o-1','price':1.9,'metadata':{'name':'Over'}},
                            {'id':'o-2','price':1.83,'metadata':{'name':'Under'}}]}]}]},sport,read_at=NOW)


@pytest.mark.parametrize('name,expected',[('1st quarter total points 52.5','q1'),('2. kwarta suma punktow 55.5','q2'),('3rd quarter total points 50.5','q3'),('4. kwarta liczba punktow 49.5','q4'),('2nd half total points 109.5','h2')])
def test_nba_quarters(name,expected):
    assert _period(name)==expected
    r=sample(name, 52.5, 'nba','NBA')
    assert r['events'][0]['selections'][0]['period']==expected
    assert any(z['key'].startswith('points.'+expected+'.total.') for z in r['events'][0]['selections'])


@pytest.mark.parametrize('league', ['nhl','ahl','echl','ohl','whl','qmjhl','ushl'])
def test_hockey_league_period_markets(league):
    assert league in ALLOWED_SPORTS
    assert sport_family(league)=='hockey'
    r=sample('1. tercja suma goli 1.5', 1.5, league, league.upper())
    assert r['events'][0]['competition']['name']==league.upper()
    assert 'goals.p1.total.over.1.5' in {x['key'] for x in r['events'][0]['selections']}
    assert r['events'][0]['sport_family']=='hockey'


def test_player_props_do_not_fake_interpretation():
    r=sample('Player rebounds total 7.5',7.5,'nba','NBA')
    assert all(x['market']=='raw' and x['interpreted'] is False for x in r['events'][0]['selections'])
    assert r['raw_selections_count']==2


def test_scope_endpoint_private_and_explicit(monkeypatch):
    with TestClient(app) as c:
        assert c.get('/scope').status_code==401
        monkeypatch.setenv('READER_API_TOKEN','x'*40)
        result=c.get('/scope',headers={'X-Reader-API-Key':'x'*40})
        assert result.status_code==200
        assert 'QMJHL' in result.json()['sports']['hockey']['leagues']
        assert 'player_rebounds' in result.json()['sports']['nba']['markets']
        assert result.json()['status']=='CONFIGURED_NOT_LIVE_VERIFIED'


def test_seasons_hockey_and_nba():
    r=season_context(NOW)
    assert r['current_nba']=='2026/27'
    assert r['current_hockey']=='2026/27'
    assert r['last_three_completed_nba_hockey']==['2023/24','2024/25','2025/26']


def test_targets_distinct_hockey_leagues():
    t=validate_targets([{'sport':'nhl','url':'https://superbet.pl/nhl'},{'sport':'ahl','url':'https://superbet.pl/ahl'},{'sport':'nba','url':'https://superbet.pl/nba'}])
    assert len(t)==3
