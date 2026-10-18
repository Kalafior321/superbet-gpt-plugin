import os
import unittest
from unittest.mock import patch, Mock
os.environ["SECRET_API_KEY"] = "test-only-key"
from fastapi.testclient import TestClient
import main

class ApiTests(unittest.TestCase):
    def setUp(self):
        main.GLOBAL_EVENTS_CACHE = []
        self.client = TestClient(main.app)

    def test_authentication(self):
        for path in ["/api/kursy", "/api/mecz-detale?mecz_id=none"]:
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={"X-API-Key": "bad"}).status_code, 401)
        self.assertEqual(self.client.get("/api/kursy", headers={"X-API-Key": "test-only-key"}).status_code, 200)

    def test_html_failure_is_visible_and_cache_survives(self):
        main.GLOBAL_EVENTS_CACHE = [{"mecz_id": "old"}]
        response = Mock(headers={"Content-Type": "text/html"})
        with patch.object(main.requests, "get", return_value=response):
            main.wykonaj_kompletny_scraping()
        self.assertEqual(main.CACHE_METADATA["status"], "Błąd źródła")
        self.assertEqual(len(main.CACHE_METADATA["errors"]), 8)
        self.assertEqual(main.GLOBAL_EVENTS_CACHE[0]["mecz_id"], "old")

    def test_full_market_mapping_and_search(self):
        response = Mock(headers={"Content-Type": "application/json"})
        response.json.return_value = {"data": [{"id": 42, "matchName": "Alpha - Beta", "leagueName": "Liga Test", "odds": [{"marketName": "BTTS", "marketCode": "btts", "outcomes": [{"outcomeName": "Tak", "price": 1.8}]}]}]}
        with patch.object(main.requests, "get", return_value=response):
            main.wykonaj_kompletny_scraping()
        headers = {"X-API-Key": "test-only-key"}
        listing = self.client.get("/api/kursy", params={"szukaj": "Liga Test"}, headers=headers).json()
        self.assertGreater(listing["liczba_meczow"], 0)
        self.assertNotIn("pelna_oferta_rynkowa", listing["mecze"][0])
        details = self.client.get("/api/mecz-detale", params={"mecz_id": "42"}, headers=headers).json()
        self.assertEqual(details["wszystkie_rynki_oferty"][0]["kursy"][0]["kurs"], 1.8)

if __name__ == "__main__":
    unittest.main()
