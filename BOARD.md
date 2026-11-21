# Board

The shared todo list. Rules: `PROTOCOL.md`. Claim one item at a time; keep items small.

### B-01 Document URLs built from tender fields
- **Status:** open
- **Problem:** TenderNed (https://www.tenderned.nl/) attaches through its own JSON API
  (`/papi/tenderned-rs-tns/v2/publicaties?page=0&size=50`, 150 tenders in a test), but documents are
  at `/papi/tenderned-rs-tns/v2/publicaties/{publicatieId}/pdf` and a recipe can't build that URL.
- **Idea:** a declarative URL template in the recipe, e.g. `"documents": {"template": ".../{id}/pdf"}`,
  filled from mapped fields. Also check whether that PDF is the notice or the actual tender documents.
- **Test with:** TenderNed.
- **Findings:**

### B-02 Documents on a different external platform per tender
- **Status:** open
- **Problem:** evergabe.nrw.de (https://www.evergabe.nrw.de/VMPCenter/, cosinex software) lists tenders
  fine (id via pattern `pid=(\d+)`, unfiltered "welcome" list), but each tender's files live on an
  external platform (e.g. `vergabe.duesseldorf.de` NetServer), different per buyer. One `documents`
  selector can't cover them.
- **Idea:** sub-recipes per document platform (keyed by host or software), reusable across portals.
- **Findings:**

### B-03 Bulk-export sources
- **Status:** open
- **Problem:** oeffentlichevergabe.de's documented open-data API
  (`/documentation/api/opendata`, OpenAPI 3.1) only offers daily or monthly ZIP exports of eForms XML,
  and its documents live on the platforms the notices link to.
- **Idea:** a `bulk_export` recipe kind (download ZIP, parse eForms XML into tenders) plus following each
  notice's link to its hosting platform.
- **Findings:**

### B-04 POST search APIs and browser interactions
- **Status:** open
- **Problem:** some JavaScript portals only load results after a search action or a POST request
  (oeffentlichevergabe.de's search page loads only region data on its own). Recipes only do GET.
- **Idea:** declarative POST bodies for JSON listings; maybe a short declarative list of browser
  actions (click a selector, wait).
- **Findings:**

### B-05 Prompt-injection hardening
- **Status:** open
- **Problem:** recipes are data, so no code runs, but page text could still steer the agent: wrong pages,
  fetching unrelated URLs. `give_up(blocked=true)` is already refused unless a block was detected.
- **Idea:** require listing and detail URLs to share the portal's site (documents may be elsewhere);
  review what tool outputs pass to the model.
- **Findings:**

### B-06 Docker: backend image, compose entry, single root Dockerfile
- **Status:** claimed by lead (main Claude session, matiiss machine) 2026-11-21 02:20
- **Problem:** the PDF wants one root `Dockerfile` serving API and dashboard on port 8080. Only the
  frontend has a Dockerfile.
- **Findings:**

### B-07 Dashboard against the new API
- **Status:** open
- **Problem:** check the dashboard shows the new fields: run `reason`, `llmCalls`, `costUsd`, portal
  `reason` texts, the four states. The PDF wants an operator to see which portals deliver, which broke and
  what the system did.
- **Findings:**

### B-08 Live log stream per run
- **Status:** open
- **Problem:** `watchtower.*` logs show every step but only on the server console.
- **Idea:** store run-scoped log lines (a `run_log` table or similar) and stream them to the dashboard (SSE).
- **Findings:**

### B-09 Verify refresh and repair end to end
- **Status:** open
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

### Later (not for the hackathon unless time allows)
- Scheduler for periodic refresh, with backoff so broken portals don't burn model calls.
- Postgres and a job queue instead of SQLite and in-process tasks.
- Rate limit shared across processes; replace `fetch.site()` with `tldextract`.
- Storage cap and deleting files no tender references.
- Redaction of secrets as a logging filter.
- The system proposing fixes to its own recipe format or code as pull requests.
