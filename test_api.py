import os
import unittest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
os.environ["SECRET_API_KEY"] = "test-only-key"
import main

HEADERS = {"X-API-Key": "test-only-key"}


class ApiTests(unittest.TestCase):
    def setUp(self):
        main.GLOBAL_EVENTS_CACHE.clear()
        main.CACHE_METADATA.update(status="BRAK_AUTORYZOWANEGO_ZRODLA", total_events=0, automatic_polling_active=False)
        self.client = TestClient(main.app)

    def sample(self, **changes):
        event = {"mecz_id": "id42", "mecz": "Alpha - Beta", "liga": "Liga Test",
                 "dyscyplina": "Pilka Nozna", "data_meczu": "2026-10-20T18:00:00Z",
                 "kursy_glowne_1X2": {}, "pelna_oferta_rynkowa": []}
        event.update(changes)
        return event

    def payload(self, kind="footballdata.io", events=None):
        return {"source_kind": kind, "observed_at": datetime.now(timezone.utc).isoformat(),
                "mecze": events if events is not None else [self.sample()]}

    def test_auth_is_required_for_reads_and_writes(self):
        for verb, url in [("get", "/api/kursy"), ("get", "/api/mecz-detale?mecz_id=id42"),
                          ("post", "/api/import-snapshot")]:
            self.assertEqual(getattr(self.client, verb)(url).status_code, 401)
            self.assertEqual(getattr(self.client, verb)(url, headers={"X-API-Key": "bad"}).status_code, 401)

    def test_no_scraper_and_truthful_status(self):
        with TestClient(main.app) as client:
            result = client.get("/api/kursy", headers=HEADERS).json()
            self.assertEqual(result["liczba_meczow"], 0)
            self.assertEqual(result["metadane"]["status"], "BRAK_AUTORYZOWANEGO_ZRODLA")
            self.assertFalse(result["metadane"]["automatic_polling_active"])
            self.assertEqual(client.get("/healthz").json()["service"], "ok")

    def test_import_fixture_and_search_without_invented_prices(self):
        response = self.client.post("/api/import-snapshot", headers=HEADERS, json=self.payload())
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/api/kursy", headers=HEADERS, params={"szukaj": "Liga"}).json()
        self.assertEqual(response["liczba_meczow"], 1)
        self.assertEqual(response["mecze"][0]["kursy_glowne_1X2"], {})
        self.assertFalse(response["mecze"][0]["superbet_prices_verified"])
        self.assertNotIn("pelna_oferta_rynkowa", response["mecze"][0])
        details = self.client.get("/api/mecz-detale", headers=HEADERS, params={"mecz_id": "id42"}).json()
        self.assertEqual(details["wszystkie_rynki_oferty"], [])
        self.assertEqual(self.client.get("/api/mecz-detale?mecz_id=absent", headers=HEADERS).status_code, 404)

    def test_reject_third_party_prices_and_preserve_old_snapshot(self):
        self.client.post("/api/import-snapshot", headers=HEADERS, json=self.payload())
        old = list(main.GLOBAL_EVENTS_CACHE)
        unsafe = self.payload(events=[self.sample(kursy_glowne_1X2={"1": 1.9})])
        self.assertEqual(self.client.post("/api/import-snapshot", headers=HEADERS, json=unsafe).status_code, 422)
        self.assertEqual(main.GLOBAL_EVENTS_CACHE, old)

    def test_reject_missing_identity_duplicates_and_naive_timestamp(self):
        for payload in [self.payload(events=[self.sample(mecz_id="")]),
                        self.payload(events=[self.sample(), self.sample()]),
                        {**self.payload(), "observed_at": "2026-10-19T10:00:00"}]:
            self.assertEqual(self.client.post("/api/import-snapshot", headers=HEADERS, json=payload).status_code, 422)

    def test_manual_export_marked_unverified(self):
        selection = {"nazwa_rynku": "1X2", "kursy": [{"typ": "1", "kurs": 2.0}]}
        data = self.payload(kind="manual_superbet_export", events=[self.sample(
            kursy_glowne_1X2={"1": 2.0}, pelna_oferta_rynkowa=[selection])])
        self.assertEqual(self.client.post("/api/import-snapshot", headers=HEADERS, json=data).status_code, 200)
        self.assertFalse(main.GLOBAL_EVENTS_CACHE[0]["superbet_prices_verified"])


if __name__ == "__main__":
    unittest.main()
