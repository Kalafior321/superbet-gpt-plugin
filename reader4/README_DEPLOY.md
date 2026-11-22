# MEGASUPERFUZJA Reader 4.1 staging
Build: `reader4/Dockerfile`, Docker context repository root.
This service is read-only and does not automatically crawl.
Only GET /health is public; all other routes require READER_API_TOKEN (secret length >=32).
POST /sync additionally requires READER_SYNC_TOKEN plus verified SUPERBET_TARGETS_JSON.
No secrets are bundled. Test fixtures do NOT prove live quote coverage.

## NBA i hokej USA/Kanada
- NBA: suma punktów całego meczu, drużyn, kwart 1–4, połów, handicapy i dostępne rynki graczy (punkty, asysty, zbiórki, trójki, kombinacje).
- Hokej: NHL, AHL, ECHL, OHL, WHL, QMJHL, USHL; gole, 1./2./3. tercja, w tym over/under 0,5 oraz 1,5, strzały, interwencje, kary i rynki graczy.
- Rynki zawodników bez jednoznacznej semantyki pozostają RAW_UNCLASSIFIED. Nie przekształcaj ich automatycznie w typ.
- Aktualny zakres: GET `/scope` (tylko z READER_API_TOKEN). Nie są to zweryfikowane oferty bukmachera. Nie ustawiaj sztucznych provider sport IDs ani nie wymyślaj ścieżek Superbet dla lig.
- Testy: `cd reader4 && python -m pytest -q tests/`.
