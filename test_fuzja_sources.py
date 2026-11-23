"""Offline regression checks for opt-in read-only source adapters."""
import os
import unittest
from unittest.mock import patch

os.environ["SECRET_API_KEY"] = "test-only-key"

from fastapi.testclient import TestClient

import main
from fuzja_extra_sources import SourceError, fetch, status


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return Response(self.data)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return Response(self.data)


class Frame:
    def to_dict(self, orient):
        assert orient == "records"
        return [{"season_name": "2026", "team_name": "A", "xg": 1.2},
                {"season_name": "2025", "team_name": "B", "xg": 1.1}]


class FakeASA:
    def get_team_xgoals(self, **kwargs):
        return Frame()


class FuzjaNewSourcesTests(unittest.TestCase):
    def test_status_never_claims_connection_verified(self):
        self.assertEqual(len(status()), 3)
        self.assertTrue(all(not x["connection_verified"] for x in status()))

    def test_protected_api(self):
        client = TestClient(main.app)
        self.assertEqual(client.get("/api/fuzja/sources").status_code, 401)
        self.assertEqual(client.get("/api/fuzja/sources/reep_manifest").status_code, 401)
        resp = client.get("/api/fuzja/sources", headers={"X-API-Key": "test-only-key"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["automatic_polling_active"])

    def test_reep_manifest_only(self):
        s = Session({"release_stamp": "2026-10-19.1"})
        result = fetch("reep_manifest", session=s)
        self.assertEqual(s.calls[0][0], "GET")
        self.assertEqual(result["kind"], "identity_release")
        self.assertFalse(result["contains_match_statistics"])
        self.assertFalse(result["superbet_prices_verified"])

    def test_asa_deny_by_default(self):
        with patch.dict(os.environ, {"FUZJA_ASA_DATA_RIGHTS_CONFIRMED": ""}):
            with self.assertRaises(SourceError):
                fetch("american_soccer_analysis", asa_client=FakeASA())

    def test_asa_gated_xg(self):
        with patch.dict(os.environ, {"FUZJA_ASA_DATA_RIGHTS_CONFIRMED": "1"}):
            result = fetch("american_soccer_analysis", dataset="team_xgoals",
                           season=2026, asa_client=FakeASA())
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["kind"], "team_xgoals")
        self.assertFalse(result["superbet_prices_verified"])

    def test_striker_disabled_with_key(self):
        s = Session({"data": {"standings": []}})
        with patch.dict(os.environ, {"STRIKERAPI_KEY": "secret",
                                     "FUZJA_STRIKER_ALLOW_FREE_CREDITS": ""}):
            with self.assertRaises(SourceError):
                fetch("strikerapi", dataset="get_standings", session=s)
        self.assertEqual(s.calls, [])

    def test_striker_approved_has_provenance(self):
        s = Session({"data": {"standings": []}})
        with patch.dict(os.environ, {"STRIKERAPI_KEY": "secret",
                                     "FUZJA_STRIKER_ALLOW_FREE_CREDITS": "1"}):
            result = fetch("strikerapi", league="soccer", dataset="get_standings",
                           session=s)
        self.assertEqual(s.calls[0][0], "POST")
        self.assertEqual(s.calls[0][2]["json"], {"season": 2026, "league": "soccer"})
        self.assertEqual(result["credit_cost"], 2)
        self.assertFalse(result["superbet_prices_verified"])

    def test_striker_max_cost_blocks_network(self):
        s = Session({"data": {}})
        with patch.dict(os.environ, {"STRIKERAPI_KEY": "secret",
                                     "FUZJA_STRIKER_ALLOW_FREE_CREDITS": "1",
                                     "FUZJA_STRIKER_MAX_CREDITS_PER_CALL": "2"}):
            with self.assertRaises(SourceError):
                fetch("strikerapi", dataset="get_team_stats", session=s)
        self.assertEqual(s.calls, [])

    def test_never_claims_superbet_for_other_bookies(self):
        s = Session({"data": {"odds": [{"bookmaker": "example"}]}})
        with patch.dict(os.environ, {"STRIKERAPI_KEY": "secret",
                                     "FUZJA_STRIKER_ALLOW_FREE_CREDITS": "1",
                                     "FUZJA_STRIKER_MAX_CREDITS_PER_CALL": "5"}):
            result = fetch("strikerapi", dataset="get_odds", session=s)
        self.assertEqual(result["bookmaker"], "third_party_not_superbet")
        self.assertFalse(result["superbet_prices_verified"])


if __name__ == "__main__":
    unittest.main()
