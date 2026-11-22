"""Read-only diagnostics: local DNS reachability != bookmaker feed availability."""
from __future__ import annotations

import argparse
import json
import socket
from urllib.parse import urlparse
from superbet_value_lab import API_URL, EXPECTED_HOST, fetch_feed


def run_diagnostic(*, sport: str = "all", resolver=None, fetcher=None) -> dict:
    host = urlparse(API_URL).hostname
    if host != EXPECTED_HOST:
        return {"status": "INVALID_CONFIG", "live_verified": False, "host": host}
    resolver = resolver or socket.getaddrinfo
    try:
        addresses = resolver(host, 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise OSError("empty DNS reply")
    except OSError as exc:
        return {"status": "DNS_UNAVAILABLE", "live_verified": False,
                "host": host, "environment_error_type": type(exc).__name__,
                "note": "DNS failure in this runtime does not prove an outage at Superbet"}
    fetcher = fetcher or fetch_feed
    try:
        result = fetcher(sport=sport)
    except Exception as exc:
        return {"status": "FEED_UNVERIFIED", "live_verified": False,
                "host": host, "error_type": type(exc).__name__,
                "note": "Local lookup succeeded, but offers were not verified"}
    return {"status": "HTTP_JSON_READ", "live_verified": bool(result.get("live_fetch_confirmed")),
            "host": host, "event_count": result.get("event_count"),
            "selections_count": result.get("selections_count"),
            "note": "HTTP read does not verify every offer against the bookmaker interface"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", default="all")
    args = parser.parse_args()
    print(json.dumps(run_diagnostic(sport=args.sport), ensure_ascii=False, indent=2))
