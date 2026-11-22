# MEGASUPERFUZJA 1.1: integration prepared

Central read-only gateway: https://megasuperfuzja-api.max-gollan.workers.dev

- D1: `fuzja-core` (production id `72b81b7d-e92e-4050-b484-57ccc5cfa8d5`).
- `.fuzja/megasuperfuzja_client.py` provides four strictly allowlisted GET methods. It never creates bets or alters databases.
- Server-only variables: `MEGAFUZJA_API_URL` (optional: verified origin above) and `MEGAFUZJA_READ_TOKEN` (required to access private data). Store tokens as host secrets; do not commit values.
- Reader 4.0 artifact: `Superbet_FUZJA_Czytnik_4_0_Context7_ALL_MARKETS.zip`, reviewed in ChatGPT. Its parser passed offline tests; live feed DNS validation remains incomplete. Do not claim live Superbet access.
- Includes football leagues 1–3, all actually offered cups (UEFA Champions League, Europa League, Conference League, domestic cups), MLS, NBA, NHL. All markets in read feed. No EV.
- The API is deliberately read-only, so this is a preparatory client integration, not an active live synchronization. No new scheduling or migration of source-of-truth tables.
- Before merging: run tests, provision the server-only secret, smoke test authenticated /v1/overview and /v1/catalog, verify Render deployment and rollback; leave D1 history untouched.

Security: app-level SELECT-only API does not reduce the Cloudflare Worker binding's underlying D1 privileges. Keep this Worker source reviewed.
