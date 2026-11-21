# Repository instructions

- Run Python commands from the repository root with `uv run`. To set up the environment for both Watchtower and FastAPI, use `uv sync --extra dev --extra backend`.
- Use `uv run --extra backend python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000` to start FastAPI on the host for either frontend setup.
- From `frontend`, use `npm ci` to install the Next.js dependencies and `npm run dev` to start the dashboard. Use `docker compose up --build --wait` from the repository root to start the frontend in Docker.
- Follow the README's Chromium setup for Watchtower tests. Run the full suite with `uv run --extra dev --extra backend python -m pytest -q`.
