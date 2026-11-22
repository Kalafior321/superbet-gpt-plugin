from datetime import datetime, timezone
import socket
from unittest.mock import Mock

from superbet_value_lab import season_context, parse_feed
from feed_diagnostics import run_diagnostic


def test_current_season_vs_completed_three():
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    state = season_context(now)
    assert state["current_european"] == "2026/27"
    assert state["last_three_completed_european"] == ["2023/24", "2024/25", "2025/26"]
    assert state["current_mls"] == "2026"
    assert state["last_three_completed_mls"] == ["2023", "2024", "2025"]
    assert state["inferred_calendar_context_only"] is True


def test_season_before_july_rollover():
    state = season_context(datetime(2027, 4, 1, tzinfo=timezone.utc))
    assert state["current_european"] == "2026/27"
    assert state["current_mls"] == "2027"


def test_parse_reports_calendar_context_not_official_event_season():
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    feed = parse_feed({"events": []}, "all", read_at=now)
    assert feed["season_context"]["current_european"] == "2026/27"
    assert "season" not in feed.get("events", [{}])[0] if feed["events"] else True


def test_dns_failure_not_superbet_outage():
    def fail(*a, **k):
        raise socket.gaierror(-3, "Temporary failure")
    check = run_diagnostic(resolver=fail)
    assert check["status"] == "DNS_UNAVAILABLE"
    assert check["live_verified"] is False


def test_dns_success_is_not_alone_feed_confirmation():
    def success(*a, **k):
        return [(None, None, None, None, ("203.0.113.3", 443))]
    def fetch_fail(*, sport):
        raise RuntimeError("HTTP 403")
    check = run_diagnostic(resolver=success, fetcher=fetch_fail)
    assert check["status"] == "FEED_UNVERIFIED"
    assert check["live_verified"] is False


def test_http_success_of_synthetic_feed_labeled_not_interface_validation():
    def success(*a, **k):
        return [(None, None, None, None, ("203.0.113.3", 443))]
    def synthetic(*, sport):
        return {"live_fetch_confirmed": True, "event_count": 2, "selections_count": 10}
    check = run_diagnostic(resolver=success, fetcher=synthetic)
    assert check["status"] == "HTTP_JSON_READ"
    assert check["live_verified"] is True
    assert "does not verify every offer" in check["note"]
