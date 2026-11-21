# Testing

How to check a change before merging it. Coordination (what to work on) lives on the `coord`
branch; see `AGENTS.md`.

## Unit tests (fast, free)

```sh
cd backend
uv sync --extra dev
uv run playwright install chromium --only-shell
uv run --extra dev python -m pytest -q
uvx ruff check .
```

Tests use fake portals (`httpx.MockTransport`) and make no network or model calls. Add a test for
anything you change in `recipe.py`, `fetch.py` or `documents.py`.

## Real portal runs (costs model money, roughly $0.02–0.20 per attach)

1. Put `OPENAI_API_KEY=...` in `.env` at the repository root. Never paste the key or raw logs that might
   contain it anywhere.
2. Start a throwaway server from `backend/`:
   ```sh
   JIMIX_DB=/tmp/jimix-test/jimix.db JIMIX_FILES=/tmp/jimix-test/files WATCHTOWER_MAX_DETAILS=10 \
     uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 2>&1 | tee /tmp/jimix-test/server.log
   ```
   `WATCHTOWER_MAX_DETAILS=10` limits detail pages and document downloads per read, so big portals
   finish quickly.
3. Attach a portal: `curl -X POST localhost:8765/portals -H 'content-type: application/json' -d '{"url":"https://..."}'`.
4. Watch it: `GET /portals/{id}` (state and reason), `GET /portals/{id}/runs` (model calls, cost),
   `GET /portals/{id}/tenders`. The log shows every agent step: `grep "watchtower.agent tool" server.log`.
   The active recipe is in SQLite: `select data from recipe where active`.
5. `POST /portals/{id}/refresh` reads again with the stored recipe and no model call.
