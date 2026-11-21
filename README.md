# Jimix Watchtower

Jimix monitors public procurement portals, collects open tenders and their documents, and reports portal failures in an operator dashboard.

## Build and run

Put your model key in the repository-root `.env` file:

```env
OPENAI_API_KEY=your-key
```

Build and run the standalone image from the repository root:

```sh
docker build --platform linux/amd64 -t jimix .
docker run --rm -p 8080:8080 --env-file .env -v jimix-data:/data jimix
```

Open <http://localhost:8080>. The image includes the API, dashboard, and Chromium headless shell. The `jimix-data` volume preserves the database and downloaded documents.

## How it works

When an operator adds a portal, the agent explores public pages and APIs and proposes a declarative JSON recipe. Jimix validates the recipe, then applies it on Refresh without another model call. Reads normalize tender fields, download documents, and retain the last good results if a portal breaks. The dashboard shows portal state, tenders, files, run history, and model usage.

Portal requests respect robots.txt, use public addresses, and are limited to one request per second per site. Files larger than 250 MB are skipped by default.

## Model costs

Two fresh TenderNed attaches on November 21, 2026 used 13–15 model calls and cost USD 0.0524–0.0674 each. Each follow-up Refresh used zero model calls. Two failed attempts on other Dev portals cost USD 0.0733 and USD 0.1324. These are measurements from 10-detail samples. Costs vary by portal; hosting is separate.

## Local development

For backend development, install Python 3.13+, uv, and Node.js 20.9+. From `backend/`, run `uv sync --extra dev`, `uv run playwright install chromium --only-shell`, then `uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`. The backend reads `OPENAI_API_KEY` from the repository-root `.env`.

For dashboard development, run `npm ci && npm run dev` from `frontend/`. Open <http://localhost:3000>.

See [testing instructions](docs/testing.md) for tests and capped portal checks.

## License

[MIT](LICENSE)
