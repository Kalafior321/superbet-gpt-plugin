import os
import secrets
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import FastAPI, Query, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI(
    title="Superbet Scheduled Multi-Market API",
    description="Zewnętrzne API z harmonogramem pobierania (10:00 i 16:00) dla ChatGPT"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
SECRET_API_KEY = os.environ.get("SECRET_API_KEY", "")

def weryfikuj_klucz(x_api_key: Optional[str] = Header(None)):
    if not SECRET_API_KEY or not x_api_key or not secrets.compare_digest(x_api_key, SECRET_API_KEY):
        raise HTTPException(status_code=401, detail="Brak autoryzacji. Nieprawidłowy klucz API.")
    return x_api_key

API_URL = "https://superbet.pl"
SPORTS_CONFIG = {"1": "Pilka Nozna", "2": "Tenis", "3": "Koszykowka", "4": "Hokej na Lodzie"}
STATUS_FILTERS = ["not_started", "live"]
GLOBAL_EVENTS_CACHE: List[Dict] = []
CACHE_METADATA = {"last_update": "Nigdy", "status": "Inicjalizacja", "total_events": 0, "last_attempt": None, "errors": [], "successful_requests": 0}

def wykonaj_kompletny_scraping():
    """Pobiera mecze oraz wszystkie rynki zakładów bezpośrednio z API Superbet."""
    global GLOBAL_EVENTS_CACHE
    nagromadzone_mecze = []
    bledy = []
    udane_zapytania = 0
    timestamp_pobrania = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    CACHE_METADATA["last_attempt"] = timestamp_pobrania
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://superbet.pl",
        "Referer": "https://superbet.pl",
    }
    for sport_id, sport_name in SPORTS_CONFIG.items():
        for status in STATUS_FILTERS:
            params = {"status": status, "sportId": sport_id, "limit": "250"}
            try:
                response = requests.get(API_URL, params=params, headers=headers, timeout=20)
                response.raise_for_status()
                if "json" not in response.headers.get("Content-Type", "").lower():
                    raise ValueError("Źródło zwróciło treść inną niż JSON")
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("Nieprawidłowy format: wymagane data jako lista")
                events = payload["data"]
                udane_zapytania += 1
                for event in events:
                    match_name = event.get("matchName", "Nieznany")
                    league_name = event.get("leagueName", "Brak danych")
                    match_date_raw = event.get("matchDate")
                    if isinstance(match_date_raw, (int, float)):
                        match_date = datetime.fromtimestamp(match_date_raw / 1000).strftime("%Y-%m-%d %H:%M")
                    else:
                        match_date = str(match_date_raw)[:16] if match_date_raw else "Brak daty"
                    wszystkie_rynki = []
                    kursy_glowne = {}
                    for market in event.get("odds", []):
                        market_name = market.get("marketName", "Inny rynek")
                        market_code = market.get("marketCode")
                        opcje_zakladu = []
                        for outcome in market.get("outcomes", []):
                            opcja = {"typ": outcome.get("outcomeName"), "kurs": outcome.get("price")}
                            opcje_zakladu.append(opcja)
                            if market_code in ["match_result", "three_way_conclusion", "winner"]:
                                kursy_glowne[outcome.get("outcomeName")] = outcome.get("price")
                        wszystkie_rynki.append({
                            "nazwa_rynku": market_name, "kod_rynku": market_code, "kursy": opcje_zakladu
                        })
                    nagromadzone_mecze.append({
                        "mecz_id": str(event.get("id", hash(match_name))),
                        "dyscyplina": sport_name,
                        "tryb": "LIVE" if status == "live" else "PREMATCH",
                        "liga": league_name, "mecz": match_name, "data_meczu": match_date,
                        "kursy_glowne_1X2": kursy_glowne, "pelna_oferta_rynkowa": wszystkie_rynki
                    })
            except Exception as exc:
                blad = {"sport_id": sport_id, "tryb": status, "error_type": type(exc).__name__}
                if isinstance(exc, ValueError):
                    blad["message"] = "Nieprawidłowy format odpowiedzi źródła"
                elif isinstance(exc, requests.HTTPError):
                    blad["http_status"] = exc.response.status_code if exc.response is not None else None
                bledy.append(blad)
                print(f"[KOLEKTOR] {blad}", flush=True)
                continue
    CACHE_METADATA["errors"] = bledy
    CACHE_METADATA["successful_requests"] = udane_zapytania
    if bledy:
        CACHE_METADATA["status"] = "Częściowy błąd" if udane_zapytania else "Błąd źródła"
    elif not nagromadzone_mecze:
        CACHE_METADATA["status"] = "Brak wydarzeń"
    if udane_zapytania == len(SPORTS_CONFIG) * len(STATUS_FILTERS) and not nagromadzone_mecze:
        GLOBAL_EVENTS_CACHE = []
        CACHE_METADATA["last_update"] = timestamp_pobrania
        CACHE_METADATA["total_events"] = 0
    if nagromadzone_mecze:
        GLOBAL_EVENTS_CACHE = nagromadzone_mecze
        CACHE_METADATA["last_update"] = timestamp_pobrania
        CACHE_METADATA["status"] = "Częściowy błąd" if bledy else "Aktywny"
        CACHE_METADATA["total_events"] = len(nagromadzone_mecze)
        print(f"[{timestamp_pobrania}] Cache zaktualizowany. Zindeksowano {len(nagromadzone_mecze)} meczów.")

def petla_sztywnych_godzin():
    """Wątek sprawdzający czas systemowy co minutę."""
    print("[START] Uruchamianie wstępnego pobierania danych na starcie serwera...")
    wykonaj_kompletny_scraping()
    while True:
        teraz = datetime.now()
        aktualna_godzina_minuta = teraz.strftime("%H:%M")
        if aktualna_godzina_minuta in ["10:00", "16:00"]:
            print(f"[{teraz.strftime('%Y-%m-%d %H:%M:%S')}] Harmonogram trafiony ({aktualna_godzina_minuta}). Pobieranie...")
            wykonaj_kompletny_scraping()
            time.sleep(61)
        else:
            time.sleep(60)

@app.on_event("startup")
def startup_event():
    thread = threading.Thread(target=petla_sztywnych_godzin, daemon=True)
    thread.start()

@app.get("/api/kursy")
def pobierz_liste_meczow(
    szukaj: Optional[str] = Query(None, description="Filtruj po zespole lub lidze"),
    dyscyplina: Optional[str] = Query(None, description="Pilka Nozna, Tenis, Koszykowka lub Hokej na Lodzie"),
    token: str = Depends(weryfikuj_klucz)
):
    wyniki = GLOBAL_EVENTS_CACHE
    if dyscyplina:
        wyniki = [w for w in wyniki if w["dyscyplina"].lower() == dyscyplina.lower()]
    if szukaj:
        szukaj_lower = szukaj.lower()
        wyniki = [w for w in wyniki if szukaj_lower in w["mecz"].lower() or szukaj_lower in w["liga"].lower()]
    lista_skrocona = []
    for w in wyniki[:40]:
        skrot = w.copy()
        skrot.pop("pelna_oferta_rynkowa")
        lista_skrocona.append(skrot)
    return {"metadane": CACHE_METADATA, "liczba_meczow": len(lista_skrocona), "mecze": lista_skrocona}

@app.get("/api/mecz-detale")
def pobierz_wszystkie_rynki_meczu(mecz_id: str, token: str = Depends(weryfikuj_klucz)):
    for event in GLOBAL_EVENTS_CACHE:
        if event["mecz_id"] == mecz_id:
            return {
                "mecz": event["mecz"], "liga": event["liga"], "data_meczu": event["data_meczu"],
                "wszystkie_rynki_oferty": event["pelna_oferta_rynkowa"]
            }
    raise HTTPException(status_code=404, detail="Nie znaleziono meczu o podanym identyfikatorze ID.")
