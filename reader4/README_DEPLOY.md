# MEGASUPERFUZJA Reader 4.1 staging
Build: `reader4/Dockerfile`, Docker context repository root.
This service is read-only and does not automatically crawl.
Only GET /health is public; all other routes require READER_API_TOKEN (secret length >=32).
POST /sync additionally requires READER_SYNC_TOKEN plus verified SUPERBET_TARGETS_JSON.
No secrets are bundled. Test fixtures do NOT prove live quote coverage.
