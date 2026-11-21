# Jimix Watchtower

Jimix monitors public procurement portals. Add a portal URL from the dashboard to collect its open
tenders and documents. Select the portal and click **Refresh now** to run another read and see the
tenders, portal state, and run history.

## Run with Docker

From the repository root, build the Linux AMD64 image and start it with your model API key exported
in the shell.

```sh
docker build --platform linux/amd64 -t jimix .
docker run --rm -p 127.0.0.1:8080:8080 -e OPENAI_API_KEY -v jimix-data:/data jimix
```

The dashboard and API are both available at `http://127.0.0.1:8080`. Use `/health` to check the API
and `/openapi.json` for its schema. The `jimix-data` volume keeps the SQLite database and downloaded
files across container restarts.

## How it works

1. **Attach.** `POST /portals` stores the URL and returns the portal ID while the first read runs in
   the background. An OpenAI-compatible model uses fetched pages, public APIs and browser traffic
   to propose a declarative JSON recipe. Jimix tests the recipe against the portal before saving it.
2. **Read.** `POST /portals/{id}/refresh` reapplies the saved recipe without calling the model.
   Jimix normalizes the tender fields, closes tenders that disappear from a complete listing, and
   tracks when each record changed so clients can query `/portals/{id}/tenders?changedSince=…`.
3. **Download.** Jimix follows document links, unpacks ZIP archives, and serves the resulting files
   by SHA-256 at `/files/{sha256}`. Files over 250 MB are skipped by default. Portal requests go only
   to public addresses, respect robots.txt, and are paced at one request per second per site.
4. **Notice and repair.** If a read detects broken extraction, the portal enters `healing` and
   continues serving its last correct tenders while the model uses the failure and old recipe to
   produce a replacement. An unsuccessful repair or a confirmed access block sets the portal to
   `failed` with a reason. The dashboard shows the state, tenders, and cost for each run.

The dashboard also looks up approximate portal registration locations through RDAP or WHOIS and
uses [GeoNames](https://www.geonames.org/) coordinates (CC BY 4.0).

## Model cost per portal

On November 21, 2026, two successful fresh TenderNed attaches used 13–15 model calls and cost
**USD 0.0524–0.0674 per attach**. Both follow-up refreshes reused the saved recipe and made zero
model calls (**USD 0**). The failed attach attempts cost USD 0.0733 for the
Danish Dev portal and USD 0.1324 for the Finnish Dev portal.

These are measured estimates from individual attempts with a 10-detail sample. Model usage varies
by portal and each repair can incur additional cost. Hosting is separate. View the estimates and
model call counts in the dashboard or at `GET /portals/{id}/runs`.

## Environment variables

Set backend options before starting the process. For Docker, pass additional settings with
`-e NAME` or `--env-file`.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Required for model calls | Key used to attach and repair portals |
| `OPENAI_BASE_URL` | OpenAI API | OpenAI-compatible Chat Completions endpoint |
| `WATCHTOWER_MODEL` | `gpt-5.4-mini` | Model for attach and repair |
| `WATCHTOWER_MAX_TURNS` | `40` | Model calls per attach or repair |
| `WATCHTOWER_MAX_COST_USD` | `1.0` | Estimated model cost budget per attach or repair |
| `WATCHTOWER_INPUT_USD_PER_MILLION` | `0.75` | USD per million uncached input tokens |
| `WATCHTOWER_CACHED_INPUT_USD_PER_MILLION` | `0.075` | USD per million cached input tokens |
| `WATCHTOWER_OUTPUT_USD_PER_MILLION` | `4.50` | USD per million output tokens |
| `WATCHTOWER_RUN_TIMEOUT` | `3600` | Run timeout in seconds |
| `WATCHTOWER_HTTP_TIMEOUT` | `120` | HTTP timeout in seconds, with a 30-second connect timeout |
| `WATCHTOWER_RENDER_TIMEOUT` | `240` | Browser render timeout in seconds |
| `WATCHTOWER_BROWSER_WS` | Unset | Remote Playwright WebSocket endpoint to use instead of local Chromium |
| `WATCHTOWER_MAX_RUN_DOWNLOAD_MB` | `1000` | Download budget per run |
| `WATCHTOWER_MAX_FILE_MB` | `250` | Largest file size allowed |
| `WATCHTOWER_MAX_DOCS_PER_TENDER` | `30` | Maximum document links processed per tender |
| `WATCHTOWER_MAX_DETAILS` | Unset | Optional testing limit on tender details and document collection |
| `WATCHTOWER_USER_AGENT` | `JimixWatchtower/0.1 (public tender monitoring)` | Portal request user agent |
| `JIMIX_DB` | `/data/jimix.db` in Docker, `data/jimix.db` locally | SQLite database path |
| `JIMIX_FILES` | `/data/files` in Docker, `data/files` locally | Downloaded file directory |
| `JIMIX_API_URL` | `http://127.0.0.1:8000` locally | Frontend API origin used at Next.js build or dev startup |

## Local development

Install uv, Python 3.13 or newer, and Node.js 20.9 or newer. Start the backend from `backend/`.
The model key can also be supplied in a `.env` file at the repository root.

```sh
cd backend
uv sync --extra dev
uv run playwright install chromium --only-shell
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, start the dashboard from the repository root.

```sh
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:3000`. To run both services in development containers, put the model key in
`.env` and run `docker compose up --build --wait` from the repository root. Compose exposes the
dashboard at port 3000 and the API at port 8000. Override these with `JIMIX_FRONTEND_PORT` and
`JIMIX_BACKEND_PORT`. The frontend container uses `JIMIX_API_URL=http://backend:8000` by default.

See [testing instructions](docs/testing.md) for fixture tests and real-portal checks.
