from datetime import datetime, timezone
import pytest

from superbet_value_lab import parse_feed, rank_by_probability
from market_history import summarize_three_seasons, EUROPE_SEASONS, MLS_SEASONS

NOW = datetime(2026, 11, 20, 20, tzinfo=timezone.utc)


def make(name, selections):
    return {"id": len(name), "name": name, "odds": [
        {"id": i + 1, "metadata": {"name": outcome}, "price": price}
        for i, (outcome, price) in enumerate(selections)]}


def test_all_market_categories_and_unknown_kept():
    markets = [
        make("Liczba rzutów rożnych 9.5", [("Powyżej", 1.9), ("Poniżej", 1.9)]),
        make("Yellow cards 4.5", [("Over", 1.8), ("Under", 2.0)]),
        make("Shots on target", [("6+", 1.7)]),
        make("Player rebounds 8.5", [("Over", 1.7)]),
        make("Asysty zawodnika 5.5", [("Powyżej", 1.8)]),
        make("HT/FT", [("1/X", 13.5)]),
        make("Space Wizard Special", [("Tak", 1.7)]),
        make("BTTS", [("Tak", 1.8), ("Nie", 2.2)]),
    ]
    ev = {"fixture": {"name": "Drużyna A vs Drużyna B", "startTime": "2026-11-22T19:00:00Z"},
          "competition": {"name": "Major League Soccer", "country": "USA", "tier": 1},
          "markets": markets}
    r = parse_feed({"events": [ev]}, "all", read_at=NOW)
    assert r["selections_count"] == 11
    assert r["raw_selections_count"] == 11
    assert r["events"][0]["competition"] == {"name": "Major League Soccer", "country": "USA", "tier_from_provider": 1, "is_mls": True}
    assert {"corners", "cards", "shots", "player_props", "btts", "other"}.issubset(set(r["categories"]))
    assert all(k["outcome_id"] for k in r["events"][0]["selections"])


def test_all_markets_stable_ids_and_no_hidden_ev():
    m = make("Player total points 24.5", [("Over", 1.95), ("Under", 1.86)])
    e = {"fixture": {"name": "Home vs Away", "startTime": "2026-11-22T19:00:00Z"}, "markets": [m]}
    a = parse_feed({"events": [e]}, "nba", read_at=NOW)
    b = parse_feed({"events": [e]}, "nba", read_at=NOW)
    keys_a = [x["key"] for x in a["events"][0]["selections"]]
    keys_b = [x["key"] for x in b["events"][0]["selections"]]
    assert keys_a == keys_b
    assert rank_by_probability(a["events"][0]["selections"], {}) == []
    assert not any("ev" in k.lower() for k in a["events"][0]["selections"][0])


def test_historical_weighted_and_coverage():
    rows = [dict(season=s, sport="football", league="Championship", team="Team X", market="corners", venue="all", matches=n, made=m, conceded=cm)
            for s, n, m, cm in zip(EUROPE_SEASONS, [20, 40, 30], [120, 160, 180], [80, 200, 90])]
    result = summarize_three_seasons(rows)
    assert len(result) == 1
    assert result[0]["complete_three_seasons"]
    assert result[0]["average_for"] == 5.1111
    assert result[0]["average_against"] == 4.1111
    assert result[0]["matches"] == 90


def test_historical_mls_and_incomplete():
    rows = [dict(season="2023", sport="football", league="MLS", team="Team A", market="shots", venue="home", matches=10, made=100, conceded=70)]
    result = summarize_three_seasons(rows, seasons=MLS_SEASONS)
    assert result[0]["average_for"] == 10
    assert not result[0]["complete_three_seasons"]


def test_historical_rejects_invalid_data():
    row = dict(season="2024/25", sport="football", league="Serie B", team="Test", market="corners", venue="all", matches=0, made=10, conceded=10)
    with pytest.raises(ValueError, match="matches"):
        summarize_three_seasons([row])
