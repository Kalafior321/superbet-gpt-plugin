from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from superbet_value_lab import (
    _dates, _period, _price, fetch_feed, parse_feed, sport_ids_config,
    rank_by_probability,
)

NOW = datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)


def event(name="Atlanta Hawks vs Charlotte Hornets", markets=None, start="2026-11-21T01:00:00Z"):
    return {"id": 88, "fixture": {"event_name": name, "startTime": start}, "markets": markets or []}


def market(name, over, under, line=None, **kw):
    m = {"name": name, "odds": [
        {"metadata": {"name": "Powyżej"}, "price": over},
        {"metadata": {"name": "Poniżej"}, "price": under}
    ], **kw}
    if line is not None:
        m["line"] = line
    return m


def parse(events, sport="nba"):
    return parse_feed({"events": events}, sport, read_at=NOW)


def keys(result):
    return {x["key"]: x for x in result["events"][0]["selections"]}


def test_price_validation():
    assert _price({"price": "1,75"}) == 1.75
    assert _price({"price": 1}) is None
    assert _price({"price": "NaN"}) is None
    assert _price({"price": True}) is None
    assert _price({"price": -3}) is None


def test_empty_feed():
    assert parse([])["event_count"] == 0


def test_unknown_schema_raises():
    with pytest.raises(ValueError, match="events"):
        parse_feed({"data": {}}, "nba")


def test_nba_total_and_team_points():
    e = event(markets=[
        market("Suma punktów w meczu 228,5", "1.84", "1.96"),
        market("Liczba punktów Atlanta Hawks", 1.8, 1.9, line=115.5),
        market("Team total points Charlotte Hornets 112.5", 1.7, 2.0),
    ])
    result = keys(parse([e]))
    assert result["points.full.total.over.228.5"]["price"] == 1.84
    assert result["points.full.total.under.228.5"]["price"] == 1.96
    assert result["points.full.team_home.over.115.5"]["price"] == 1.8
    assert result["points.full.team_away.under.112.5"]["price"] == 2.0


def test_nhl_first_period_and_team_goals():
    e = event(name="Rochester Americans vs Hershey Bears", markets=[
        market("Liczba goli w meczu 5,5", 1.9, 1.9),
        market("1. tercja suma goli 0.5", 1.24, 4.0),
        market("Gole drużyny Rochester Americans", 1.7, 2.1, line="2.5"),
        market("First period total goals 1.5", 1.9, 1.8),
    ])
    result = keys(parse([e], "ahl"))
    assert "goals.full.total.over.5.5" in result
    assert "goals.p1.total.over.0.5" in result
    assert "goals.p1.total.under.1.5" in result
    assert "goals.full.team_home.over.2.5" in result


def test_nhl_1st_period_total_not_team_one():
    result = keys(parse([event(markets=[market("1. tercja - suma goli 1,5", 2.1, 1.6)])], "nhl"))
    assert all("team_home" not in key for key in result)


def test_football_1x2_regression():
    e = event("Arsenal vs Liverpool", [{"name":"1x2", "odds": [
        {"price":1.8, "metadata":{"name":"1"}},
        {"price":3.1, "metadata":{"name":"X"}},
        {"price":4.2, "metadata":{"name":"2"}},
    ]}, market("Gole 2.5", 1.9, 1.85)])
    result = keys(parse([e], "football"))
    assert result["1x2.full.1"]["price"] == 1.8
    assert result["1x2.full.X"]["price"] == 3.1
    assert result["1x2.full.2"]["price"] == 4.2
    assert result["goals.full.total.over.2.5"]["price"] == 1.9


def test_filter_explicitly_inactive():
    e = event(markets=[market("Total points 218.5", 1.8, 1.9, active=False),
                       {"name":"Total points 220.5", "odds":[
                           {"price":1.8,"metadata":{"name":"Over"},"suspended":True},
                           {"price":1.9,"metadata":{"name":"Under"}}]}])
    result=keys(parse([e]))
    assert "points.full.total.over.218.5" not in result
    assert "points.full.total.over.220.5" not in result
    assert "points.full.total.under.220.5" in result


def test_player_props_preserved_as_raw():
    e = event(markets=[market("Player total points 18.5", 1.8, 1.9),
                       market("Asysty zawodnika 5.5", 1.7, 1.9)])
    r = parse([e])
    assert r["selections_count"] == 4
    assert r["raw_selections_count"] == 4
    assert {x["category"] for x in r["events"][0]["selections"]} == {"player_props"}
    assert all(x["market"] == "raw" for x in r["events"][0]["selections"])


def test_conflicting_duplicates_excluded():
    e = event(markets=[market("Suma punktów 228.5", 1.8, 1.9),
                       market("Suma punktów 228.5", 1.88, 1.9)])
    result = parse([e])
    assert "points.full.total.over.228.5" not in keys(result)
    assert "points.full.total.under.228.5" in keys(result)
    assert len(result["warnings"]) == 1


def test_expired_event_excluded():
    e = event(markets=[market("Suma punktów 228.5", 1.8, 1.9)],start="2026-11-20T19:00:00Z")
    assert parse([e])["event_count"] == 0


def test_ambiguous_team_line_kept_raw_not_misclassified():
    e = event(markets=[market("Team 7 total points 117.5", 1.8, 1.9)])
    items = parse([e])["events"][0]["selections"]
    assert len(items) == 2
    assert all(x["market"] == "raw" and x["key"].startswith("raw.") for x in items)


def test_quote_time_not_verified():
    result = parse([event(markets=[market("Total points 200.5",1.8,1.8)])])
    assert result["quote_time_verified"] is False
    assert result["events"][0]["quote_time_verified"] is False


def test_fetch_requires_nba_sport_id(monkeypatch):
    monkeypatch.delenv("SUPERBET_SPORT_IDS_JSON", raising=False)
    with pytest.raises(ValueError,match="sport ID"):
        fetch_feed(sport="nba")


def test_configured_sport_ids(monkeypatch):
    monkeypatch.setenv("SUPERBET_SPORT_IDS_JSON", '{"nba":3,"nhl":4,"ahl":6}')
    assert sport_ids_config()["nba"] == 3
    assert sport_ids_config()["football"] == 5


def test_http_mock_success(monkeypatch):
    monkeypatch.setenv("SUPERBET_SPORT_IDS_JSON", '{"nba":3}')
    response = Mock(status_code=200, headers={"Content-Type":"application/json"})
    response.json.return_value = {"events":[event(markets=[market("Total points 228.5",1.9,1.8)],
        start=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())]}
    session = Mock()
    session.get.return_value = response
    result = fetch_feed(sport="nba",session=session)
    assert result["live_fetch_confirmed"] is True
    assert result["selections_count"] == 2
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["params"]["sports"] == "3"


def test_http_403_respected(monkeypatch):
    monkeypatch.setenv("SUPERBET_SPORT_IDS_JSON", '{"nba":3}')
    session=Mock()
    session.get.return_value=Mock(status_code=403)
    with pytest.raises(RuntimeError, match="403"):
        fetch_feed(sport="nba",session=session)
    assert session.get.call_count == 1


def test_no_made_up_probability_no_ev():
    sels = [{"key":"points.full.total.under.228.5", "price": 2.0, "category": "points"}]
    assert rank_by_probability(sels,{}) == []
    assert rank_by_probability(sels,{"points.full.total.under.228.5":0.60}) == []
    found = rank_by_probability(sels,{"points.full.total.under.228.5":0.85})
    assert found[0]["probability_pct"] == 85.0
    assert not any("ev" in k.lower() for k in found[0])


def test_warsaw_date_window():
    a,b=_dates(2)
    assert a.endswith("Z") and b.endswith("Z")
    assert datetime.fromisoformat(a.replace("Z","+00:00")) < datetime.fromisoformat(b.replace("Z","+00:00"))


def test_line_only_from_price_never():
    e=event(markets=[market("Total points",1.8,1.9)])
    items = parse([e])["events"][0]["selections"]
    assert len(items) == 2
    assert all(x["market"] == "raw" and x["line"] is None for x in items)


def test_all_mode_explicit_points_and_goals():
    p = parse_feed({"events": [event(markets=[market("Total points 220.5",1.8,1.9),
                                               market("Total goals 5.5",1.8,1.9)])]},
                   "all", read_at=NOW)
    found = {x["key"] for x in p["events"][0]["selections"]}
    assert "points.full.total.over.220.5" in found
    assert "goals.full.total.over.5.5" in found


def test_missing_start_time_rejected_live_mode():
    e=event(markets=[market("Total points 220.5",1.8,1.9)])
    e["fixture"].pop("startTime")
    assert parse([e])["event_count"] == 0


def test_metadata_wrong_type_does_not_crash():
    e=event(markets=[{"name":"Total points 220.5","odds":[{"price":1.8,"name":"Over","metadata":[]}]}])
    assert parse([e])["selections_count"] == 1
