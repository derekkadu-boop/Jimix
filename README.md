# Jimix

## Run locally

Start the FastAPI backend from `backend/`.

```sh
cd backend
python -m pip install -e ".[dev]"
python -m playwright install chromium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start Next.js in a second terminal with Node.js 20.9 or later.

```sh
cd frontend
npm ci
npm run dev
```

Open [the dashboard](http://127.0.0.1:3000/). Next.js forwards portal requests
to FastAPI on port 8000. Set `JIMIX_API_URL` before starting development
or building for production to use another backend origin.

## How Watchtower works

`POST /portals` stores the portal and starts a read in the background.

1. **Attach.** With no recipe yet, an OpenAI agent (`app/agent.py`) explores the portal with a few tools
   (`fetch_page`, `render_page`, `fetch_json`, `scan_scripts`, `test_recipe`, `submit_recipe`, `give_up`),
   after an automatic check for API documentation and API paths in the site's scripts. It prefers a
   documented public API, then a JSON API the site calls, then server HTML, then browser rendering.
   It produces a declarative JSON recipe (`app/recipe.py`): URLs, CSS selectors or JSON paths, and
   field mappings. The model never writes code. A recipe is accepted only when it reads real tenders.
2. **Read.** Python applies the active recipe without a model call, normalizes tenders to the spec fields,
   downloads documents (unpacking ZIPs, skipping files over 250 MB) into `data/files/<sha256>`, and marks
   tenders that disappeared as closed.
3. **Notice.** Each read is checked: no tenders, most tenders missing a title or URL, or a sharp drop in
   count means the portal is broken. Logins, captchas and robots.txt refusals mark it `failed`.
4. **Repair.** A broken read sets the portal to `healing`, keeps its last tenders, and reruns the agent with
   the old recipe and the failure. A working new recipe becomes active; old recipes stay in the `recipe`
   table. If repair fails the portal is `failed` and `reason` says why.

Reads happen on attach and on `POST /portals/{id}/refresh`. Requests are paced to one per second per site
(including browser requests), respect robots.txt, and only go to public addresses.

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | | Required for attaching and repairing portals |
| `WATCHTOWER_MODEL` | `gpt-5.4-mini` | Model used by the agent |
| `OPENAI_BASE_URL` | OpenAI | Any OpenAI-compatible Chat Completions endpoint |
| `WATCHTOWER_MAX_TURNS` | `40` | Model calls allowed per attach or repair |
| `WATCHTOWER_MAX_COST_USD` | `1.0` | Model cost limit per attach or repair |
| `WATCHTOWER_INPUT_USD_PER_MILLION` / `..._OUTPUT_...` / `..._CACHED_INPUT_...` | `0.75` / `4.50` / `0.075` | Prices for the cost estimate |
| `WATCHTOWER_RUN_TIMEOUT` | `3600` | Seconds before a run is stopped |
| `WATCHTOWER_RENDER_TIMEOUT` | `240` | Seconds for one browser render |
| `WATCHTOWER_BROWSER_WS` | unset | Connect to a remote Playwright server instead of launching Chromium |
| `WATCHTOWER_MAX_RUN_DOWNLOAD_MB` | `1000` | Document bytes per run; the rest continues on the next refresh |
| `WATCHTOWER_MAX_FILE_MB` | `250` | Larger files are skipped |
| `WATCHTOWER_MAX_DOCS_PER_TENDER` | `30` | Document links per tender |
| `WATCHTOWER_MAX_DETAILS` | unset (all) | For testing: detail pages and document downloads per read |
| `WATCHTOWER_USER_AGENT` | `JimixWatchtower/0.1 ...` | User agent sent to portals |
| `JIMIX_DB` | `data/jimix.db` | SQLite database |
| `JIMIX_FILES` | `data/files` | Downloaded documents |

Each run records its model calls, tokens and estimated cost (`GET /portals/{id}/runs`).
Regular reads make no model call.

### Tests

```sh
cd backend
python -m pytest -q
```
