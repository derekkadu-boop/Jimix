# Board

The shared todo list. Rules: `PROTOCOL.md`. Claim one item at a time; keep items small.


## Workers

One line per worker; refresh at least every 30 minutes (see PROTOCOL.md, "Staying alive and restarting").

- Derek-Codex-Main | Danylos/Windows, primary Codex session | last seen 2026-11-21 07:59 | B-73
- Derek-Codex-B | Danylos/Windows, Codex background worker | last seen 2026-11-21 08:09 | B-71, B-74 independent review
- lead | Codex, matiiss machine | last seen 2026-11-21 07:42 | coordinating
- lead-w1 | Codex subagent, matiiss machine | last seen 2026-11-21 07:53 | B-72
- lead-w2 | Codex subagent, matiiss machine | last seen 2026-11-21 07:50 | B-46
- lead-w3 | Codex subagent, matiiss machine | last seen 2026-11-21 07:53 | B-64
- lead-monitor | Claude subagent, matiiss machine | last seen 2026-11-21 06:26 | stopped (operator usage limit)
- Derek-Codex-A | Danylos/Windows, Codex background worker | last seen 2026-11-21 08:02 | B-74 live validation done, independent review
- Codex (Derek Windows) | Windows/Codex | last seen 2026-11-21 08:02 | B-52 multi-step document downloads
- Codex (Derek Windows B-42) | Windows/Codex subagent | last seen 2026-11-21 07:10 | idle after B-42 validation
- Codex (Dani146 Windows) | Codex, Dani146's Windows machine | last seen 2026-11-21 05:15 | left Refresh operation
- Codex (Johanna Mac) | Codex, Johanna’s Mac | last seen 2026-11-21 07:55 | B-60, B-50 and B-28 in review

- Codex bulk-export (Johanna Mac) | Codex, Johanna Mac isolated worktrees | last seen 2026-11-21 04:36 | appears dead (B-03 reopened)

### B-01 Document URLs built from tender fields
- **Priority:** high (documents are the core feature)
- **Status:** done (4f3751a)
- **Problem:** TenderNed (https://www.tenderned.nl/) attaches through its own JSON API
  (`/papi/tenderned-rs-tns/v2/publicaties?page=0&size=50`, 150 tenders in a test), but documents are
  at `/papi/tenderned-rs-tns/v2/publicaties/{publicatieId}/pdf` and a recipe can't build that URL.
- **Idea:** a declarative URL template in the recipe, e.g. `"documents": {"template": ".../{id}/pdf"}`,
  filled from mapped fields. Also check whether that PDF is the notice or the actual tender documents.
- **Test with:** TenderNed.
- **Last seen:** 2026-11-21 04:46
- **Findings:** Generic, no portal rules. Recipe gains `detail.url` and `documents_url: {template, kind}`
  (placeholders = mapped field names, URL-quoted; host must be literal, no `//`), and JSON `attribute`
  may be a dot path (`links.download.href`). `documents_url` + `documents` reads file links from the
  templated page; without `documents` the templated URL is the file. Errors/blocks on that page set
  `_documentPageError` / `_documentBlock` like documents_by_host. test_recipe now probes per-tender JSON
  document lists by REST convention (`<json listing path>/<id>/<name>`, JSON detail URL, sibling of a
  single-file documents_url; names documents/attachments/files/dokumente/documenten/documentos/documenti),
  cached per run, restores `fetcher.blocks` on a probe 403, and hands the agent a ready template.
  Reviewed by 2 subagents (6 + 3 findings, all fixed). 51 tests + ruff pass.
  Real runs (fresh DB, unassisted): run 1-2 serving ($0.04/$0.05), files delivered, but only the notice
  PDF (`/{id}/pdf`); the actual tender files are in `/publicaties/{id}/documenten` (loads only when the
  Documents tab is clicked, so render_page never shows it). Run 3 (probe only on single-file recipes)
  failed: agent gave up. Runs 4-5 (generalized probe) failed before reaching the API: render_page got
  an empty Angular shell (B-21). So the probe is unit-tested but not yet confirmed live on TenderNed;
  retest after B-21. No second JSON-API portal tried yet. Spend: about $0.21 over 5 attaches.

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
- **Status:** in review (PR #8, fresh attach validation pending)
- **Problem:** oeffentlichevergabe.de's documented open-data API
  (`/documentation/api/opendata`, OpenAPI 3.1) only offers daily or monthly ZIP exports of eForms XML,
  and its documents live on the platforms the notices link to.
- **Idea:** a `bulk_export` recipe kind (download ZIP, parse eForms XML into tenders) plus following each
  notice's link to its hosting platform.
- **Findings:** Generic daily eForms ZIP reader, links taken from XML, host selectors use the existing document workflow. Two reviewers and 48 tests passed. The original bounded public archive smoke test parsed 693 competition notices; it was not an unassisted attach. The PR remains in review until fresh validation.

  Updated PR #8 at 83c49d0 to resolve the current main conflicts. POST listing validation, GET-only
  export mapping, and document integrity checks are all preserved. Conflict resolution reviewed,
  all 166 integrated backend tests and Ruff passed. No paid live attach was run.

### B-04 Read public Düsseldorf NetServer document lists
- **Priority:** high (documents are the core feature)
- **Status:** open
- **Last seen:** 2026-11-21 08:00
- **Problem:** NRW notice 1138705 links to Düsseldorf NetServer. Public document links appear only
  after opening the versioned file-list dialog.
- **Findings:** The NetServer option opens the latest public file-list version and reads its
  Lead: Fresh URL-only NRW attach on reviewed 379489f, max details 3: failed with 0 tenders / 0 files, 13 model calls / USD 0.0576. Listing was readable, but the agent could not validate a document mapping; no successful Refresh or Düsseldorf attachment verified.
  newly displayed links. The live NRW sample produced 25 links and a verified 102,316-byte PDF.
  Failed dialogs retain earlier files and allow other tenders to finish. Reviewed by the Codex
  browser guard and recipe reviewers. All 19 backend tests and Ruff passed.
  A fresh NRW attach reached the public listing but gave up validating files across buyer hosts
  after 19 calls (estimated USD 0.099). A stored-recipe Refresh then delivered 232 files and served
  the verified PDF through the API with zero model calls. Public POST search is B-18 and automatic
  NRW attachment is B-20.
  Code is on main at 4665b28a. Reopened under the revised protocol for an unassisted attach
  that saves real files on a previously untested NetServer portal.
  Fresh URL-only attaches from Bremen and Baden-Wuerttemberg, started before the Dev-list update, reached public listings but saved no files. Bremen had no detail hrefs and exposes them through its own read-only row lookup (B-30). NRW remains the applicable Dev portal and is blocked on mixed-platform discovery (B-20).
  Reviewed code is pushed on branch b-04-unassisted-validate (26e9873 plus main c78a645); 173 backend tests and Ruff pass. A 360-second fresh attach did not finish, and the next fresh attach failed before model calls because the API account reports no credits. B-04 is open pending funded live validation.

### B-05 Prompt-injection hardening
- **Status:** in review (PR #17, defensive prompt guidance)
- **Problem:** recipes are data, so no code runs, but page text could still steer the agent: wrong pages,
  fetching unrelated URLs. `give_up(blocked=true)` is already refused unless a block was detected.
- **Idea:** require listing and detail URLs to share the portal's site (documents may be elsewhere);
  review what tool outputs pass to the model.
- **Findings:** PR #17 explicitly treats website-derived tool results and automatic discovery as untrusted evidence. Fake-model test verifies evidence stays outside the system message. Existing deterministic access guards are unchanged; follow-up review of tool scoping may still be useful. Independent review, all 166 tests, and Ruff passed.

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
- **Priority:** medium (operator request)
- **Status:** open
- **Problem:** `watchtower.*` logs show every step but only on the server console.
- **Idea:** store run-scoped log lines (a `run_log` table or similar) and stream them to the dashboard (SSE).
- **Scope (operator):** live streaming in the dashboard of every step while a portal is attached (agent tool calls and results), read (pages, documents, counts) and healed/repaired, so an operator can watch the system work. Check the frontend first: the dashboard currently shows run reasons and model usage after a run, but no live stream.
- **Findings:**

### B-09 Verify refresh and repair end to end
- **Priority:** high (documents are the core feature)
- **Status:** done (4d75b8a, c670f4e)
- **Last seen:** 2026-11-21 05:13
- **Problem:** `/refresh` and repair work in unit-level runs but haven't been exercised on a real portal
  that reached `serving`.
- **Test:** attach a portal until `serving`, refresh it, then break its stored recipe (edit `recipe.data`)
  and refresh to trigger `healing` and repair.
- **Findings:** Portal https://www.vergabe.metropoleruhr.de/ (Cosinex satellite; outside the Dev list, chosen before that rule). Run 1 (code before 4d75b8a): attach serving, 20 tenders, USD 0.054, but files were notice PDFs only (agent ignored the "Vergabeunterlagen" page) and no pagination; refresh serving with 0 model calls; recipe break (listing.records) -> healing -> repair serving, 400 tenders, USD 0.033 (repair also added next_page). Run 2 (fresh DB, unassisted, on 4d75b8a): attach serving, 20 tenders, 409 files / 53 MB, USD 0.10, agent used documents_page itself; refresh serving, 0 model calls; break -> healing -> repair serving, 400 tenders, 317 files, USD 0.032. Fixes: 57e9a50 (file names from `filename*`, were "%20"-encoded), a6faa27 (test_recipe warns when a tender page links to an unused documents page, multilingual keywords), c670f4e (B-29 dedupe). Filed B-26 (max_pages cap), B-27 (missing pagination not flagged), B-29. Not yet checked on an unseen Dev portal; refresh takes 3-10 min at 1 req/s because the site sometimes stalls 30 s on redirects.

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
  - https://www.vergabe.metropoleruhr.de/ (DE, Cosinex; scouted by lead-w2 for B-09, so not unseen)
- **Results** (one line per run: `date | portal | worker | state | tenders | files | cost | blocker`):
  - 2026-11-21 | Finland Hilma | Derek-Codex-B, B-43 + B-21 integration | failed | 0 | 0 | USD 0.1324 (22 calls) | Fresh URL-only attach found the public JSON listing. Sampled details did not yield procurement documents, so validation failed. No confirmed access block.
  - 2026-11-21 | TenderNed | Derek-Codex-B, B-21 reviewed | serving | 1000 | 158 distinct files | USD 0.0524 (13 calls) | Fresh URL-only attach and zero-call Refresh; same file count and sample SHA-256 after both. See B-49 for the count difference.
  - 2026-11-21 | udbud.dk | Derek-Codex-B, B-21 final | failed | 0 | 0 | USD 0.0733 (15 calls) | Public listing discovered; agent could not map external document links on the sampled notices. B-42 already covers this.
  - 2026-11-21 | TenderNed | Derek-Codex-B, B-21 cache integration | serving | 2000 | 158 distinct files | USD 0.0674 (15 calls) | fresh URL-only attach; Refresh preserved all 2000 tenders and 158 files with 0 calls. API document SHA-256 matched after both.
  - 2026-11-21 | NRW | Codex (Dani146 Windows) | failed | 0 | 0 | USD 0.0737 | Fresh attach from the public landing page produced no category-spanning recipe. Refresh was unavailable.
  - 2026-11-21 | NRW | Codex (Derek Windows) | serving | 20 | 162 (54 Office) | USD 0.0797 attach, USD 0 Refresh | Fresh attach from the Dev URL with B-20 category hints. Refresh retained all files. Agent dropped its wider tested index for an unpaginated recipe.
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.0901 | Capped validation saw 40 open rows but sampled zero file links. Refresh was unavailable.
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.1264 | The agent saw category and document-link hints, then submitted selectors matching zero rows. Refresh was unavailable.
  - 2026-11-21 | DTVP | Codex (Derek Windows) | failed | 0 | 0 | USD 0.1393 | Agent matched no tender rows from the Dev URL. It suspected authentication, which was not independently confirmed. Refresh was unavailable.
  - 2026-11-21 | DTVP | Codex (Dani146 Windows) | failed | 0 | 0 | USD 0.0692 | Fresh URL-only attach could not find a complete listing. Invalid selectors and an empty sample preceded failure.
  - 2026-11-21 | Federal procurement UI | Codex (Dani146 Windows) | failed | 0 | 0 | USD 0.0459 | The public listing was unresolved. The agent received HTTP 406 from the export request and reported login required on other API routes.
  - 2026-11-21 | Irish eTenders | Codex (Dani146 Windows) | failed | 0 | 0 | USD 0.0412 | The recipe test read 30 tenders but found no public file links. Attach did not pass, so Refresh was unavailable.
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.1604 | A recipe proposed a linked document page without a file selector; its fallback matched zero rows.
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.0968 | Indexed validation found 37 open tenders but zero document links; the agent stopped at the public file modal.
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.0409 | After the modal fix, the fresh run stopped earlier: the recipe selected a JavaScript placeholder instead of the literal public pagination URL visible in its handler.
  - 2026-11-21 | boamp.fr | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | reported captcha on pages + dataset API disallowed by robots
  - 2026-11-21 | contrataciondelestado.es | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | robots.txt disallowed all tried paths
  - 2026-11-21 | find-tender.service.gov.uk | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | JS-heavy search; no list endpoint found
  - 2026-11-21 | etenders.gov.ie | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | listing readable; no document links on sampled tenders
  - 2026-11-21 | doffin.no | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | render returned an empty shell; script API paths 404
  - 2026-11-21 | hankintailmoitukset.fi | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | render returned an empty shell; only CMS API found
  - 2026-11-21 | ezamowienia.gov.pl | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | render returned an empty app-root
  - 2026-11-21 | dtvp.de | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | HTML archive found, agent gave up on completeness
  - 2026-11-21 | udbud.dk | lead (wide test, main 533643e) | failed | 0 | 0 | ~$0.01-0.09 | render returned an empty #app
  - 2026-11-21 | Irish eTenders | Codex (Derek Windows) | failed | 0 | 0 | USD 0.0550 | Agent found 10 open rows. It proposed per-row notice PDFs, but no actual tender files were validated. No document controls were observed on the two public detail pages inspected.
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.0328 | Fresh attach of cb2a19f stopped after nine calls before testing any recipe. It had seen category index hints. This is the B-33 early give-up pattern.
  - 2026-11-21 | Doffin | Codex (Dani146 Windows) | failed | 0 | 0 | USD 0.0688 | Render loaded notice data. Fresh attach could not map a complete public listing and document recipe.
  - 2026-11-21 | Finland Hilma | Codex (Dani146 Windows) | failed | 0 | 0 | USD 0.0504 | The tested 19-record API candidate lacked tender titles and file links. Refresh was unavailable.
  - 2026-11-21 | boamp.fr | lead-w1 | failed | 0 | 0 | USD 0.0427 | captcha false positive fixed (B-34); API correctly disallowed by robots.txt on www.boamp.fr and its open-data host
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.1284 | Fresh B-33 branch on current main: readable list, 23 calls, agent could not validate public buyer-platform file modal.
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.0883 | Fresh combined B-20/B-33 trial: readable list, 16 calls, still declined document mapping on public NetServer modal.
  - 2026-11-21 | Find a Tender (UK) | lead-w2 | failed | 0 | 0 | USD 0.0199 | Site answered very slowly (curl: 36 s to first byte, 79 s for the results page, 40 s even for a 404 robots.txt); our fixed 30 s read timeout plus one retry turned every listing fetch into ReadTimeout, plus 2 DNS failures. Agent gave up after 11 calls. Taken up in B-23.
  - 2026-11-21 | NRW | Codex (Derek Windows) | serving | 1449 | 153 | USD 0.0596 attach, USD 0 Refresh | Fresh combined B-20/B-33 URL-only attach on the Dev URL; three recipe tests, two submits, zero deferred give-ups. Refresh retained tenders/files with zero calls; file API bytes matched SHA-256.
  - 2026-11-21 | Find a Tender (UK) | lead-w2 | failed | 0 | 0 | USD 0.0728 | Retest with B-23 timeouts: slow pages (43-94 s) now load; one HTTP 429 after ~48 requests, then the agent gave up without testing a recipe (B-33).
  - 2026-11-21 | NRW | Codex (Derek Windows) | failed | 0 | 0 | USD 0.0684 | Fresh URL-only attach at 8f14869, 15 calls, final recipe document validation failed; Refresh unavailable.
  - Note (lead): Find a Tender notice pages answer a 118-byte `403 Forbidden` to our honest user agent, while the listing gives 200 and there is no robots.txt. That's bot filtering, not a login. Don't spoof user agents. The right route is its official public OCDS API (see B-39's hint: look for an official API when blocked).
  - 2026-11-21 | TenderNed | lead-w1 | serving | 1000 | 158 (10 sampled tenders), refresh kept 159 | USD 0.0452 attach, USD 0 Refresh | none for documents; award notices count as open (B-47)
  - 2026-11-21 | NRW | Codex (Derek Windows) | serving | 679 | 93 (18 Office) | USD 0.1019 attach, USD 0 Refresh | Fresh URL-only on eef301a; zero-call Refresh kept all files; sample SHA-256 verified.
  - 2026-11-21 | TenderNed | Codex (Derek Windows) | serving | 1000 | 159 (52 Office) | USD 0.0463 attach | Fresh URL-only on d125a58, 13 calls; sampled attachment hash verified.
- 2026-11-21 | Doffin | Codex (Derek Windows B-42) | failed | 0 | 0 | USD 0.0634 | Fresh URL-only on main acefd4c; listing not discovered (13 calls).
  - 2026-11-21 | Finland Hilma | Codex (Derek Windows B-42) | failed | 0 | 0 | USD 0.0634 | Fresh URL-only on main acefd4c; listing not discovered (16 calls).
  - 2026-11-21 | Irish eTenders | Codex (Derek Windows B-42) | serving | 10 | 6 | USD 0.0358 attach, USD 0 Refresh | All six files were public notice PDFs, not tender attachments; sample hash matched and Refresh retained six with 0 calls (B-50).
  - 2026-11-21 | Doffin | Derek-Codex-A | failed | 0 | 0 | USD 0.0738, 17 calls | Fresh eade7ae attach found the public listing, but six recipe tests could not map any document. No refresh.
  - 2026-11-21 | TenderNed | Derek-Codex-A | failed | 0 | 0 | USD 0.0583, 14 calls | Fresh eade7ae attach made three submissions. The model reported JSONDecodeError while validating its candidate; no refresh. This is another B-49 variability observation.
  - 2026-11-21 | Doffin | Derek-Codex-A | failed | 0 | 0 | USD 0.0871, 19 calls | Fresh JSON-hint build reached an external buyer restriction. No portal-wide restriction established.
  - 2026-11-21 | TenderNed | Derek-Codex-A | serving | 200 | 159 | USD 0.0568 attach, USD 0 refresh | Fresh 01703d8, URL only, 11 calls. Files across all 10 sampled tenders, including 52 Word or Excel files. Refresh retained 200/159 with zero model calls and a verified 227492-byte sample SHA-256.
  - 2026-11-21 | Doffin | Derek-Codex-A | serving, false file result | 984 attach / 993 stored after refresh | 3 attach / 6 refresh, zero verified procurement attachments | USD 0.0605, 14 attach calls; refresh zero | The selected direct document template pointed to notice-detail JSON. All 7 stored hashes decoded to notice objects; six were referenced after refresh. B-74.
  - 2026-11-21 | Doffin | Derek-Codex-A | failed | 0 | 0 | USD 0.0698, 15 calls | Fresh B-74, max three details. Public JSON listing found, no genuine attachment recipe. The earlier inline notice payload was separately verified to be rejected before storage.
- **Findings:**

### B-11 Cost per portal for the README
- **Priority:** high (mandatory README content: what one portal costs)
- **Status:** open
- **Problem:** the PDF wants the model cost of one portal in the README. Runs record it
  (`GET /portals/{id}/runs`); collect numbers across the test set.
- **Findings:**
  Derek-Codex-B, B-21 fresh runs on Windows (10 detail pages): two successful TenderNed attaches cost USD 0.0524 / 13 calls / 360 s and USD 0.0674 / 15 calls / 367 s; both subsequent reads took 31 s and zero model calls. Two failed attaches still incurred costs: Denmark USD 0.0733 / 15 calls / 129 s; Finland USD 0.1324 / 22 calls / 150 s. Saved run metadata, measured 2026-11-21; estimates are per attempt and exclude hosting.

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
- **Priority:** high (sweep 2: the main blocker for JS portals now that rendering works)
- **Status:** done (04f7cd5)
- **Problem:** On https://www.oeffentlichevergabe.de/, the search page returns only region data
  before a search action. Listing recipes currently use GET and cannot read a public POST search.
- **Idea:** Verify the site's unfiltered public search and add narrowly scoped listing support.
  Bulk exports are tracked under B-03.
- **Evidence (sweep 2, main 46e634a):** renders now load real data (Doffin rendered in 15 s with 4 JSON responses), but Doffin's tender data comes from `POST https://api.doffin.no/webclient/api/v2/search-api/search`; recipes only do GET, so the agent can't use it. Several other JS portals look similar. Needs a declarative POST JSON body (and paging inside the body) for json listings, validated against the request the page itself made (render_page could report method + body of captured JSON calls).
- **Findings:** Listings of kind json can use `method: POST` + `body`, with page_param as a dot path in the body. Only requests the site's own page made can be repeated: same site, JSON 2xx, same keys and non-number values (numbers such as page and size may change, bools are strict), no sensitive keys, at most 8 KB. `fetch_json` can explore them, and `render_page` lists POST calls first with requestBody. A 4xx on a later page ends paging (listing incomplete) instead of failing. On Doffin (fresh DB, unassisted) the agent built a POST recipe that was accepted and the read paged 11 x 100 tenders; documents are B-42. Reviewed by a security and a correctness reviewer, then a follow-up review.

### B-20 Attach NRW automatically across buyer platforms
- **Priority:** high (public document delivery on a mixed-platform portal)
- **Status:** done (acefd4c)
- **Last seen:** 2026-11-21 06:49
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
  Generic pagination hints and upcoming-deadline sampling shipped in 7bccb54d and bd70ed7f
  (main 533643e5). Two reviewers, 47 integrated backend tests and Ruff passed. Fresh URL-only
  checks with the current validator still failed on NRW and unseen Rheinland-Pfalz. Both agents
  stopped while trying to cover the category groups, before a document recipe passed. The
  Rheinland-Pfalz trace also showed repeated recipe validation errors. Both runs started before
  the Dev-list-only update. A reusable category index hint could expose repeated same-site links
  in the page tool alongside the existing pagination hint.
  The category-index hint and cross-category validation budget produced the first successful NRW URL-only attach in this investigation. It delivered 162 files and Refresh retained them, but the model submitted a 20-notice unpaginated fallback after an indexed candidate passed validation on 37 notices. A fresh retest is running with a coverage comparison that counts open tenders.
  Offline reproduction confirmed that capped validation can fetch listing pages from two categories yet use all three detail requests on the first. It rejects otherwise working recipes when only the second category exposes files. Fixing detail sampling across categories before the next clean attach.
  The reviewed local integration with B-01 (a65c274) passes 62 backend tests and Ruff. It detects the public NetServer file-list marker and falls back to the existing guarded modal reader when the HTML selector produces no files. An automatic modal denial stays local to its tender. A live run failed earlier in pagination after selecting the visible JavaScript placeholder. The next revision will reuse the strict literal navigation parser at runtime.
  Reviewed work is published on branch b-20-category-index at cb2a19f, separate from main. A selected JavaScript no-op now follows a path only when the shared literal parser validates it; an unresolved next control leaves the scan incomplete. All 73 backend tests and Ruff passed. Final fresh NRW attach gave up before trying a recipe, the same B-33 failure seen by the wider test. The earlier 162-file attach is still the only working integrated NRW evidence from this series.
  Derek's local b-20-delivery branch is preserved at 8f14869, integrated with main 72e313c. A reviewer found and we fixed a cross-category capped-validation gap when a category lacks deadlines; two reviewers approved the final fix, and 106 backend tests plus Ruff passed through uv. A fresh final-branch NRW attach was interrupted at operator request before completion, so its result is unverified. Earlier combined B-20/B-33 trial served 1449 tenders/153 files and kept them on zero-call Refresh, but was before the final review fix.
  Fresh URL-only NRW attach at 8f14869, with new database and file store, failed after 95 seconds. 15 model calls, USD 0.0684, zero tenders and files. Two recipe tests and two submissions did not yield a valid recipe. Last validation reported a document problem. Refresh was unavailable. Branch remains unmerged.
  Reviewed delivery branch is published at d125a58, integrated with main 7860ff2. Fresh NRW at eef301a accepted buyer document mapping and served 679 tenders and 93 files (18 Office) for USD 0.1019. Refresh retained all with zero model calls and the sampled file API SHA-256 still matched. Fixes recover stale HTML detail templates and resolve guarded literal pagination paths. Final integration passed 157 tests and Ruff. TenderNed validated real files but timed out during a six-minute full read; standard-timeout retest in progress.
  Separate fresh URL-only TenderNed check on d125a58 reached serving with 1,000 tenders and 159 files (52 Office) after 361 seconds, 13 calls, USD 0.0463. Sample non-PDF attachment SHA-256 matched. Merged into main at acefd4c after 165 tests, Ruff, and a focused coverage-message check. The live runs used the ten-detail cap.

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
- **Status:** reviewed locally by Derek-Codex-B, awaiting operator authorization to publish the implementation
- **Last seen:** 2026-11-21 08:09
- **Problem:** TenderNed's Angular app loads dozens of script chunks; a render took about 2 minutes and the first try hit the old 90 s timeout. The asset cache (`fetcher.assets`) only lives for one run, so every attach and repair starts cold. oeffentlichevergabe.de's app also failed to boot once because its main script timed out.
- **Idea:** Persist the script/CSS cache per site across runs (disk, keyed by URL + ETag); measure render times on 2-3 JS portals; consider skipping clearly irrelevant third-party scripts.
- **Findings:**
  2026-11-21 07:12 Derek-Codex-B: follow-up review passed (Derek-Codex-A); 184 tests and Ruff green. URL-only TenderNed attached and refreshed with 2000 tenders / 158 files and a verified hash (B-10). Denmark fresh attach in flight. Delivery of the reviewed local implementation is awaiting operator authorization.
  A cold TenderNed render on the reviewed code took 97 s (740122 HTML chars, 2 JSON calls). 90 browser asset responses had ETag and Last-Modified without max-age or Expires; the script scan carried cookies. No cross-run cache hits there. Denmark exposed document links beyond its working public list that the agent could not map (B-42).
  Final TenderNed run: all 158 stored hashes independently matched. 104 files begin with a PDF signature, 52 are Office Open XML packages (32 DOCX and 20 XLSX), and two are markup files (.nsx/.rsx).
  Derek-Codex-B: integration snapshot 8c13b3a (review/Derek-Codex-B/B-21), 180 tests and Ruff pass. Finland's cold render after a script scan took 8.4 s / 2 static GETs versus 19.5 s / 13 on main with the same DOM length and JSON count. Denmark's fresh-Fetcher repeat scan took 3.5 s / 1 script GET versus its cold 26.6 s / 25 with identical API hints. Public immutable assets that vary on CORS headers reuse only the absent-header variant. Full fresh attach/refresh validation in progress.
  Reviewed feature branch b-21-browser-assets (66e987ba): a bounded process-local cache shares
  explicitly fresh anonymous same-origin scripts and styles across new Fetchers. Two reviewers,
  59 backend tests and Ruff passed. On the federal procurement UI, a repeat scan requested one
  script instead of 25 and took 8.6 s versus 26.5 s with identical API hints. A repeat browser render
  reached the application DOM in 4.2 s with one static GET. TenderNed made 91 static GETs and took
  93.7 s with or without the change. Its scripts lack explicit freshness, so it saved zero shared
  entries. Fresh URL-only DTVP attach stopped before listing discovery and downloaded zero files.
  Fresh attaches on the federal UI and on Irish eTenders also saved zero files. The federal
  agent did not obtain a usable public listing from its API requests. The Irish recipe read a
  30-notice sample and could not map a public document source. The commit remains on its feature
  branch, and B-21 is open for the unassisted file-delivery check and TenderNed cold-start work.
  lead-w1 2026-11-21 (released, nothing pushed): merging main into b-21-browser-assets conflicts in fetch.py
  (keep both imports; take main's retry loop in `get` and pass `headers=headers` to `client.get`; keep both
  `script_text` and `post_json`), browser.py (import `site` too; replace the branch's two
  `wait_turn` + `route.fetch(max_redirects=0, headers=headers)` with main's `fetch_route(route)`, extended
  to pass `headers`, so the per-request timeout from B-32 stays) and tests/test_browser.py. The cache only
  helps a later run in the same process (refresh, repair, re-attach); the first cold render is unchanged,
  so measure by rendering each portal twice with two Fetchers in one process, main vs branch (no model
  cost).

### B-22 Agent can submit a recipe whose documents match nothing
- **Priority:** high (portals end up serving without files)
- **Status:** done (09092be)
- **Problem:** `test_recipe` only warns "no document links found in the sample"; the agent may submit again to accept the warning. TenderNed and evergabe.nrw.de both reached `serving` with 0 files this way.
- **Idea:** Make a working sample document download a hard requirement for `submit_recipe`, unless a login/captcha block on documents was actually detected (then fail with `Blocked: documents ...`). Keep an explicit escape only for portals that publish no files at all.
- **Findings:** `_check_documents` now returns a hard problem when no sampled tender yields any document link, so `submit_recipe` is refused; partial samples still pass. Reviewed by a review subagent (caveat: give_up(blocked=true) is refused without a detected block, so the message now says to use it only after a detected login/captcha). Behavior change: a portal whose sampled tenders genuinely have no files now ends `failed` instead of `serving` without files.

### B-23 Transient network errors abort whole runs
- **Priority:** medium
- **Status:** done (6c652c1, 2f0da22, 890ebee)
- **Last seen:** 2026-11-21 05:58
- **Problem:** On a flaky network single DNS timeouts or connection resets (httpx, Playwright route.fetch, OpenAI) fail the attach; there is only one retry for GETs and five for OpenAI.
- **Idea:** Retry with backoff (2-3 tries) for listing and detail GETs and browser subresources; count retries against the rate limit; log them.
- **Findings:** On main (9db64f7): 6c652c1 `Fetcher.get` makes 3 attempts with 2 s/5 s backoff on transport errors, DNS failures (raw `socket.gaierror` from our public-address hook) and 502/503/504; read timeout 120 s (`WATCHTOWER_HTTP_TIMEOUT`). 2f0da22 a stored-recipe read that fails on such an error (or 429) keeps the portal state with "temporary error ... retry later" and starts no repair; after 3 failed reads in a row the portal is `failed` as unreachable. Two reviewers each. Not covered: downloads (a failed file already leaves the tender pending for the next refresh), robots.txt fetch (not retried, cached per run), browser recipes (Playwright net::ERR_* still trigger a repair; browser code belongs to B-21/B-32), OpenAI (5 retries already). Find a Tender retest (fresh DB): pages that took 43-94 s now load, but the attach still failed (16 calls, USD 0.07) after a single 429; the agent gave up without running test_recipe (B-33 territory). Next: 429 with Retry-After backoff. Done in 890ebee: HTTP 429 is retried too, waiting for a numeric Retry-After (capped at 60 s, else 15 s), and every retry wait pushes the site's rate-limit gate forward, so all requests to that site (httpx, browser routes, TLS fallback) back off together. Reason after 3 temporary failures in a row: "Failing on temporary errors for 3 reads in a row". Two reviewers each for all three commits. No unseen-portal run of the 429 path yet (the UK 429 was not reproducible).

### B-24 Browser subresources skip robots.txt
- **Priority:** low
- **Status:** done (7860ff2, merged PR #14 by lead)
- **Problem:** `browser.render` checks robots.txt for the page URL only; scripts and XHRs it loads are fetched without a robots check.
- **Idea:** Check `fetcher.allowed()` for same-site XHR/fetch requests in the route handler.
- **Findings:** PR #14 enforces cached robots rules for intercepted same-origin browser Fetch/XHR before cache or network. Chromium proves allowed data still yields a file link while the denied request never reaches the fixture. Two reviews, all 99 backend tests and Ruff pass. Broader cross-origin and service worker coverage is unchanged.

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
- **Findings:** Recognizes Office ZIP packages by their internal entries and stores them intact. Ordinary ZIP bundles still unpack, and existing cache records with Office internals are rechecked. Two Codex reviewers approved. All 45 backend tests and Ruff passed after integrating current main. Live NRW notice 1138704 recovered from 51 old entries to five files, including an intact 11,749,325-byte DOCX verified via the file API. Fresh unassisted attach from the previously unused Niedersachsen public listings page (https://vergabe.niedersachsen.de/Satellite/company/welcome.do) reached serving, read 400 tenders, and delivered 10 open-tender files for USD 0.0777. A following zero-model-call Refresh also served 10 files. That sample contained no Office files. The bare-domain redirect failure is tracked in B-28.

### B-26 Listings silently capped at max_pages
- **Priority:** medium (tenders beyond the cap never get documents)
- **Status:** done (791657e, merged PR #12 by lead after resolving conflicts with B-40)
- **Problem:** `Recipe.max_pages` defaults to 20 and the agent leaves it. On https://www.vergabe.metropoleruhr.de/
  (20 rows per page, about 41 pages) a repaired recipe read 400 tenders and stopped; `complete` is false,
  but no run reason or test warning says the listing was cut.
- **Idea:** report "listing stopped at max_pages" in test_recipe and the run reason; raise the default or
  let the agent set it from the page count it sees.
- **Findings:** PR #12 reports incomplete listing coverage in recipe validation and run/portal reasons. Page limits and preservation of older records stay intact. Two reviewers, 84 backend tests and Ruff passed. No fresh real-portal attach was run.

### B-27 test_recipe doesn't flag a listing without pagination
- **Priority:** medium
- **Status:** done (791657e, merged PR #11 by lead after resolving conflicts with B-40)
- **Problem:** the first attach of https://www.vergabe.metropoleruhr.de/ submitted a recipe with no
  next_page/page_param although the list page has a forward link (`a.browseForward`, page links with a
  numeric parameter): 20 of ~800 tenders. A later repair found the link by itself.
- **Idea:** generic check in test_recipe: when paging is unset and the first list page has a link whose
  text or class means next (multilingual keywords) or whose URL differs from the listing URL only by a
  numeric parameter, warn.
- **Findings:** PR #11 warns on ordinary same-origin next links or advancing numeric paging parameters in loaded listings without paging configured. No additional requests. Two reviewers, 86 backend tests and Ruff passed. No fresh real-portal attach was run.

### B-28 Follow HTML refresh redirects during initial portal discovery
- **Priority:** high (a public portal address can stop before listing discovery)
- **Status:** in review (PR #9)
- **Problem:** A fresh attach of https://vergabe.niedersachsen.de/ failed before finding a listing. The homepage returns HTTP 200 with an 89-byte HTML document and no links. Its only navigation is a standard HTML meta refresh to the procurement application. The agent spent 16 calls and $0.0234 without reaching the listings.
- **Idea:** Recognize HTML refresh redirects generically, with a bounded chain and the existing URL, rate-limit, robots and access checks. Verify a fresh unassisted attach from a homepage using that mechanism.
- **Findings:** PR #9 follows standard meta refresh with explicit-hop robots/public URL/rate/access checks, bounded hops and delay, and cycle detection. Two Codex reviewers, 62 offline tests including Chromium, and Ruff passed. Fresh unassisted real-portal attach remains to be verified.
  2026-11-21 07:51 Codex (Johanna Mac): PR #9 reconciled with main at e9f696d. Per-hop robots, throttling, retry and access checks preserved. Derek-Codex-B and Codex independent review approved; 177 offline tests and Ruff passed.

### B-29 Same file listed twice per tender (archive ZIP plus single files)
- **Priority:** high (document lists are wrong)
- **Status:** done (c670f4e)
- **Last seen:** 2026-11-21 04:55
- **Problem:** documents pages often offer every file and a ZIP of all of them. Both match the file
  selector, the ZIP is unpacked, and the tender lists each file twice (unassisted attach of
  https://www.vergabe.metropoleruhr.de/: 18 entries, 9 unique sha256).
- **Idea:** dedupe a tender's documents by sha256 in `fetch_documents`.
- **Findings:** `fetch_documents` keeps the first entry per sha256, so files that also come from an unpacked archive are listed once (unit test with a single PDF plus a ZIP containing it). One review subagent: ok. Old cached tenders keep their duplicates until re-fetched. Generality check on a Dev portal pending (contrataciondelestado.es next).

### B-30 Discover detail destinations for NetServer clickable tender rows
- **Priority:** high (blocks tender documents from public NetServer listings)
- **Status:** open (awaiting lead decision on a Dev NetServer listing or sanitized prior capture)
- **Last seen:** 2026-11-21 07:14
- **Problem:** Fresh URL-only attaches of the public Bremen and Baden-Württemberg NetServer portals found open tender rows but saved zero files. Bremen supplies tender IDs and categories in row attributes, with no detail href. Its own handler sends a read-only lookup for the detail URL before navigating. The existing script scan did not expose that navigation.
- **Idea:** Detect the reusable NetServer markup and resolve public detail destinations through a narrowly scoped read-only mechanism. This complements B-04 file-dialog support, which takes effect only after the detail destination has been resolved.
- **Findings:**

### B-31 Experiment: more agent reasoning, less special-case code
- **Priority:** high (generalization)
- **Status:** done (experiment, nothing merged: main's prompt kept)
- **Last seen:** 2026-11-21 05:55
- **Problem:** Most successes so far come from code mechanisms built for one software family (NetServer,
  Cosinex), and the prompt now carries software-specific instructions. That may not generalize to unseen
  portals.
- **Idea:** On a branch, try an agent that gets a wider, longer prompt (general strategies for finding
  listings, detail pages and document files, worked examples in the abstract) and more turns, but no new
  site- or software-specific code. Compare it with `main` on the same Dev portals with fresh databases:
  portals reaching `serving` with files, files per portal, and model cost per attach. Report the numbers
  and keep whichever generalizes better.
- **Findings:** A/B on main 1dbf27d, fresh DB per phase, same 5 Dev portals attached in parallel
  (TenderNed, NRW, DTVP, etenders.gov.ie, find-tender), max details 10. Variant B (`WATCHTOWER_PROMPT=wide`):
  main's strategy section replaced by a general playbook (map the site, rank candidates, inspect 2-3
  tenders, test early, retry other candidates, give up only after several failed tests, abstract recipe
  examples), NetServer lines removed, MAX_TURNS 70 instead of 40; recipe docs identical.
  - A (main): 0/5 serving, 0 files, 84 model calls, $0.355 (TenderNed 26, NRW 11, DTVP 17, IE 13, UK 17).
  - B (wide): 0/5 serving, 0 files, 75 model calls, $0.398 (TenderNed 17, NRW 12, DTVP 26, IE 12, UK 8).
  The wider prompt and turn budget changed nothing measurable: the agent still calls give_up after 8-26
  calls, so extra turns are never used. That is B-33's job (refuse early give_up); re-run this A/B after
  B-33 lands (patch kept locally, easy to redo). Generic causes seen in both phases: TenderNed found its
  per-tender JSON file list by itself in both (B-01 probe + documents_url) and failed only on
  `$.documenten[*].links.download.href` syntax, now B-40; IE and NRW never reached file links on detail
  pages; find-tender answered 403 on notice pages in A and 429 in B (B ran right after A on the same
  site, so B's UK result is confounded).

### B-32 Rendering returns before JavaScript apps load their data
- **Priority:** high (dominant failure in the wide test)
- **Status:** done (46e634a, merged PR #10 after a fresh review)
- **Problem:** Wide test (fresh DBs, 14 portals): at least 7 JS portals rendered as empty shells (`<div id="root"></div>`, `<app-root></app-root>`, empty `#app`), and 0 JSON responses were captured. 38 browser requests were aborted: 21 because the page or context was already closed, 7 on Route.fetch 30 s timeouts, 9 on network errors. `render()` waits for `load` + 2 s, but with one request per second per site, the app's script chunks and data XHRs arrive much later.
- **Idea:** Wait until the page settles: no pending routed requests for about 3 s and the DOM stops growing, with a total cap (the render timeout). Raise the per-request Route.fetch timeout. Log render time and the number of requests. Coordinate with B-21 (render speed).
- **Findings:**
  Reviewed feature branch b-32-render-readiness (5530afe8). Ordinary renders wait for request
  and DOM stability with a shared timeout. Browser recipes reject incomplete renders so a partial
  listing cannot close saved tenders. Two reviewers, 54 backend tests and Ruff passed. Doffin's
  old renderer returned an empty mount with 0 links and 1 JSON response in 6.6 s. The new renderer
  captured 38 links and 4 JSON responses in 11.5 s. Fresh URL-only attaches on Doffin and Finland
  still saved zero files. Doffin could not build a listing-and-document recipe from the public
  API paths. Finland's notice candidate lacked tender titles and files. The patch remains on
  its branch and the item is open. Dani146 stopped Refresh work at the operator's request.

  Integrated without changes on 1dbf27d in PR #10. All 79 backend tests and Ruff passed.
  The original end-to-end document-delivery caveat still applies.

### B-33 Agent gives up too early
- **Priority:** high
- **Status:** done (72e313c)
- **Last seen:** 2026-11-21 05:43
- **Problem:** Wide test: across 14 portals the agent called test_recipe only 6 times and never submit_recipe. It spent $0.47 of a $14 budget, giving up after 5-19 calls with reasons like "without guessing" or "within the tool constraints".
- **Idea:** Refuse give_up(blocked=false) until the agent has made a real attempt, e.g. at least one test_recipe and a minimum share of its turns or budget. Prompt: encourage trying candidate recipes and listing pages; it's cheap. Add a unit test with a fake model.
- **Findings:** Derek Windows local branch b-33-prevent-early-give-up at 29a5a72 has a reviewed early-exit guard. It counts actual validation reports, preserves confirmed access blocks and accepted recipes, and stays within the turn and cost budgets. All 56 tests and Ruff passed. A fresh NRW run made two recipe attempts but extracted zero tenders and failed after 19 model calls (USD 0.0986). Local combined NRW trial b-33-nrw-trial at 119146e preserves B-20 and B-33 and passed 78 tests. No live result was confirmed for that combination or for the UK check before stopping.
  Fresh uv-tested branch on current main: 57 tests and Ruff passed. NRW URL-only attach made 23 calls (USD 0.1284), but no files; combined B-20/B-33 trial passed 79 tests and Ruff, then gave up after 16 calls (USD 0.0883) on the NetServer document modal. Both used new empty stores and the ten-detail limit. Next: count validation attempts and refine the agent decision.
  Branch b-33-prevent-early-give-up is published at 11cb6bc, merged with main 46e634a for the lead to merge separately; 83 tests and Ruff pass via uv. A fresh combined B-20/B-33 NRW attach reached serving (1449 tenders, 153 files, 14 calls, USD 0.0596); Refresh kept the same counts with zero calls, and /files returned a SHA-256-verified file. That successful run made three recipe tests and did not exercise the B-33 deferral, so its direct live effect remains unmeasured.
  The reviewed guard was merged separately into main at 72e313c after 83 post-merge backend tests and Ruff passed through uv. It keeps confirmed block and accepted-recipe exits fast while deferring ordinary untested early exits within the existing turn and cost caps. The successful fresh NRW integration did not exercise a deferral, so broad live impact still needs the lead's wide sweep.

### B-34 Check block decisions for false positives
- **Priority:** medium
- **Status:** done (1dbf27d)
- **Last seen:** 2026-11-21 05:25
- **Problem:** Wide test: one Dev portal was reported as captcha-blocked although its documented open-data API is public; another as robots.txt-disallowed on every path. Either may be correct, but a false block is fatal.
- **Idea:** For each reported block, log the exact trigger (status, marker, robots rule). Check the captcha markers against pages that merely embed a captcha script, and the robots evaluation against the real file (user agent, wildcards). Fix generically and add unit tests.
- **Findings:** Checked both reported blocks against the live sites. boamp.fr's "captcha" was false:
  its JS app shell (<1500 chars of text) has `RECAPTCHA_PUBLIC_KEY` and "wrong captcha" translations in
  script config, and the old check matched the raw substring. Now a captcha needs a visible widget
  (.g-recaptcha/.h-captcha not invisible, .cf-turnstile, Cloudflare challenge ids, hCaptcha/reCAPTCHA
  challenge iframes, DataDome captcha-delivery) or challenge wording in visible text (the "protected by
  reCAPTCHA" notice is ignored); AWS WAF challenge header added; a login form needs an
  input[type=password] element. Reasons name the trigger, e.g. `captcha (div g-recaptcha)`, and every
  block is logged with its URL. robots.txt: replaced urllib.robotparser (first-match order, no `*`/`$`)
  with RFC 9309 matching: exact product-token group, longest match, Allow wins ties, wildcards via a
  linear matcher (no regex, no ReDoS), BOM/CRLF, percent-encoding normalized; robots.txt served as an
  HTML page counts as none. The robots blocks themselves were correct: contrataciondelestado.es has
  `Disallow: /` for all, boamp.fr and its open-data host disallow `/api/`. Two reviewers (captcha: 5
  findings; robots: 6), all fixed; 77 tests. Unassisted boamp.fr attach after the fix: no captcha
  block, fails on the (correct) robots rule, $0.043.

### B-35 Servers with an incomplete TLS certificate chain
- **Priority:** medium
- **Status:** done (0d58a41)
- **Problem:** Wide test: one portal failed on every request with SSL certificate verification errors; browsers usually fill in missing intermediates.
- **Idea:** Fill in a missing intermediate via the certificate's AIA URL (fetched once, cached, verified), or another standard approach. Keep verification on; never disable it.
- **Findings:** New `app/tls.py`: `ChainFillingTransport` (default httpx transport of `Fetcher`) catches "unable to get local issuer certificate", reads the leaf's AIA caIssuers URL, downloads the issuer (no redirects, 64 KiB cap, public-address check, rate limit), requires each step to be directly issued, and adds only certs on a path that verifies against the certifi roots for the hostname (cryptography.x509.verification). Verification is never disabled. Verified live on a portal with an incomplete chain (robots.txt 200 after one intermediate). Reviewed before commit by a security reviewer (found a trust-anchor poisoning hole and an off-path extra cert; both fixed) and a correctness reviewer (redirect SSRF, late size cap, transient-failure caching; fixed). Chromium already fills chains itself.

### B-36 Recipe stability: a later refresh reuses the recipe
- **Priority:** medium (do it once portals reach serving reliably)
- **Status:** open
- **Problem:** The PDF expects regular reads to run without model calls. Nobody has checked that a recipe
  found on attach keeps working on later refreshes when the site hasn't changed.
- **Test:** For each portal that reaches `serving` (fresh DB, unassisted), wait at least 5 minutes, then
  `POST /portals/{id}/refresh`. Expected: the same recipe is reused, the run is a `read` with 0 model
  calls, the portal stays `serving`, tenders and files are kept. Transient errors may fail a read without
  triggering a repair (retry later). An unnecessary repair or recipe rebuild is a bug: file it.
- **Also check:** compare the same tender ids' documents before and after a repair (one attach-then-repair run showed 409 files on attach and 317 after repair, probably because WATCHTOWER_MAX_DETAILS sampled different tenders, but that is unproven).
- **Findings:**

### B-37 Reuse recipes across portals running the same software
- **Priority:** low (later; no caching layer needed)
- **Status:** open
- **Problem:** The PDF notes that many portals run the same software under different addresses (Cosinex,
  NetServer, EPPS, ...), and judges how well the approach scales to many more portals. Today each attach
  starts from scratch, so sibling portals pay for the same discovery again.
- **Idea:** When attaching, detect the software from generic signals (URL path patterns, generator tags,
  footers). If an active recipe exists for another portal with the same software, give the agent that
  recipe as a starting point, with host-specific parts replaced. It still goes through test_recipe and
  submit as usual. Measure model cost and calls with and without the starting point.
- **Findings:**

### B-38 Structured failure reasons for operators
- **Priority:** normal
- **Status:** open
- **Problem:** `portal.reason` and `run.reason` are free text, often written by the agent. Operators can't
  filter or count failures by cause, and the evidence (which robots.txt rule, which captcha marker, which
  HTTP status, which URL) isn't stored. Example: a portal whose robots.txt allows only named search
  engines (`User-agent: *` / `Disallow: /`) shows up only as a sentence.
- **Idea:** Add a `failure_kind` (robots, captcha, login, access_denied, tls, network, no_listing,
  no_documents, agent_gave_up, internal) plus a short `evidence` (rule line, marker, status code and URL)
  to portal and run. Set them where the block is detected (fetch.py and the watchtower states), not by the
  model. Return them in `GET /portals` and `/runs` (extra fields are fine), and show them in the dashboard.
- **Findings:**

### B-39 Operator-supplied API access per portal
- **Priority:** low
- **Status:** open
- **Problem:** Some portals block crawlers in robots.txt (e.g. `User-agent: *` / `Disallow: /`) but offer a
  dedicated API, sometimes behind a free key. Today the system can only use what it discovers anonymously.
- **Idea:**
  1. An optional per-portal config, given by the operator (e.g. `POST /portals` with extra fields, or a
     settings endpoint): API base URL or endpoints, and credentials (API key or header). Stored securely,
     never logged or returned in API responses.
  2. The agent uses that config to map the API once into a recipe. Regular reads then reuse it without
     model calls.
  3. Agent hint (generic): when robots.txt broadly disallows the website, look for an official API or
     open-data host (its own robots.txt applies) before giving up, and say in the failure reason if one
     exists but needs a key.
- **Findings:**

### B-40 JSON paths with [*] and [0] are not understood
- **Priority:** high (a correct document recipe failed on syntax)
- **Status:** done (62bbb61)
- **Last seen:** 2026-11-21 06:21
- **Problem:** A fresh TenderNed attach found the per-tender JSON file list and tested
  `documents: {"path": "$.documenten[*].links.download.href"}`, the usual JSONPath form. Recipe dot paths
  only accept `path: "documenten"` plus `attribute: "links.download.href"`, so every test reported no
  document links and the agent gave up.
- **Idea:** Accept `[*]` (map over a list) and `[N]` (index) in dot paths, anywhere in the path.
- **Findings:** Dot paths now accept `[*]` (map over a list or a dict's values, results flattened,
  None dropped) and `[N]`, parsed once; a literal key named `*` still wins. Unsupported JSONPath (`..`,
  `['k']`, filters, negative indexes) returns nothing at runtime, as before, so stored recipes can't start
  failing, but test_recipe now reports it as a problem naming the path. `attribute` results are flattened,
  and an `attribute` on items that are already scalars keeps them (the agent wrote
  `$.documenten[*].links.download.href` plus `attribute: "href"`). Two reviewers (4 + 4 findings), all
  fixed; 102 tests. **Unassisted result:** fresh DB, just `https://www.tenderned.nl/aankondigingen/overzicht`:
  serving, 1000 tenders, the 10 sampled tenders got 158 real tender files (leidraad, bijlagen, nota's van
  inlichtingen, prijzenbladen; 48 MB), 10 model calls, $0.045. Refresh: ok, 0 model calls, 78 s, 159 files
  kept. The recipe the agent built by itself: JSON listing `publicaties?page=0&size=50`, detail.url
  template, `documents_url` `.../publicaties/{id}/documenten` (found via the B-01 sibling probe) with
  documents `$.documenten[*].links.download.href`.

### B-41 Preserve tenders when a listing index is truncated
- **Priority:** high (unvisited categories can lose open status and stop receiving documents)
- **Status:** in review (PR #13)
- **Problem:** Recipe.apply caps unique category links at MAX_INDEX_LISTS. If all visited categories finish, it reports complete despite unvisited categories. Synthetic index with MAX_INDEX_LISTS + 1 ordinary category links reproduces the coverage error; no real site was probed.
- **Idea:** Mark truncated category indexes as incomplete and verify that refreshing preserves the open status and files of a previously saved tender in a skipped category.
- **Findings:** PR #13 marks an indexed listing incomplete if unique category links exceed the existing cap, preserving saved tenders and their files. Duplicate links still permit closure when all distinct categories finish. Two reviewers, 93 backend tests and Ruff passed with synthetic fixtures.
  PR updated to 2ca52ed preserving new indexed sampling and reservation. Derek-Codex-A and the Codex
  document reviewer independently checked integration; 169 backend tests and Ruff passed.

### B-42 Document discovery when the listing already works
- **Priority:** high (sweep 2: the next blocker after POST listings)
- **Status:** in review (draft PR #18, JSON document-link hints; B-52/B-67 cover external buyer documents)
- **Last seen:** 2026-11-21 07:47
- **Problem:** On several Dev portals the agent finds and validates the tender listing, but no document
  links in the sampled tenders, so B-22 correctly refuses the recipe:
  - etenders.gov.ie: detail pages have no file links;
  - hankintailmoitukset.fi: a JSON listing works, but notice pages have no links;
  - TenderNed: the real files only load when the "Documents" tab is clicked;
  - Doffin: its guessed `/notices/{id}/documents` endpoints return 404.

  Documents are often loaded by JavaScript after a tab click, live on a separate documents page or on
  the buyer's external platform, or sit behind a per-tender JSON endpoint the page calls.
- **Idea:** (all generic, no portal rules)
  1. `render_page` on one tender's own page and report the JSON calls it makes, including POSTs (B-18).
     Document lists are often among them.
  2. A small declarative browser action for detail pages: click an element matching common document-tab
     labels in many languages (Documents, Dokumente, Documenten, Pièces, Documentos...), then capture the
     JSON calls and links it loads.
  3. Follow "documents", "tender documents", "procurement documents", "DCE", "pliegos" or "Vergabeunterlagen"
     links (documents_page), including links to external buyer platforms (B-02).
  4. Extend B-01's per-tender JSON probe to POST endpoints observed on detail pages.
  - Validate with fresh, unassisted attaches on the Dev portals above. Count tenders with real files.
- **Findings:**
  Derek-Codex-B: on B-43 + B-21 integration, fresh Finnish attach still found only the public JSON listing and no usable tender-document links in its sampled details (B-10). The Finnish browser-render comparison had identical JSON counts and DOM lengths with and without the cache.
  PR #15 broadens existing HTML hints to accessible names and direct file links without fetching candidate URLs. Seven local fixtures, 105 backend tests, Ruff and pre-commit review passed. No fresh live trial; broader JS-dependent discovery and delivery remain open.
  lead-w1 2026-11-21: TenderNed solved unassisted via B-01 probe + B-40 (158 files). etenders.gov.ie is not a
  discovery gap: the tender page's download button calls `/epps/cft/downloadCompetition.do?resourceId=<id>`,
  which answers 302 to `/cas/login`, so documents need a login. Correct outcome there is
  `Blocked: documents need login` (B-34 block rules), not a recipe without files.

2026-11-21 Codex (Derek Windows B-42): Fresh Doffin and Finland Hilma URL-only attaches on main acefd4c failed before listing (0 tenders/files; 13/$0.0634 and 16/$0.0634 calls). eTenders reached serving with 10 tenders and 6 files (8 calls/$0.0358), but all six source references were notice-download links. Refresh retained six at 0 calls and the sample file hash matched. See B-50; B-42 remains open for generic document discovery on portals with usable listings.
  Derek-Codex-A: Fresh eade7ae Doffin and TenderNed results in B-10. Manually following a listed Doffin competition showed the response's external document-page field at character 817; render_page cuts JSON previews at 600. The referenced buyer page advertises ten attachments on a separate details page. A generic JSON field-hint change is in fresh validation; no site identifiers or endpoints in code.

  Reviewed branch b-42-validate-document-discovery (01703d8): Doffin normal render tool now reports the document-reference key that fell outside its JSON preview. A fresh URL-only run (USD 0.0871, 19 calls) found a buyer-platform restriction but still saved 0 tenders/files; the recorded state is failed, not a confirmed portal-wide block. No host-specific rules. The 168-test suite and Ruff passed. TenderNed served 200 tenders and 159 files, including 52 Office attachments. A zero-model-call refresh preserved the counts and a checked file hash. See B-10 and draft PR #18.

### B-43 Documents behind a tab or script on the tender page (browser slice of B-42)
- **Priority:** high (part of B-42)
- **Status:** done (eade7ae); unassisted validation on Doffin / hankintailmoitukset.fi / TenderNed still welcome
- **Last seen:** 2026-11-21 06:26
- **Problem:** B-42's browser part: documents that only appear after a "Documents" tab is clicked or after
  scripts load them (TenderNed's Documents tab; JSON calls a tender page makes). B-42 (Codex Johanna Mac)
  covers the public HTML hint slice; this item covers rendering one tender page, a declarative "click a
  documents tab" step, and capturing the JSON calls and links it loads.
- **Idea:** generic, no portal rules. Detail kind `browser` plus an optional declarative
  `detail.click` (selector or multilingual documents-tab label) before reading; report JSON calls seen
  after the click so the agent can map document lists. Validate fresh and unassisted on Dev portals
  (TenderNed, etenders.gov.ie, hankintailmoitukset.fi); count tenders with real files.
- **Findings:** WIP by lead-w2 on branch `b-43-documents-tab` (6e21c1a, based on main 890ebee; 100 tests and ruff pass).
  - What it does: `browser.render(..., click=pattern)` clicks, after the page settles, the first visible link-less
    control (`[role=tab], [role=button], button, summary, a:not([href]), a[href^='#'], a[href^='javascript:']`, up to
    80) whose text (<= 60 chars, casefolded) matches the pattern, waits until stable again, returns the DOM after the
    click; JSON loaded after the click carries `afterClicking: <label>` (own cap), plus a `{"clicked": label}` marker
    in `calls`. Agent `render_page` always passes the documentsPage keyword regex, lists after-click JSON first and
    says what it clicked; instructions say to map such JSON with `documents_url` (kind json) or use `detail.click`.
    Recipe `detail.click` (literal label, 2-60 chars, detail kind browser only) clicks before reading detail pages.
  - Works: live TenderNed tender page: clicked the "Documenten" tab (div role=tab, no href) and captured
    `/papi/tenderned-rs-tns/v2/publicaties/{id}/documenten` (24 documents, `documenten[*].links.download.href`, plus
    `links.downloadZip.href` for all files as one ZIP, which plain dot paths already read). Tests: last test in
    tests/test_browser.py (real Chromium, local server), last test in tests/test_recipe.py.
  - Must fix before merge (two reviewers agreed): (1) don't click submit-capable controls: skip `button` whose
    `type` is submit inside a `form`, try `[role=tab]`/`summary` before buttons; (2) skip labels with download or
    upload words (download, herunterladen, upload, télécharger, descargar, zip...); (3) abort form-submission
    navigations (POST, resource_type document) after the click; (4) `accept_downloads=False` for every render
    (now only the NetServer path); (5) `call_limit = len(calls) + 11` (marker eats one slot); (6) don't fail
    `require_stable` when only the post-click settle times out; (7) re-check robots.txt if the click navigated;
    (8) agent note: "the page shown is AFTER the click", and `detail.click` is a literal case-insensitive substring.
  - Not done: unassisted validation. A fresh-DB TenderNed attach on this code was stopped early. Test: fresh
    JIMIX_DB, attach https://www.tenderned.nl/aankondigingen/overzicht, check the agent uses documents_url
    `.../publicaties/{id}/documenten` (kind json) with `links.downloadZip.href` or, after B-40,
    `documenten[*].links.download.href`; count tenders with files. Irish eTenders: real tender documents need a login
    (`downloadCompetition.do` redirects to CAS login); only the notice PDF is public and listing rows link it directly
    (`downloadNoticeForAdvSearch.do`), so IE needs row-level notice links rather than this click (B-42 slice).

### B-44 Cost and run metrics
- **Priority:** normal
- **Status:** open
- **Problem:** Each run stores llm_calls, tokens and cost_usd, and the dashboard shows them per run, but
  there are no totals or averages. The PDF asks "what one portal costs in model usage".
- **Idea:** `GET /metrics` (or fields on `GET /portals`): total cost; average and median cost per attach,
  per repair and per portal; model calls per attach; runs by kind and result; share of reads with 0 model
  calls. Show a small summary on the dashboard. This feeds B-11 (README numbers).
- **Findings:**

### B-45 Per-portal spend limit for repeated repairs
- **Priority:** normal
- **Status:** open
- **Problem:** `WATCHTOWER_MAX_COST_USD` caps each attach or repair run, but nothing caps a portal over
  time. If a portal is genuinely broken and repair keeps failing, every `/refresh` starts another paid
  repair (up to the cap each time). Transient errors already don't trigger repairs (B-23).
- **Idea:** Back off repairs per portal: after a failed repair, wait an increasing interval (or a fixed
  number of refreshes) before the next one, and keep a daily per-portal model budget
  (`WATCHTOWER_PORTAL_DAILY_USD`). Refresh still does cheap reads meanwhile. Say so in the portal's
  `reason`, e.g. "repair paused until ... after 2 failed repairs".
- **Findings:**

### B-46 README: deployment and the PDF's hand-in requirements
- **Priority:** high (mandatory hand-in, per the PDF)
- **Status:** claimed by lead-w2 (matiiss/Codex) 2026-11-21 07:50
- **Last seen:** 2026-11-21 08:00
- **Problem:** The README covers local setup and how Watchtower works, but doesn't match the PDF's "What you
  hand in" section yet, and the Docker deployment isn't the main path.
- **The PDF expects:**
  - A Git repository with a Dockerfile at its root. `docker build .` must work on linux/amd64 and build
    reasonably quickly. The image's default command starts the API and dashboard on port 8080.
    This is already done: root `Dockerfile` + `docker/start.sh` (B-06), verified building and serving
    in B-16.
  - A one-page README: how the system works, which environment variables it needs (model keys named in
    the README; values given at hand-in), and what one portal costs in model usage (numbers from B-11 /
    B-44).
  - A video of at most three minutes showing the system at work, including the dashboard (not a repo task,
    but the README can link it).
- **Do:** Put "Run with Docker" first (`docker build -t jimix . && docker run -p 8080:8080 -e OPENAI_API_KEY=... jimix`,
  data volume `/data`), then local development. Keep the "how it works" part to about one page. Give a
  complete, current environment-variable table (model and key, `OPENAI_BASE_URL`, cost and turn limits,
  download limits, timeouts, `WATCHTOWER_BROWSER_WS`, `JIMIX_DB`/`JIMIX_FILES`). Add a cost-per-portal
  section with real numbers. Remove stale instructions (old watchtower package, scheduler).
- **Findings:**

### B-47 Status mapped to a field that never changes
- **Priority:** medium (correct fields)
- **Status:** in review (PR #16)
- **Problem:** TenderNed (fresh unassisted attach, 1000 tenders): the agent mapped `status` to
  `publicatiestatus.omschrijving`, which is "Gepubliceerd" for every notice, so award and prior-information
  notices (e.g. "Aankondiging gegunde opdracht") count as open: 774 of 1000 open.
- **Idea:** Generic test_recipe warning: if `status` (or any mapped field) has one identical value across
  the whole sample, say so and suggest a field naming the notice type or stage. Award-type wording is already
  in keywords.json (closed list).
- **Findings:** PR #16 adds an advisory warning for one case-insensitive raw mapped status shared by three or more unique tenders. No additional requests or output/status changes. Four synthetic cases, 122 backend tests, Ruff and review passed. No fresh live attach.

### B-48 Check that the address is a tender portal at all
- **Priority:** normal
- **Status:** open
- **Problem:** `POST /portals` accepts any URL. Given a news site, a shop or a blog, the agent may build a
  "recipe" from any list it finds, and the portal could end up `serving` with non-tenders, or waste model
  calls before failing.
- **Idea:** (generic, multilingual)
  - Before or early in the attach, classify the site cheaply: procurement words in the page and its links
    (tender, notice, procurement, Ausschreibung, Vergabe, appel d'offres, licitación, aanbesteding,
    przetarg...), CPV codes, deadlines.
  - In `test_recipe`, require the sampled records to look like tenders (titles plus a buyer or deadline,
    procurement words in the listing or detail pages).
  - If not, fail fast with a clear reason ("doesn't look like a public procurement portal") and a
    structured kind (see B-38).
  - Unit-test with a fake non-tender site.
- **Findings:**

### B-49 Attach results vary between runs on the same portal
- **Priority:** high (reliability; the judges attach each portal once)
- **Status:** open
- **Problem:** The same code and portal succeed in one fresh run and fail in the next. TenderNed: Derek's fresh
  run at 06:49 reached serving with 1000 tenders and 159 files. Sweep 3 (main 791657e, 06:45) failed with
  "JS app and no usable rows/API endpoints were discovered". NRW also flips between runs. The agent's
  exploration path decides the outcome.
- **Idea:**
  1. Measure it: attach the same 3 Dev portals 3 times each with fresh DBs and record success, calls and
     the first divergent step (from `watchtower.agent tool` log lines).
  2. Make discovery less path-dependent: run the cheap deterministic steps every time before the model
     starts (render the start page and record its JSON/POST calls, B-01 probes, documents-page hints) and
     give their results to the agent up front.
  3. On a failed attach, retry once with a hint summarising what the first attempt found, if the budget
     allows.
  4. Lower temperature or seed if the provider supports it.
- **Findings:**
  Two B-21 fresh TenderNed attaches both served 158 distinct files; reads and hashes survived both zero-call refreshes. Stored listing recipes request different page sizes (100 vs 50) at the same 20-page limit, yielding 2000 vs 1000 saved tenders. One was before the final cache-directive review; the cache probe measured zero cross-run entries on this portal.

### B-50 Notice-only PDFs pass tender-document validation
- **Priority:** high (false `serving` state without procurement documents)
- **Status:** in review (PR #19)
- **Problem:** A fresh URL-only eTenders attach (Dev URL, empty DB/files, max 10 details) reached `serving` with 10 tenders and six PDFs, but all six source references were `downloadNoticeForAdvSearch.do` publication notices. The B-42 findings record that the actual competition-document download redirects to CAS login. The portal therefore looks healthy while no procurement attachments are available.
- **Idea:** Generically distinguish a publication notice from tender documents; notice PDFs alone should not satisfy the hard document requirement. When an actual document path is login-blocked, report a true blocked reason. Avoid host, endpoint, or selector special cases.
- **Findings:** On main `acefd4c`, attach used 8 model calls / USD 0.0358 and saved 6 files for 10 tenders. The `/files/{sha256}` sample matched, and Refresh retained all six files with 0 model calls. The six sources were all notice-download links. See B-42 for the broader portal runs.
  2026-11-21 07:51 Codex (Johanna Mac): PR #19 (7afdee5) surfaces an observed mapped attachment access block even when notice-like files download. Filename-only stays advisory to avoid rejecting combined PDFs. Independent review, 168 offline tests and Ruff passed. Fresh Dev attach pending.

### B-51 Live cost and usage during a run
- **Priority:** normal
- **Status:** open
- **Problem:** A run's llm_calls, tokens and cost_usd are written only when the run finishes, so the dashboard shows $0 for a running attach or repair.
- **Idea:** Update the run row after each model call (or every few seconds) and show it live in the dashboard next to the B-08 log stream.
- **Findings:**

### B-52 Multi-step document downloads
- **Priority:** high (reusable declarative scraping capability)
- **Status:** claimed by Codex (Derek Windows) (Windows/Codex) 2026-11-21 08:02
- **Last seen:** 2026-11-21 08:02
- **Problem:** Some portals don't link files directly: you request a ZIP of all documents (often POST), wait while it's prepared, poll a status URL, then download; or a link leads to a short-lived token URL.
- **Idea:** Declarative steps on documents: `prepare` (request, possibly POST the site itself made), `poll` (URL plus JSON path until ready, bounded), `download` (URL from the poll response). Respect the rate limit and download budget.
- **Findings:**
  Derek-Codex-A: Doffin Dev notice 2026-118085 links to an external buyer page and then a public attachment page. Its rendered DOM offers individual download controls and a ZIP control, but none has an href; the static HTML has no file anchors. This is an observed B-52 case, with no confirmed login block.

### B-53 Fixed interaction sequences for listings and searches
- **Priority:** high (reusable declarative scraping capability)
- **Status:** open
- **Problem:** Some listings only appear after a fixed sequence: accept a cookie banner, choose "all" in a filter, press Search, open a tab. As long as it's the same steps every time it can be replayed.
- **Idea:** Declarative `actions` for browser listings and details: click (literal label or selector), select option, type a fixed value, press Enter, wait for settle. Same safety guards as B-43 (no submits of personal data, no downloads, robots per navigation), a small step cap, and the agent may only use actions it ran in render_page.
- **Findings:**

### B-54 Session tokens and CSRF fields in requests
- **Priority:** high (reusable declarative scraping capability)
- **Status:** open
- **Problem:** Search APIs and form posts often need a token from the page (CSRF field, anti-forgery header, session id in the body), which changes every visit; a stored POST body with an old token fails.
- **Idea:** Recipe placeholders filled at read time from the start page: `{{token: <selector or JSON path on the listing page>}}` in body or headers; the page is fetched first in the same session (cookies kept). Never store the token in the recipe.
- **Findings:**

### B-55 Cursor and token pagination
- **Priority:** high (reusable declarative scraping capability)
- **Status:** open
- **Problem:** APIs often page with a cursor from the previous response (`nextPageToken`, `cursor`, `after`, a `Link: rel=next` header) rather than page numbers or next links.
- **Idea:** Listing `next_from`: JSON path (or header name) of the next cursor in the response, and where to put it (query parameter or body path). Stop when absent.
- **Findings:**

### B-56 RSS, Atom and XML listings
- **Priority:** high (reusable declarative scraping capability)
- **Status:** open
- **Problem:** Many portals publish notices as RSS/Atom feeds or XML (sitemaps, OCDS/eForms XML). Recipes can only read HTML and JSON.
- **Idea:** A `kind: xml` listing with XPath-like paths (or convert XML to the JSON shape and reuse dot paths). Feeds often carry title, link, date and id; documents come from the linked page.
- **Findings:**

### B-57 ASP.NET WebForms postback paging
- **Priority:** high (reusable declarative scraping capability)
- **Status:** open
- **Problem:** Older portals page and search with `__doPostBack`: each page is a POST of the whole form with `__VIEWSTATE` and `__EVENTTARGET`; there are no next links or page numbers in URLs.
- **Idea:** Either a browser `actions` click on the pager (see the fixed-interaction item), or a declarative postback: take the form fields from the current page and set `__EVENTTARGET`/`__EVENTARGUMENT` from the pager link. Same-site only.
- **Findings:**

### B-58 Listings and documents inside iframes
- **Priority:** high (reusable declarative scraping capability)
- **Status:** open
- **Problem:** Some portals embed the tender list or the document list in an iframe from another host (often the procurement software vendor). Selectors on the outer page find nothing.
- **Idea:** render_page reports iframes with their src; the agent can use the iframe URL as the listing or detail URL (same robots and rate rules per host).
- **Findings:**

### B-59 Localised dates and month names
- **Priority:** normal
- **Status:** open
- **Problem:** Dates such as "21. November 2026", "21 novembre 2026" or "21 listopada 2026" fail parsing, so publicationDate and submissionDeadline end up null.
- **Idea:** Multilingual month names (keywords.json) mapped before parsing; tests in many languages; keep "wrong is worse than missing".
- **Findings:**

### B-60 Documents split by lot or folder
- **Priority:** high (reusable declarative scraping capability)
- **Status:** in review (PR #20)
- **Problem:** Tender documents are often grouped by lot or folder (lot pages, folder trees, "show more" lists), so one documents page shows only part of the files.
- **Idea:** Allow a documents selector to follow up to N levels of folder or lot links (same site, bounded), deduplicating by sha256.
- **Findings:**
  2026-11-21 07:55 Codex (Johanna Mac): PR #20 (ca5580a) adds bounded public same-origin folder navigation and preserves old files on partial coverage. Independent review, 172 offline tests and Ruff passed. Fresh Dev attach pending. When merging with B-28, keep folder GETs exempt from both HTTP and HTML redirects.

### B-61 Incremental reads
- **Priority:** low
- **Status:** open
- **Problem:** Every refresh re-reads the whole listing and every detail page, which is slow at one request per second.
- **Idea:** Skip unchanged tenders: use ETag/Last-Modified, list-level change dates, or only open detail pages for new or changed rows; still mark disappeared tenders closed only when the listing was read completely.
- **Findings:**

### B-62 Deliver tenders even when their documents can't be collected
- **Priority:** high (UX, operator request)
- **Status:** open (consolidating with lead-w1 at the lead's request)
- **Last seen:** 2026-11-21 07:56
- **Problem:** B-22 refuses any recipe whose sampled tenders yield no downloadable files. On portals whose documents are on buyers' platforms that block crawlers or need a login (Doffin: tendsign robots-disallowed, artifik/hyyr HTML pages; Ireland and Finland: documents behind login), we deliver nothing at all, although the tenders and their links are public.
- **Idea:** Accept a recipe that reads tenders correctly even if documents can't be collected, once the agent has really tried (documents mapped, the downloads blocked or not files). Deliver tenders with `url` so people can open them in a browser, `documents: []`, and a per-tender reason (e.g. `documentsNote: "hosted on tendsign.com, which disallows crawlers"`). The portal stays serving, with its reason saying documents are unavailable and why. Keep "no tenders" as a real failure. Update B-22's check accordingly and test it.
- **Findings:**
  Derek-Codex-A: Local alternate implementation stayed unmerged for the lead-w1 handoff. Eight release/validation fixtures cover real attachment denial, host-only mappings, external HTML, 404/503, tender-detail self-links, and shared open/blocked footer links. Verified a zero-model refresh restores availability when a real document appears. The fresh Doffin trial found a separate false success: the six reported downloads were JSON notice metadata (B-74).

### B-63 Show as much as possible on partial failures
- **Priority:** normal
- **Status:** open
- **Problem:** On failures we often show only a sentence. Operators would rather see what was found: the listing URL, sample tenders, the recipe tried, which step failed.
- **Idea:** Store partial results of failed attaches and repairs (best tested recipe, sample tenders, the failing step and its evidence) and show them in the dashboard, clearly marked as not delivered. Never present them as delivered tenders through the API.
- **Findings:**

### B-64 Dashboard: Refresh button can't be spammed
- **Priority:** high (Refresh availability and feedback)
- **Status:** claimed by lead-w3 (matiiss/Codex) 2026-11-21 07:53
- **Last seen:** 2026-11-21 08:00
- **Problem:** Refresh is clickable again immediately and while a run is in progress.
- **Idea:** Disable while submitting, while an unfinished run exists, or during a short cooldown. Give a visible reason and re-enable when eligible, including after a failure. Keep this UI work separate from the backend retry investigation.
- **Findings:**

### B-65 API contract tests against the PDF's "Exact answers"
- **Priority:** high (mandatory API behaviour)
- **Status:** done (c78a645)
- **Problem:** Nothing checks automatically that every endpoint matches the PDF's appendix.
- **Idea:** One test module with a fake portal that checks each answer: POST /portals 201 `{id}` and the same id for the same URL; GET list fields (`id, url, state, reason, tenderCount, lastSuccessAt`); DELETE 204 and no further portal requests; refresh 202 `{runId}` and the running runId again; tenders with every required field (null when not shown, optional ones omitted) and `changedSince`; runs `id, kind, startedAt, finishedAt, result`; 404 for unknown ids; times with offset; ZIP unpacked; `/files/...` bytes.
- **Last seen:** 2026-11-21 07:44
- **Findings:**
  Fixture portal verifies the API lifecycle, ZIP files, timestamps and deletion during runs. 170 tests and Ruff passed. No review: fixture-only change verified under the sprint rule.

### B-66 Three-minute demo video
- **Priority:** high (mandatory hand-in, human task)
- **Status:** open
- **Problem:** The PDF wants a video of at most three minutes showing the system at work, including the dashboard.
- **Idea:** Script: add 2-3 portals (one serving with files, one blocked with a clear reason, one repairing), show the dashboard, a refresh with 0 model calls, a repair after a recipe break, the cost per portal. Needs the B-08 live logs to look good. Record on the Docker image.
- **Findings:**

### B-67 Learn external document platforms automatically
- **Priority:** high (documents on buyer platforms, e.g. Doffin)
- **Status:** open
- **Problem:** On national notice portals (Doffin, also TED-style aggregators) each tender's documents live on whatever platform the buyer uses (tendsign, artifik, hyyr, Mercell, EU-Supply...). The notice links to a page on that platform, not to files. B-02/B-20 handle per-host rules the agent writes up front, but the set of hosts is open-ended and unknown at attach time.
- **Idea:** When sampled document links lead to HTML pages on another host: (1) group them by host; (2) for each host, have the agent learn a small sub-recipe (find the file links or the platform's documents API on that page, using B-43 tab clicks and B-18 POST capture if needed), stored per host and reused for every tender and every portal linking there (see B-37); (3) respect robots.txt and logins per host, record a per-tender reason when a host can't be read (B-62); (4) cap hosts learned per attach by cost. Validate on Doffin unassisted.
- **Findings:**
  Derek-Codex-A: Manually traced one current Doffin competition into Artifik. The public tender detail JSON has an explicit buyer-document URL; the target advertises ten attachments on a separate details page. Individual file controls have no href. B-42 work makes the JSON field discoverable; B-52 covers the later download step.

### B-68 Opt-in generated extraction code as a last resort
- **Priority:** normal (off by default)
- **Status:** open
- **Problem:** Some portals need logic that declarative recipes can't express. Generated code would handle them quickly, but it runs model output written from untrusted pages.
- **Idea:** A setting (default off): when the declarative path fails, the agent may write a small extraction function. It runs in a sandbox (separate process, no network except a pipe to the rate-limited Fetcher, no filesystem, CPU and time limits) and returns the same tender/document structure, which passes the same validation (B-22/B-48 checks) before anything is saved. The code is stored like a recipe, shown to operators, and logged. Security review needed.
- **Findings:**

### B-69 Settings in the dashboard, stored in the database
- **Priority:** normal
- **Status:** open
- **Problem:** Limits and constants (model, cost and turn caps, download limits, timeouts, max details, render timeout, user agent, ...) are environment variables or hard-coded; the dashboard's agent settings only live in browser storage. The OpenAI key can only come from the environment.
- **Idea:** A `setting` table and `GET/PUT /settings` (API key write-only, never returned or logged). Every tunable constant in backend/app read through one settings helper: database value, else environment variable, else default. Dashboard settings page to edit them, with validation. Changes apply to the next run. Document precedence in the README.
- **Findings:**

### B-70 Validate a captured POST search through recipe test and submission
- **Priority:** medium
- **Status:** open
- **Problem:** A dashboard attach to https://oeffentlichevergabe.de/ui/de failed on 2026-11-21 at 06:21 UTC. The agent reported that it could read the portal and see the POST search endpoint, but the replay check refused the candidate recipe during validation. The attach ended without a validated recipe.
- **Idea:** Trace the same captured public search through `test_recipe` and `submit_recipe` to identify the first refusal. Verify that a matching capture can pass both steps and that a mismatched request still gets a clear refusal.
- **Findings:** The failed run is confirmed in dashboard history on main `acefd4cc`. Exact-body replay was the explanation in the run report. The specific mismatch is still unverified. Related to B-18, which added support for captured POST listings.
  Fresh model-free render on the demo revision at the exact German portal URL: the page rendered 77617 characters of DOM and 6 JSON responses, all GET; the Fetcher recorded zero eligible POST bodies. A known POST endpoint in a script cannot satisfy replay validation without the browser actually issuing it. This observation is limited to that render, not the earlier run.
  Derek-Codex-B: a local Chromium portal on the demo revision acefd4c generated a genuine public JSON search, observed by its Fetcher. The captured body passed both test_recipe and submit_recipe, including a real fixture document download and an accepted recipe. Changing a nonpaging search value was refused by both calls. The original live mismatch is still unconfirmed.

### B-71 Investigate tender ID validation for POST search results
- **Priority:** medium
- **Status:** claimed by Derek-Codex-B (Danylos/Windows) 2026-11-21 08:09
- **Last seen:** 2026-11-21 08:09
- **Problem:** An operator reported that attach discovered the public POST search endpoint `/bkmc/searches`. The agent could not validate the candidate recipe, and `submit_recipe` reported the same issue with the source of the tender ID. The run ended without an accepted recipe.
- **Idea:** Compare the ID field in a captured search result with the candidate mapping in `listing.fields.id`. Trace that mapping through `test_recipe` and `submit_recipe`. A mapping to a stable portal ID should pass both steps. If the mapping is invalid, the error should identify which field or path needs correction.
- **Findings:** Operator report only. The portal URL, candidate mapping and exact validator error have not been confirmed. Related to B-70 (captured POST replay). This report specifically identifies ID validation at submission.
  On acefd4c a local Chromium fixture produced a real captured JSON search and retained one attachment. Both test_recipe and submit_recipe passed with a valid tender ID path. Replacing only that path with a nonexistent one made both report "no tenders found"; neither diagnostic identified the absent ID mapping. This reproduces the diagnostic gap on a fixture, not the earlier portal failure.

### Later (not for the hackathon unless time allows)
- Generated scraper code instead of declarative recipes, run in a tight sandbox (no network except through the rate-limited Fetcher, no filesystem). More flexible for odd portals; much harder to make safe against prompt injection.
- Scheduler for periodic refresh, with backoff so broken portals don't burn model calls.
- Postgres and a job queue instead of SQLite and in-process tasks.
- Rate limit shared across processes; replace `fetch.site()` with `tldextract`.
- Storage cap and deleting files no tender references.
- Redaction of secrets as a logging filter.
- Self-improving codebase: when the agent meets a pattern the recipe format can't express (as with POST search APIs before B-18), it writes the missing mechanism as a pull request to this repo, with tests, for human or agent review. Never applied automatically.

### B-72 Investigate and fix Refresh after a failed run
- **Priority:** high (operator report, core Refresh reliability)
- **Status:** claimed by lead-w1 (matiiss/Codex) 2026-11-21 07:53
- **Last seen:** 2026-11-21 08:00
- **Problem:** An operator reported that Refresh may do nothing after a failure. Reproduce before assuming the cause.
- **Idea:** Trace the dashboard POST through run creation and state transitions after a failed attach or repair. Verify an eligible retry starts a fresh run, active requests reuse the active run, and failures finalize so they cannot block later retries. Add a focused regression test. The disabled button and cooldown are B-64.
- **Findings:**

### B-73 Keep exploring public paths to tenders and documents before giving up
- **Priority:** high (discovery reliability, complements B-49)
- **Status:** claimed by Derek-Codex-Main (Danylos/Windows) 2026-11-21 07:59
- **Last seen:** 2026-11-21 07:59
- **Problem:** The model can stop after an unsuccessful recipe or a narrow discovery path while viable public listing and document routes remain untried.
- **Idea:** Encourage distinct public discovery paths and use feedback from failed candidates to guide the next probe. Separate discovery coverage from final tender/detail/file preview limits. Keep existing cost, request pacing, robots, and download safeguards. Test early give-up and a later successful candidate deterministically.
- **Findings:**

### B-74 Skip API metadata returned as a tender attachment
- **Priority:** high (false serving state on the Doffin Dev portal)
- **Status:** claimed by Derek-Codex-A (Danylos/Windows) 2026-11-21 07:54
- **Last seen:** 2026-11-21 08:02
- **Problem:** Fresh URL-only Doffin treated notice-detail JSON as binary attachments. Attach served 984 tenders with 3 files at USD 0.0605 / 14 calls. Refresh made 0 model calls and listed 993 saved tenders with 6 files. Every inspected file parsed as a notice metadata object. Zero procurement attachments were established.
- **Idea:** Reject an inline JSON API response as a document when it is an unnamed endpoint. Preserve explicitly published JSON attachments. Validation and refresh should use the same decision, including existing recipes previously accepted on these bytes.
- **Findings:**
  Local fix rejected the previously misclassified Doffin notice API response before storing it. In a fresh URL-only attach (three details), the public listing was discovered but the model could not map real downloadable files. Failed with zero tenders/files, 15 calls, USD 0.0698. Original false success and new run in B-10. Independent review in progress.
