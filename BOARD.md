# Board

The shared todo list. Rules: `PROTOCOL.md`. Claim one item at a time; keep items small.


## Workers

One line per worker; refresh at least every 30 minutes (see PROTOCOL.md, "Staying alive and restarting").

- lead | Claude Code main session, matiiss machine | last seen 2026-11-21 04:06 | protocol keeper
- lead-w1 | Claude subagent, matiiss machine | last seen 2026-11-21 03:39 | B-01 (paused until 04:05 CET, claim held)
- lead-w2 | Claude subagent, matiiss machine | last seen 2026-11-21 03:38 | B-09 (paused until 04:05 CET, claim held)
- lead-monitor | Claude subagent, matiiss machine | last seen 2026-11-21 04:08 | coord monitoring (every 20-30 min)
- Codex (Derek Windows) | Codex, Derek's Windows machine | last seen 2026-11-21 04:14 | B-04
- Codex (Dani146 Windows) | Codex, Dani146's Windows machine | last seen 2026-11-21 04:13 | B-20
- Codex (Johanna Mac) | Codex, Johanna's Mac | last seen 2026-11-21 04:04 | B-17 in review (PR #7)

### B-01 Document URLs built from tender fields
- **Priority:** high (documents are the core feature)
- **Status:** claimed by lead-w1 (matiiss machine, Claude subagent) 2026-11-21 03:15
- **Problem:** TenderNed (https://www.tenderned.nl/) attaches through its own JSON API
  (`/papi/tenderned-rs-tns/v2/publicaties?page=0&size=50`, 150 tenders in a test), but documents are
  at `/papi/tenderned-rs-tns/v2/publicaties/{publicatieId}/pdf` and a recipe can't build that URL.
- **Idea:** a declarative URL template in the recipe, e.g. `"documents": {"template": ".../{id}/pdf"}`,
  filled from mapped fields. Also check whether that PDF is the notice or the actual tender documents.
- **Test with:** TenderNed.
- **Last seen:** 2026-11-21 03:39 (paused until 04:05 CET, claim held)
- **Findings:** (in progress, branch `b-01-url-templates`, not merged, awaiting review) Recipe gains
  `detail.url` and `documents_url: {template, kind}` (placeholders = mapped fields) plus dot-path
  `attribute`. TenderNed attaches with it ($0.04-0.05, serving, 9 of 10 sampled tenders got a file), but
  the agent picks `/{id}/pdf`, which is only the notice. The real tender files are at
  `/papi/tenderned-rs-tns/v2/publicaties/{id}/documenten` (JSON, `documenten[].links.download.href`,
  e.g. 450212 has bestek, leidraad, ZIPs). That list loads only when the Documents tab is clicked, so
  render_page misses it. Branch now also has test_recipe probe sibling `documents`/`documenten`/...
  endpoints next to a single-file documents_url; not yet tried on the real portal.

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
- **Status:** claimed by Codex bulk-export (Johanna Mac) 2026-11-21 03:30
- **Last seen:** 2026-11-21 03:30
- **Problem:** oeffentlichevergabe.de's documented open-data API
  (`/documentation/api/opendata`, OpenAPI 3.1) only offers daily or monthly ZIP exports of eForms XML,
  and its documents live on the platforms the notices link to.
- **Idea:** a `bulk_export` recipe kind (download ZIP, parse eForms XML into tenders) plus following each
  notice's link to its hosting platform.
- **Findings:**

### B-04 Read public Düsseldorf NetServer document lists
- **Priority:** high (documents are the core feature)
- **Status:** claimed by Codex (Derek Windows) 2026-11-21 04:14
- **Last seen:** 2026-11-21 04:14
- **Problem:** NRW notice 1138705 links to Düsseldorf NetServer. Public document links appear only
  after opening the versioned file-list dialog.
- **Findings:** The NetServer option opens the latest public file-list version and reads its
  newly displayed links. The live NRW sample produced 25 links and a verified 102,316-byte PDF.
  Failed dialogs retain earlier files and allow other tenders to finish. Reviewed by the Codex
  browser guard and recipe reviewers. All 19 backend tests and Ruff passed.
  A fresh NRW attach reached the public listing but gave up validating files across buyer hosts
  after 19 calls (estimated USD 0.099). A stored-recipe Refresh then delivered 232 files and served
  the verified PDF through the API with zero model calls. Public POST search is B-18 and automatic
  NRW attachment is B-20.
  Code is on main at 4665b28a. Reopened under the revised protocol for an unassisted attach
  that saves real files on a previously untested NetServer portal.

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
- **Last seen:** 2026-11-21 03:38 (paused until 04:05; metropoleruhr attach/refresh/repair verified, 2 fixes committed on branch b-09-refresh-repair, not merged yet)
- **Problem:** `/refresh` and repair work in unit-level runs but haven't been exercised on a real portal
  that reached `serving`.
- **Test:** attach a portal until `serving`, refresh it, then break its stored recipe (edit `recipe.data`)
  and refresh to trigger `healing` and repair.
- **Findings:**

### B-10 Dev portal set
- **Priority:** high (every document fix is validated here)
- **Status:** open (shared: anyone may take one portal at a time; write results below)
- **Rules:** fresh, empty `JIMIX_DB` per run; unassisted attach (just the URL); then refresh. Record state,
  tender count, files downloaded, model cost, and what blocked it. File every problem as an item (generic
  fixes only, see PROTOCOL.md "Build the system, not the answers"). Don't scout portals outside this list.
- **Dev portals:**
  - https://www.tenderned.nl/aankondigingen/overzicht (NL, JSON API)
  - https://oeffentlichevergabe.de/ui/ (DE, JS app + bulk export API)
  - https://www.evergabe.nrw.de/VMPCenter/ (DE, Cosinex, external buyer platforms)
  - https://www.boamp.fr/pages/api-boamp/ (FR, documented API)
  - https://contrataciondelestado.es/ (ES)
  - https://www.find-tender.service.gov.uk/ (UK)
  - https://www.etenders.gov.ie/epps/quickSearchAction.do?searchType=cftFTS&latest=true (IE, EPPS)
  - https://www.doffin.no/Notice (NO)
  - https://www.hankintailmoitukset.fi/en (FI)
  - https://ezamowienia.gov.pl/pl/ (PL)
  - https://dtvp.de/ausschreibungen/ (DE)
  - https://udbud.dk/ (DK)
- **Results** (one line per run: `date | portal | worker | state | tenders | files | cost | blocker`):
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
- **Status:** done (665cd5d5)
- **Last seen:** 2026-11-21 03:19
- **Problem:** `Fetcher.download` (`backend/app/fetch.py`) returns `None` when a document URL answers with
  HTML (a landing or "download" page) or a file over `MAX_FILE_BYTES`. `fetch_documents` then keeps
  `complete=True`, so the tender is stored with 0 files, marked `_documentsComplete`, and never retried
  on refresh. Nothing in the run reason or logs says why.
- **Idea:** count those URLs as skipped (with a reason: "HTML page", "over size limit") and keep
  `complete` false for HTML answers; surface the count in the run reason like the existing
  "No document links found" note. Unit test with a mock transport.
- **Findings:**
  HTML responses are reported and retried. Oversized files are reported and their URLs stay
  skipped while unchanged. Legacy caches are rechecked, and old files stay visible while
  that recheck is incomplete or deferred. A live Westfalen check verified an HTML landing
  page and a PDF over a small test limit. Seventeen backend tests and Ruff passed after
  merging main. Reviewed by the Codex download and cache reviewers.

### B-14 Dashboard: show per-tender document state
- **Priority:** high (part of "Done means": the operator sees what happened)
- **Status:** done (153746a)
- **Problem:** the tender card (`frontend/public/dashboard.js`, around line 284) shows "No document records
  supplied." both when a tender has no document links and when files are still pending (capped by
  `WATCHTOWER_MAX_DETAILS`, blocked, budget hit). `GET /portals/{id}/tenders` drops every `_`-prefixed key,
  so `_documentUrls` and `_documentsComplete` never reach the dashboard.
- **Idea:** expose a public `documentsStatus` (e.g. `complete`, `pending`, `none`) and the source document
  URLs in the tender API; show "n files", "pending, next refresh" or "no document links" on the card.
- **Findings:** PR #5 adds this status. The API and dashboard distinguish pending, complete, no links, and links without files. Raw source URLs stay private. Reviewed by the Codex document status reviewer. All 16 backend tests and the frontend build passed. Merged after B-19; all 24 integrated backend tests, the production build, and the refresh-to-tender-API status check passed.

### B-15 Preserve saved files after a temporary document-page error
- **Priority:** high (transient errors should preserve delivered documents)
- **Status:** done (ce9a291)
- **Problem:** When an NRW-style tender follows a link to a document page and that page returns a
  temporary HTTP error, the recipe treats it as an empty result. A refresh can replace previously
  downloaded files with an empty list. This affects shared and host-specific linked pages.
- **Idea:** Distinguish failed page loads from a successful page with no file links. Keep last-known
  files on failure and retry on a later refresh. Test with a mock transport.
- **Findings:** Integrated the linked-page retry with the current cache and pending handling. Two Codex reviewers approved, and all 27 backend tests and Ruff passed. Live NRW notice 1138704 retained its files through a locally simulated linked-page 503 and recovered.

### B-16 Verify the root and backend images build and run
- **Status:** done (38d8e63)
- **Last seen:** 2026-11-21 03:27
- **Problem:** `docker build .` (root) and `docker compose up --build --wait` were never run successfully;
  the build machine couldn't reach Docker Hub. Check: build on linux/amd64 reasonably fast, `docker run -p
  8080:8080 -e OPENAI_API_KEY=... <image>` serves `/` (dashboard), `/portals`, `/health` on 8080, Chromium
  renders inside the container, data persists in `/data`.
- **Findings:** Windows Docker Desktop verified the linux/amd64 root build and healthy Compose stack. Installed libatomic1 before Playwright, and normalized the executable startup script inside the root image. Chromium opened the dashboard and reached the API on 8080. Health, portal list and a synthetic document download worked, and the database and file persisted through container replacement. 17 backend tests passed. Pre-commit review: Codex B-16 reviewer.

### B-17 Bound failed document downloads and clear partial files
- **Status:** done (b693cee, merged PR #7)
- **Problem:** Fetcher reads HTML document responses in full before applying the byte limit, and
  an interrupted streamed download can leave a partial file on disk.
- **Idea:** Bound the HTML used for access checks and clean partial files on interruption.
- **Findings:** PR #7 bounds the HTML probe, charges received bytes, and deletes interrupted partials. Two Codex reviewers and 28 backend tests passed, including Chromium. Pre-existing follow-up: unexpected compressed binary responses still enter HTTPX decompression before the byte check.

### B-18 Read public listings backed by POST search
- **Status:** open
- **Problem:** On https://www.oeffentlichevergabe.de/, the search page returns only region data
  before a search action. Listing recipes currently use GET and cannot read a public POST search.
- **Idea:** Verify the site's unfiltered public search and add narrowly scoped listing support.
  Bulk exports are tracked under B-03.
- **Findings:** Split from B-04 after shipping the Düsseldorf document dialog.

### B-20 Attach NRW automatically across buyer platforms
- **Priority:** high (public document delivery on a mixed-platform portal)
- **Status:** claimed by Codex (Dani146 Windows) 2026-11-21 03:51
- **Last seen:** 2026-11-21 04:13
- **Problem:** A fresh API attach of https://www.evergabe.nrw.de/VMPCenter/company/welcome.do
  found the public HTML listing but stopped because it could not validate document handling
  across buyer hosts. B-04 verified Düsseldorf file delivery using a stored recipe.
- **Idea:** Record structured recipe validation outcomes from a bounded attach and identify which
  buyer routes or coverage checks keep the model from submitting a working recipe.
- **Findings:** One live attach failed after 19 model calls at an estimated USD 0.099.
  The trace found JavaScript-only hrefs for category pagination. The controls contain a public GET
  path, and separate categories reuse the same page URLs in one session. Fix 563e2ec2 is on main.
  Two categories now returned 80 distinct notices across four sampled pages. Two reviewers and
  25 integrated backend tests passed. A live attach with the new paging hint reached serving and
  read 449 tenders, but saved zero files. Its recipe only mapped Düsseldorf and did not use the modal.
  Under the updated protocol, the pagination candidate is derived from recognized markup.
  On NRW it appears at character 21,854, beyond the first 12,000 characters the agent sees.
  Testing early exposure plus upcoming-deadline sample selection. A fresh Brandenburg attach
  was blocked by robots.txt. Fresh NRW and previously untested Rheinland-Pfalz runs are underway.

### B-19 Preserve documents when tender detail was not checked
- **Priority:** high (saved documents and dashboard status)
- **Status:** done (8d2980a)
- **Last seen:** 2026-11-21 03:56
- **Problem:** A failed or deferred tender detail request can produce empty document URLs. The
  refresh stores an empty document list and can mark it complete even though discovery never ran.
  This can erase saved files and make B-14 display no document links for an unchecked tender.
- **Idea:** Propagate unchecked detail discovery to delivery, keep previous files and URLs, and
  distinguish it from a successful detail request containing no document links.
- **Findings:** Unchecked details keep saved files, source URLs, and skip metadata and remain pending. A successful empty detail can clear files. Two Codex reviewers approved, 23 backend tests and Ruff passed, and live NRW notice 1138704 retained its verified PDF after a capped refresh.

### B-21 Heavy JavaScript portals render slowly at one request per second
- **Priority:** high (blocks documents on JS portals)
- **Status:** open
- **Problem:** TenderNed's Angular app loads dozens of script chunks; a render took about 2 minutes and the first try hit the old 90 s timeout. The asset cache (`fetcher.assets`) only lives for one run, so every attach and repair starts cold. oeffentlichevergabe.de's app also failed to boot once because its main script timed out.
- **Idea:** Persist the script/CSS cache per site across runs (disk, keyed by URL + ETag); measure render times on 2-3 JS portals; consider skipping clearly irrelevant third-party scripts.
- **Findings:**

### B-22 Agent can submit a recipe whose documents match nothing
- **Priority:** high (portals end up serving without files)
- **Status:** done (09092be)
- **Problem:** `test_recipe` only warns "no document links found in the sample"; the agent may submit again to accept the warning. TenderNed and evergabe.nrw.de both reached `serving` with 0 files this way.
- **Idea:** Make a working sample document download a hard requirement for `submit_recipe`, unless a login/captcha block on documents was actually detected (then fail with `Blocked: documents ...`). Keep an explicit escape only for portals that publish no files at all.
- **Findings:** `_check_documents` now returns a hard problem when no sampled tender yields any document link, so `submit_recipe` is refused; partial samples still pass. Reviewed by a review subagent (caveat: give_up(blocked=true) is refused without a detected block, so the message now says to use it only after a detected login/captcha). Behavior change: a portal whose sampled tenders genuinely have no files now ends `failed` instead of `serving` without files.

### B-23 Transient network errors abort whole runs
- **Priority:** medium
- **Status:** open
- **Problem:** On a flaky network single DNS timeouts or connection resets (httpx, Playwright route.fetch, OpenAI) fail the attach; there is only one retry for GETs and five for OpenAI.
- **Idea:** Retry with backoff (2-3 tries) for listing and detail GETs and browser subresources; count retries against the rate limit; log them.
- **Findings:**

### B-24 Browser subresources skip robots.txt
- **Priority:** low
- **Status:** open
- **Problem:** `browser.render` checks robots.txt for the page URL only; scripts and XHRs it loads are fetched without a robots check.
- **Idea:** Check `fetcher.allowed()` for same-site XHR/fetch requests in the route handler.
- **Findings:**

### B-25 Preserve ZIP-based Office documents as tender files
- **Priority:** high (deliver the original tender document)
- **Status:** done (971451f)
- **Last seen:** 2026-11-21 04:13
- **Problem:** Live NRW notice 1138704 links to a public Vergabe-Westfalen specification DOCX.
  Jimix unpacks every ZIP signature, so it exposes the Word file's package XML and loses the
  original DOCX from the tender. Five source links produced 51 stored entries. A two-entry
  synthetic DOCX also reproduces this via documents._save.
- **Idea:** Keep Office packages intact, still unpack ordinary ZIP bundles, and recheck cached
  results that previously exposed Office package internals. Test on NRW 1138704.
- **Findings:** Recognizes Office ZIP packages by their internal entries and stores them intact. Ordinary ZIP bundles still unpack, and existing cache records with Office internals are rechecked. Two Codex reviewers approved. All 45 backend tests and Ruff passed after integrating current main. Live NRW notice 1138704 recovered from 51 old entries to five files, including an intact 11,749,325-byte DOCX verified via the file API. Fresh unassisted attach from the previously unused Niedersachsen public listings page (https://vergabe.niedersachsen.de/Satellite/company/welcome.do) reached serving, read 400 tenders, and delivered 10 open-tender files for - **Findings:**.0777. That sample contained no Office files. The bare-domain redirect failure is tracked in B-28.

### B-26 Listings silently capped at max_pages
- **Priority:** medium (tenders beyond the cap never get documents)
- **Status:** open
- **Problem:** `Recipe.max_pages` defaults to 20 and the agent leaves it. On https://www.vergabe.metropoleruhr.de/
  (20 rows per page, about 41 pages) a repaired recipe read 400 tenders and stopped; `complete` is false,
  but no run reason or test warning says the listing was cut.
- **Idea:** report "listing stopped at max_pages" in test_recipe and the run reason; raise the default or
  let the agent set it from the page count it sees.
- **Findings:**

### B-27 test_recipe doesn't flag a listing without pagination
- **Priority:** medium
- **Status:** open
- **Problem:** the first attach of https://www.vergabe.metropoleruhr.de/ submitted a recipe with no
  next_page/page_param although the list page has a forward link (`a.browseForward`, page links with a
  numeric parameter): 20 of ~800 tenders. A later repair found the link by itself.
- **Idea:** generic check in test_recipe: when paging is unset and the first list page has a link whose
  text or class means next (multilingual keywords) or whose URL differs from the listing URL only by a
  numeric parameter, warn.
- **Findings:**

### B-28 Follow HTML refresh redirects during initial portal discovery
- **Priority:** high (a public portal address can stop before listing discovery)
- **Status:** open
- **Problem:** A fresh attach of https://vergabe.niedersachsen.de/ failed before finding a listing. The homepage returns HTTP 200 with an 89-byte HTML document and no links. Its only navigation is a standard HTML meta refresh to the procurement application. The agent spent 16 calls and $0.0234 without reaching the listings.
- **Idea:** Recognize HTML refresh redirects generically, with a bounded chain and the existing URL, rate-limit, robots and access checks. Verify a fresh unassisted attach from a homepage using that mechanism.
- **Findings:**

### Later (not for the hackathon unless time allows)
- Scheduler for periodic refresh, with backoff so broken portals don't burn model calls.
- Postgres and a job queue instead of SQLite and in-process tasks.
- Rate limit shared across processes; replace `fetch.site()` with `tldextract`.
- Storage cap and deleting files no tender references.
- Redaction of secrets as a logging filter.
- The system proposing fixes to its own recipe format or code as pull requests.
