# Jimix

## Run with Docker Compose

With Docker Desktop in Linux container mode or Docker Engine with the Compose
plugin, run this from the repository root.

```sh
docker compose up --build --wait
```

Compose starts the Next.js frontend at `http://127.0.0.1:3000/`. If port 3000
is busy, set `JIMIX_FRONTEND_PORT` before starting and open that port instead.

Use `docker compose logs -f frontend` to follow startup and `docker compose down`
to stop the frontend.

### Connect the backend

On Docker Desktop, run FastAPI on the host from `backend/` in a
second terminal. Install uv first if needed.

```sh
cd backend
uv run playwright install chromium --only-shell
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The frontend proxies API requests to `http://host.docker.internal:8000`.
Verify the connection through the frontend's [OpenAPI response](http://127.0.0.1:3000/openapi.json).
To use a different reachable API, set `JIMIX_API_URL` before rebuilding the frontend.
[Docker Desktop host networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/#connect-a-container-to-a-service-on-the-host)

## Local setup

### FastAPI backend

Install uv before running the Python commands below. uv manages the project
environment and can download a compatible Python version (3.13 or newer).
[uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)

From `backend/`, use uv to install the backend dependencies, Chromium, and start FastAPI.

```sh
cd backend
uv run playwright install chromium --only-shell
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Next.js frontend

Install Node.js 20.9 or later with npm. Node.js 24 matches the Docker image.
From the repository root, run these commands in a second terminal.

```sh
cd frontend
npm ci
npm run dev
```

Open [the dashboard](http://127.0.0.1:3000/). Next.js forwards API requests
to `http://127.0.0.1:8000` by default. To build and serve the frontend locally
in production mode, run `npm run build` and `npm start` from `frontend`.
Set `JIMIX_API_URL` before starting development or building for production to
use a different API origin.

Jimix saves new portals immediately and looks up public registration data in
the background through RDAP or WHOIS. The radar refreshes automatically and
uses city coordinates from [GeoNames](https://www.geonames.org/) (CC BY 4.0).
It skips identifiable privacy proxies and falls back to a country marker when
the registration or country-code domain provides a usable country.

## Manual frontend Docker setup

Run these PowerShell commands from the repository root with Docker Desktop in
Linux container mode. To start FastAPI on your host, follow the connection
instructions above.

Both targets use Node.js 24 and install dependencies from `package-lock.json`
during the build. The frontend reaches the host API at
`http://host.docker.internal:8000`. [Docker Desktop networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/)

### Development with hot reload

```powershell
docker build --target development -t jimix-frontend:dev ./frontend
$frontend = (Resolve-Path .\frontend).Path
docker run --rm --init -p 127.0.0.1:3000:3000 `
  --mount "type=bind,source=$frontend\app,target=/app/app,readonly" `
  --mount "type=bind,source=$frontend\public,target=/app/public,readonly" `
  --mount "type=bind,source=$frontend\world-map.svg,target=/app/world-map.svg,readonly" `
  -e JIMIX_API_URL=http://host.docker.internal:8000 `
  jimix-frontend:dev
```

Edits to the mounted files are visible in the container. Rebuild the development
image after changing dependencies or `next.config.mjs`.

### Production build

The production image runs the Next.js standalone server as the `node` user.

```powershell
docker build --target production -t jimix-frontend:prod `
  --build-arg JIMIX_API_URL=http://host.docker.internal:8000 `
  ./frontend
docker run --rm --init -p 127.0.0.1:3000:3000 jimix-frontend:prod
```

Set `JIMIX_API_URL` to a reachable API origin in the development `docker run`
command or the production `docker build` command. Next.js writes the production
rewrites at build time, so changing the backend origin requires a new image.

Open [the dashboard](http://127.0.0.1:3000/) and check that the frontend can
reach FastAPI at [the proxied OpenAPI document](http://127.0.0.1:3000/openapi.json).
If port 3000 is in use, change the port mapping to `127.0.0.1:3001:3000` and
open [port 3001](http://127.0.0.1:3001/).

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
uv run --extra dev python -m pytest -q
```
