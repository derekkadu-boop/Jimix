# Jimix

## Run locally

Start the FastAPI backend from the repository root.

```sh
python -m pip install -e ".[backend]"
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
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

Jimix saves new portals immediately and looks up public registration data in
the background through RDAP or WHOIS. The radar refreshes automatically and
uses city coordinates from [GeoNames](https://www.geonames.org/) (CC BY 4.0).
It skips identifiable privacy proxies and falls back to a country marker when
the registration or country-code domain provides a usable country.

## Patterno Watchtower Website Explorer

Give Watchtower a public procurement portal URL to get a recommended extraction method and its verification evidence. Watchtower checks robots.txt and access restrictions, then uses OpenAI to choose inspection tools and Python to verify the proposed recipe. A normal run works in memory and prints the recommendation.

### PowerShell setup

Run these commands from the repository root using Python 3.13 or newer and the Windows Python launcher.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.venv-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
```

You can also create the environment and install dependencies with `uv`.

```powershell
uv python install 3.13
uv venv --python 3.13
uv pip install --python .\.venv\Scripts\python.exe -e ".[dev]"
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.venv-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
```

Use `-e ".[backend]"` to add the FastAPI backend dependencies or `-e ".[dev,backend]"` to install both extras.

### Run an exploration

Set your OpenAI key in the current PowerShell session, or set `OPENAI_API_KEY` in a local `.env` file. Model exploration uses your OpenAI account. Set `--max-llm-calls 0` for a run that only inspects robots.txt and the initial page.

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.venv-browsers"
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'OpenAI API key' -AsSecureString)).Password
.\.venv\Scripts\python.exe explore.py https://example.eu/procurement
.\.venv\Scripts\python.exe explore.py https://example.eu/procurement --max-duration 60 --max-requests 15 --max-llm-calls 5
```

Replace the example with the portal URL. Add `--json` for the full recipe and evidence or `--verbose` for live progress and budget usage. Ctrl+C cancels the running action and returns the findings available so far. Exit codes are `0` for verified, `1` for failure, `2` for incomplete, `3` for blocked, and `130` for cancellation.

### Saved runs

Add `--db` to keep portal settings and run history in SQLite. To resume, use the saved run ID printed by the command. Resume gets a fresh budget and rechecks access and recipe evidence.

```powershell
.\.venv\Scripts\python.exe explore.py https://example.eu/procurement --db data/watchtower.db
.\.venv\Scripts\python.exe explore.py --resume RUN_ID --db data/watchtower.db
```

Storage-backed service calls use `data/watchtower.db` by default. Set `$env:WATCHTOWER_DB` to use a different file for those calls.

### Python service

Use `explore_portal(url, config=None)` for a one-off recommendation. The result exposes `best_candidate_method`, `candidate_verified`, and the recipe when available. Pass an initialized `ExplorationStore` to `Explorer` to keep history. The saved-run services are `resume_exploration`, `get_portal_config`, `update_portal_config`, and `get_exploration_progress`. Cancellation reaches runs in the same Python process through `cancel_exploration`.

The model's tool list and OpenAI function schemas are in `watchtower/tools/__init__.py`. Tool handlers return bounded observations for the model to interpret and test its proposed extraction against live responses. Portal HTTP and browser requests share the access checks and request limits in `watchtower/transport.py`.

Edit `watchtower/phrases.json` to add translations or variants for page recognition. Each signal groups its phrases by language, with `und` used for language-neutral terms and symbols.

Protego parses robots.txt rules. The documentation tool uses `openapi3-parser` for OpenAPI 3 and Swagger 2 specs in JSON or YAML. It resolves references within the downloaded spec. For HTML, Markdown, and plain text, the tool returns short excerpts and any explicit GET examples it finds. XML sitemaps provide page links.

```python
from watchtower import get_portal_config, update_portal_config

config = await get_portal_config(portal_id)
updated = await update_portal_config(
    portal_id,
    {
        "max_http_requests": 20,
        "request_interval_seconds": 2.0,
    },
)
```

Run overrides apply to the current exploration. Saved configuration includes request headers, model and pricing, robots handling, and run limits. The progress service returns the step journal. The default model and its configured rates come from the OpenAI model reference. [GPT-5.4 mini model reference](https://developers.openai.com/api/docs/models/gpt-5.4-mini)

### Boundaries and current coverage

- Discovery uses public GET and HEAD requests. HTTPX and Chromium share request limits and per-host rate control, with a one-second floor and longer crawl delays applied when required by robots.txt. Redirects, robots requests, and browser subrequests are counted. Cached observations are tracked separately.
- Robots policies are checked for each permitted host, and private or mixed public/private DNS destinations are rejected. URLs with credentials, unrecognized query parameters, or nondefault ports are rejected. Chromium denies direct outbound requests and uses the same guarded transport as HTTPX.
- JSON API, HTML, rendered DOM, and hybrid listing/detail recipes are supported. Verification samples records and each declared retrieval step. A verified sample establishes that the path works for the sampled records. Coverage, ID stability across runs, and recovery after upstream changes need ongoing monitoring. Portals with zero open notices produce an unverified finding.
- A model token-count request, a generation, and every attempted retry each consume an OpenAI request slot. At the default request limit, five count-and-generation pairs fit before retries. When a count request is unavailable or there is no spare slot, Watchtower uses a conservative local upper bound. Reported usage is tracked separately from reservations for requests that returned no usage. The estimated cost uses the configured rates and is an estimate of API usage.
- Chromium inspection is limited to public pages and eligible subresources. Portals that depend on authenticated state, POST-based search, cross-origin application requests absent from observable links, WebSockets, interactive browser actions, or unsupported document formats may produce partial results.

### Tests

After installing Chromium, run the test suite from the repository root.

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.venv-browsers"
.\.venv\Scripts\python.exe -m pytest -q
```

The suite uses synthetic portal fixtures, a local HTTP server, mocked OpenAI responses, and real headless Chromium. It makes no paid OpenAI calls.
