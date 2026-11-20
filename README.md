# Jimix

test
## Run locally

Start the FastAPI backend from the repository root.

```sh
python -m pip install -r backend/requirements.txt
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
