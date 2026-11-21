# Board

The shared todo list. Rules: `PROTOCOL.md`. Claim one item at a time; keep items small.

### B-01 Document URLs built from tender fields
- **Priority:** high (documents are the core feature)
- **Status:** claimed by lead-w1 (matiiss machine, Claude subagent) 2026-11-21 03:15
- **Problem:** TenderNed (https://www.tenderned.nl/) attaches through its own JSON API
  (`/papi/tenderned-rs-tns/v2/publicaties?page=0&size=50`, 150 tenders in a test), but documents are
  at `/papi/tenderned-rs-tns/v2/publicaties/{publicatieId}/pdf` and a recipe can't build that URL.
- **Idea:** a declarative URL template in the recipe, e.g. `"documents": {"template": ".../{id}/pdf"}`,
  filled from mapped fields. Also check whether that PDF is the notice or the actual tender documents.
- **Test with:** TenderNed.
- **Findings:**

### B-02 Documents on a different external platform per tender
- **Priority:** high (documents are the core feature)
- **Status:** done (9a25ba81)
- **Last seen:** 2026-11-21 03:04
- **Problem:** evergabe.nrw.de (https://www.evergabe.nrw.de/VMPCenter/, cosinex software) lists tenders
  fine (id via pattern `pid=(\d+)`, unfiltered "welcome" list), but each tender's files live on an
  external platform (e.g. `vergabe.duesseldorf.de` NetServer), different per buyer. One `documents`
  selector can't cover them.
- **Idea:** sub-recipes per document platform (keyed by host or software), reusable across portals.
- **Findings:**
  Recipes select files by exact buyer hostname after a notice link or redirect. A blocked buyer leaves
  other buyers available and preserves its own previous files. The NRW sample found five Westfalen
  download links and saved a verified 44,729-byte PDF. Düsseldorf reaches the documents page
  and needs its public JavaScript modal to list files (B-04).
  Reviewed by the Codex recipe and coverage reviewers. Fifteen backend tests and Ruff passed.

### B-03 Bulk-export sources
- **Status:** open
- **Problem:** oeffentlichevergabe.de's documented open-data API
  (`/documentation/api/opendata`, OpenAPI 3.1) only offers daily or monthly ZIP exports of eForms XML,
  and its documents live on the platforms the notices link to.
- **Idea:** a `bulk_export` recipe kind (download ZIP, parse eForms XML into tenders) plus following each
  notice's link to its hosting platform.
- **Findings:**

### B-04 POST search APIs and browser interactions
- **Priority:** high (Düsseldorf NetServer documents only list after a modal click, see B-02)
- **Status:** claimed by Codex (Johanna Mac) 2026-11-21 03:30
- **Last seen:** 2026-11-21 03:30
- **Problem:** some JavaScript portals only load results after a search action or a POST request
  (oeffentlichevergabe.de's search page loads only region data on its own). Recipes only do GET.
- **Idea:** declarative POST bodies for JSON listings; maybe a short declarative list of browser
  actions (click a selector, wait).
- **Findings:**
  B-02 live observation: NRW notice 1138705 links to Düsseldorf NetServer. Its public download
  control opens a modal, and the document listing has no ordinary file links until that step.

### B-05 Prompt-injection hardening
- **Status:** open
- **Problem:** recipes are data, so no code runs, but page text could still steer the agent: wrong pages,
  fetching unrelated URLs. `give_up(blocked=true)` is already refused unless a block was detected.
- **Idea:** require listing and detail URLs to share the portal's site (documents may be elsewhere);
  review what tool outputs pass to the model.
- **Findings:**

### B-06 Docker: backend image, compose entry, single root Dockerfile
- **Status:** done (700f08b), verification split out as B-16
- **Problem:** the PDF wants one root `Dockerfile` serving API and dashboard on port 8080. Only the
  frontend has a Dockerfile.
- **Findings:** `backend/Dockerfile` + compose `backend` service (frontend waits for its /health); root `Dockerfile` builds the dashboard (standalone Next.js) and backend with Chromium into one image; `docker/start.sh` runs uvicorn on 127.0.0.1:8000 and the dashboard on 0.0.0.0:8080, which proxies /portals, /files, /openapi.json, /health. No nginx or Docker-in-Docker (DinD would need --privileged). Builds untested: Docker Hub DNS failed on the build machine.

### B-07 Dashboard against the new API
- **Status:** done (0ee02c3)
- **Problem:** check the dashboard shows the new fields: run `reason`, `llmCalls`, `costUsd`, portal
  `reason` texts, the four states. The PDF wants an operator to see which portals deliver, which broke and
  what the system did.
- **Findings:**
  Dashboard shows API run reasons, calls and estimated USD per run and in the usage summary. Chromium verified the current API shape and legacy fallback. Frontend build and 13 backend tests passed.

### B-08 Live log stream per run
- **Status:** open
- **Problem:** `watchtower.*` logs show every step but only on the server console.
- **Idea:** store run-scoped log lines (a `run_log` table or similar) and stream them to the dashboard (SSE).
- **Findings:**

### B-09 Verify refresh and repair end to end
- **Priority:** high (documents are the core feature)
- **Status:** claimed by lead-w2 (matiiss machine, Claude subagent) 2026-11-21 02:46
- **Last seen:** 2026-11-21 03:30 (vergabe.metropoleruhr.de attached and refreshed; repair test next)
- **Problem:** `/refresh` and repair work in unit-level runs but haven't been exercised on a real portal
  that reached `serving`.
- **Test:** attach a portal until `serving`, refresh it, then break its stored recipe (edit `recipe.data`)
  and refresh to trigger `healing` and repair.
- **Findings:**

### B-10 Test portal set
- **Status:** open
- **Problem:** the PDF scores on unseen portals. We need our own set: at least 4 countries, 3 portal
  software packages, server-rendered, JavaScript and multi-step portals, plus 1–2 behind login or captcha
  (to check `failed` with a reason). See the PDF's "Finding test portals" page.
- **Deliverable:** a list in this file with each portal's result.
- **Findings:**

### B-11 Cost per portal for the README
- **Status:** open
- **Problem:** the PDF wants the model cost of one portal in the README. Runs record it
  (`GET /portals/{id}/runs`); collect numbers across the test set.
- **Findings:**

### B-12 Agent tool-output size and cost
- **Status:** open
- **Problem:** every model call resends the whole conversation; attaches use 100–260k input tokens
  (mostly cached). Ideas: an adjustable number of earlier tool results kept in full (0 = no compaction)
  with a cached-response toggle for repeats; `fetch_page` returning an outline first.
- **Findings:**

### B-13 Document links that return an HTML page are silently dropped
- **Priority:** high (documents are the core feature)
- **Status:** claimed by Codex (Dani146 Windows) 2026-11-21 03:19
- **Last seen:** 2026-11-21 03:19
- **Problem:** `Fetcher.download` (`backend/app/fetch.py`) returns `None` when a document URL answers with
  HTML (a landing or "download" page) or a file over `MAX_FILE_BYTES`. `fetch_documents` then keeps
  `complete=True`, so the tender is stored with 0 files, marked `_documentsComplete`, and never retried
  on refresh. Nothing in the run reason or logs says why.
- **Idea:** count those URLs as skipped (with a reason: "HTML page", "over size limit") and keep
  `complete` false for HTML answers; surface the count in the run reason like the existing
  "No document links found" note. Unit test with a mock transport.
- **Findings:**

### B-14 Dashboard: show per-tender document state
- **Priority:** high (part of "Done means": the operator sees what happened)
- **Status:** in review (PR #5)
- **Problem:** the tender card (`frontend/public/dashboard.js`, around line 284) shows "No document records
  supplied." both when a tender has no document links and when files are still pending (capped by
  `WATCHTOWER_MAX_DETAILS`, blocked, budget hit). `GET /portals/{id}/tenders` drops every `_`-prefixed key,
  so `_documentUrls` and `_documentsComplete` never reach the dashboard.
- **Idea:** expose a public `documentsStatus` (e.g. `complete`, `pending`, `none`) and the source document
  URLs in the tender API; show "n files", "pending, next refresh" or "no document links" on the card.
- **Findings:** PR #5 is open. The API and dashboard distinguish pending, complete, no links, and links without files. Raw source URLs stay private. Reviewed by the Codex document status reviewer. All 16 backend tests and the frontend build passed.

### B-15 Preserve saved files after a temporary document-page error
- **Priority:** high (transient errors should preserve delivered documents)
- **Status:** in review (PR #6)
- **Problem:** When an NRW-style tender follows a link to a document page and that page returns a
  temporary HTTP error, the recipe treats it as an empty result. A refresh can replace previously
  downloaded files with an empty list. This affects shared and host-specific linked pages.
- **Idea:** Distinguish failed page loads from a successful page with no file links. Keep last-known
  files on failure and retry on a later refresh. Test with a mock transport.
- **Findings:** PR #6 preserves previous files and retries after linked document-page errors. Covered shared and per-host page selectors through mock HTTP 503, recovery, and genuinely empty pages. Two Codex reviewers and 17 backend tests passed. Detail-page failures and skipped details can still produce an empty result before link following; track separately.

### B-16 Verify the root and backend images build and run
- **Status:** claimed by Codex (Derek Windows) 2026-11-21 03:27
- **Last seen:** 2026-11-21 03:27
- **Problem:** `docker build .` (root) and `docker compose up --build --wait` were never run successfully;
  the build machine couldn't reach Docker Hub. Check: build on linux/amd64 reasonably fast, `docker run -p
  8080:8080 -e OPENAI_API_KEY=... <image>` serves `/` (dashboard), `/portals`, `/health` on 8080, Chromium
  renders inside the container, data persists in `/data`.
- **Findings:**

### Later (not for the hackathon unless time allows)
- Scheduler for periodic refresh, with backoff so broken portals don't burn model calls.
- Postgres and a job queue instead of SQLite and in-process tasks.
- Rate limit shared across processes; replace `fetch.site()` with `tldextract`.
- Storage cap and deleting files no tender references.
- Redaction of secrets as a logging filter.
- The system proposing fixes to its own recipe format or code as pull requests.
