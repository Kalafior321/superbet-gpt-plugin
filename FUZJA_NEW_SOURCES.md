# FUZJA — nowe źródła (read-only)

Dodano adaptery do autoryzowanego mostka FastAPI, bez zmian w produkcyjnym D1/R2 i bez harmonogramu. **Żaden z nich nie dostarcza potwierdzonych cen Superbet PL ani własnego skalibrowanego p_model.**

## Endpointy

Wymagany nagłówek `X-API-Key` jak w istniejącej aplikacji:

- `GET /api/fuzja/sources` — status konfiguracji (nie oznacza udanego połączenia).
- `GET /api/fuzja/sources/reep_manifest` — wyłącznie publiczny manifest wydania. To warstwa tożsamości/deduplikacji, nie feed meczów.
- `GET /api/fuzja/sources/american_soccer_analysis?league=mls&season=2026&dataset=team_xgoals` — dane xG z oficjalnego wrappera `itscalledsoccer` (`pip install itscalledsoccer`), po zatwierdzeniu praw do danych.
- `GET /api/fuzja/sources/strikerapi?league=mls&season=2026&dataset=get_team_stats` — dane per-drużyna, xG; wymaga klucza i zgody na zużycie darmowych kredytów.

## Zabezpieczenia / zmienne środowiskowe

- `FUZJA_ASA_DATA_RIGHTS_CONFIRMED=1` — domyślnie wyłączone; licencja MIT kodu **nie** obejmuje automatycznie danych ASA.
- `STRIKERAPI_KEY` — sekret po stronie serwera; nigdy nie wpisywać do repo.
- `FUZJA_STRIKER_ALLOW_FREE_CREDITS=1` — domyślnie wyłączone.
- `FUZJA_STRIKER_MAX_CREDITS_PER_CALL=2` — opcjonalny limit, domyślnie 5; zapytania xG za 5 kredytów będą przy limicie 2 blokowane.

Dostępne narzędzia StrikerAPI: `get_standings`, `get_schedule` (2 kredyty); `get_team_stats`, `get_odds` (5 kredytów). Na każdy request przypada najwyżej **jedno** połączenie, bez retry. `get_odds` oznacza kursy obcych bukmacherów. Koszty potwierdź w oficjalnej dokumentacji przed włączeniem. SDK ASA może zwrócić dane bez timestampu — `data_as_of=None` zamiast zgadywania.

### Testy

```bash
python -m pip install -r requirements.txt
python -m unittest -v test_api test_fuzja_sources
```

Testy adapterów mają mockowane HTTP/ASA: nie pobierają danych, nie zużywają kredytów, nie modyfikują bazy, nie obstawiają.

Źródła dokumentacji: https://reep.football/data/ · https://github.com/American-Soccer-Analysis/itscalledsoccer · https://strikerapi.com/docs .
