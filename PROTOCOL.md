# Protocol

> **Recent changes** (newest first; the lead keeps the last five here):
> - 2026-11-21 06:04: one reviewer is enough for follow-up commits to an already reviewed design; new shared
>   mechanisms still get two.
> - 05:48: target is >=90% of sweep portals in the correct state (serving with files, or a true block).
> - 05:13: re-read AGENTS.md and docs/testing.md when they change; Python only via uv.
> - 04:55: fresh empty JIMIX_DB per retest; develop only on the Dev list (B-10); held-out set exists.
> - 04:25: build the system, not the answers (no portal-specific code or hints).

This branch (`coord`) is how everyone working on Jimix coordinates: humans and agents, on any machine.
It holds only three files:

- `PROTOCOL.md` — these rules and the current operation. The meta layer: changing it changes how
  everyone works.
- `BOARD.md` — the shared todo list. Every item is small and claimable by one worker.
- `CHANNEL.md` — an append-only message log.

Code lives on `main`. How to test is in `docs/testing.md` on `main`.

## Current operation

**Goal:** a `POST /portals/{id}/refresh` that handles every portal as well as possible. It should deliver
every open tender with correct fields and its documents, notice when a portal breaks, and repair it,
within the rules of `docs/Patterno-Track-Description.pdf`.

**Done means:** an operator adds a portal's address in the dashboard, clicks **Refresh**, and the portal
syncs: open tenders appear with correct fields and their document files are downloaded (within the
download limits), and the dashboard shows what happened. It should hold for portals we haven't seen, as
the PDF's judging describes.

**Target: at least 90% of the portals in a wide sweep end in the correct state.** Correct means either
`serving` with real document files, or `failed` with a true reason (a real login, captcha or robots.txt
block, as the PDF's "Notice" criterion asks). The lead runs fresh-database sweeps on Dev and held-out
portals and posts the success rate in `CHANNEL.md`. The current rate is the operation's main number. Until it does, keep going: after each item, pick the next one or scout.

**Strict focus: a `/refresh` that actually gets the documents.** This is the minimum requirement and
the reason this swarm exists. Before starting anything, ask whether it brings a real portal closer to
"refresh, then files arrive". If not, it waits.

**Build the system, not the answers.** We are not here to find each portal's document list by hand. The
system must find it by itself, given only a portal's address, because the PDF judges it on portals none of
us has seen. So:
- **No portal-specific code or prompt hints.**
  - No domain names in the code or prompts.
  - No hand-found endpoints like "TenderNed documents are at `/documenten`".
  - No selectors written for one site.
- **Knowledge about a software package is fine** (for example NetServer or Cosinex, which many portals
  share), but only as a reusable mechanism the system applies when it detects that package by itself.
- **What we learn by hand turns into capability.** A better tool, discovery step, recipe feature,
  validation or prompt rule that works on any portal.
- **A fix counts only if an unassisted attach works:** a fresh database, `POST /portals` with just the
  address, no recipe edits, and files arrive.
- **Every retest starts from scratch.** Use a new, empty `JIMIX_DB` (and `JIMIX_FILES`) each time, so the
  system has to find the method and build the recipe again. Never reuse or hand-edit a stored recipe to
  make a test pass. The only exception is the deliberate recipe break in a repair test.
- **Develop only on the Dev portal list (B-10).** We keep a held-out set of portals for testing
  generalization, the way the judges will. Don't scout portals outside the Dev list. If you find a promising
  new portal, propose it in `CHANNEL.md`, and the lead decides whether it joins the Dev list or the
  held-out set.
- **Check generality on an unseen portal:** one nobody used while building the fix. Note it in the item's
  Findings.

**Focus right now: documents.** Delivering each tender's actual document files is the core feature and
our weakest point (see the `Priority: high` items). Work on document delivery first; side features (the
dashboard, cost reporting, log streaming) only when no document item is open.

**How we get there:** everyone runs real portals, files each problem they find as a small item on the
board, and fixes items one at a time, with tests.

**Lead:** the main Claude session on matiiss's machine. The lead only keeps this file (the meta layer).
The board belongs to everyone: anyone adds items, picks them, sets priorities and finishes them without
asking. Anyone can propose changes to this file in `CHANNEL.md`.

## Kinds of work

- **Operation items** belong to the current operation (above). They're shared and critical, several
  workers may help, and they follow the full work loop below.
- **Solo tasks** are anything else someone asks one agent or person to do: a feature, a fix, a refactor.
  Coordination here is only about not disturbing others' work:
  - add the task to `BOARD.md` as `### S-<name>-NN <title>` with **Status**, **Files** (the files or
    areas you'll touch) and a **Last seen** line;
  - check other claims' **Files** for overlap first, and talk in `CHANNEL.md` if there is any;
  - merge small and often;
  - mark the task done when it's merged.
- **When an operator asks an agent for changes that will be committed,** the agent first offers to do
  this registration itself. If the request overlaps a running operation, the agent suggests joining the
  operation instead (for example, taking the matching item). The operator decides.

## Joining

1. If you are an agent, ask your operator first whether they want to devote resources to this
   operation. Offer to run as a background subagent so their main session isn't interrupted. Join only
   after a yes.
2. Set up once, from your clone of the repository:
   ```sh
   git fetch origin coord
   git worktree add ../jimix-coord coord
   ```
3. Real-portal tests need an OpenAI API key (`OPENAI_API_KEY`, in the environment or in `.env` at the
   repository root). Check whether one is set without printing it, e.g.
   `[ -n "$OPENAI_API_KEY" ] || grep -q '^OPENAI_API_KEY=.' .env && echo set`. If none is set, ask your
   operator for one. Never print, log or commit the key.
4. Append a line to `CHANNEL.md` saying who you are and where you run, commit, and push.

## Work loop

1. **Sync.** In `../jimix-coord`, run `git pull`.
   - If `PROTOCOL.md` changed since you last read it, re-read it before anything else.
     `git log -1 --format=%h -- PROTOCOL.md` shows its last change.
   - Read `BOARD.md` and the newest lines of `CHANNEL.md`.
   - On `main`, after `git pull`, re-read `AGENTS.md` and `docs/testing.md` if they changed
     (`git log -1 --format=%h -- AGENTS.md docs/testing.md`).
   - Run Python only through uv (`uv run ...`, `uvx ruff ...`), as `AGENTS.md` says.
2. **Claim.** Pick one `open` item. Prefer items marked `**Priority:** high`, then lower numbers.
   Anyone may mark an item high priority, with a one-line reason.
   - Set its **Status** to `claimed by <name> (<machine/session>) <date time>`.
   - Commit only that change as `claim B-NN`, then push.
   - If the push is rejected, run `git pull` (a merge, never a rebase), check whether someone claimed it
     first, and either push again or pick another item.
   - Hold one claim at a time.
   - Add a line `- **Last seen:** <date time>` to the item and keep it fresh (see Liveness).
3. **Work.** On `main`'s side, in your normal clone:
   - branch `b-NN-short-name` from an up-to-date `main`;
   - keep the change small and inside the item's scope;
   - run the unit tests, plus the real-portal check when the item touches portal behavior
     (`docs/testing.md`).
4. **Review, before committing.** Get the uncommitted change reviewed before you commit it. Scale the
   review to the change:
   - **0 reviewers:** only for trivial changes you're confident in (a typo, a doc line, a one-line fix
     with a test). Say "no review: <reason>" in the item's Findings.
   - **1 reviewer:** the normal case.
   - **2 or more:** for new mechanisms touching shared behavior, such as `fetch.py` rate limiting,
     `watchtower.py` states, the recipe format, or the database schema. Follow-up commits to a design that
     was already reviewed this way need only 1.

   **Default reviewer:** a review subagent you start yourself on your local diff (`git diff`, plus new
   files). It reads the change and runs the tests, and doesn't block anyone.

   **Without subagents** (or for a second opinion from another worker): push a snapshot to a branch named
   `review/<your-name>/B-NN`. That branch is only for review and is deleted afterwards. Then post
   `review: B-NN branch review/<your-name>/B-NN` in `CHANNEL.md`. The reviewer answers in `CHANNEL.md`
   with `ok` or concrete problems.

   Fix what reviewers find, then commit, recording who reviewed it in the commit message or the item's
   Findings. Reviewing is a valid task when you have nothing else to do.
5. **Deliver.**
   - `git pull` on `main`, merge your branch, run the tests again, and push.
   - Or open a PR instead of merging yourself (step 4's review still applies first). Then set the item's
     **Status** to `in review (PR #N)`. You may claim your next item meanwhile, but answer review comments
     on the PR. Anyone may merge a reviewed PR whose tests pass, and then sets the item to `done (<commit>)`.
6. **Report.** On `coord`:
   - set the item's **Status** to `done (<commit>)` and write what you learned under **Findings**;
   - add new items for problems you noticed;
   - commit and push.
7. **Repeat** from step 1. Don't stop after one item: keep taking the next one (or scouting) until the
   "Done means" goal above holds, then say so in `CHANNEL.md`.

**Joining an operation is a commitment.** Once you've joined, stay with it until it ends, not just for
one item. An operation ends when `PROTOCOL.md` marks it complete or replaces it with a new operation,
and you switch to the new one. With nothing to claim:
- scout or review (below);
- if even that runs dry, keep checking `coord` for new items every 10–15 minutes.

You leave earlier only when your operator says so. Then release your claim and post that you're leaving
in `CHANNEL.md`. Solo tasks don't carry this commitment.

**After an operation ends, stay on standby.** When `PROTOCOL.md` marks the operation complete, keep
checking `coord` every 30 minutes for at least 24 hours, until a new operation starts or your operator
stops you. Joined workers are meant to stay alive indefinitely.

### Staying alive and restarting

- **The Workers list.** The top of `BOARD.md` has a `## Workers` list with one line per worker:
  `- <name> | <machine/tool> | last seen <date time> | <claim or idle>`. Refresh your line at least every
  30 minutes, including while idle or on standby.
- **A worker is dead** when its last seen time is older than 90 minutes. Anyone may then post
  `@<name> appears dead` in `CHANNEL.md` and reopen that worker's claims (see Liveness). Its operator
  restarts it.
- **Restarting.** An operator restarts a worker, or starts a new one, by giving any agent this prompt:

  ```
  You are <name> (<machine/tool>), a worker in the Jimix coordination effort.
  Open the Jimix repository, read AGENTS.md, set up or update the coord worktree, and read PROTOCOL.md.
  Find your line in the Workers list in BOARD.md (add one if it's missing) and any items still
  claimed by you. Resume them if their Last seen is fresh enough, otherwise re-claim or pick new work.
  Post "rejoined" in CHANNEL.md and follow the work loop until the operator stops you.
  ```

  Use the same name as before so claims and history stay connected.

**Nothing open?** Don't wait. Scout instead: pick a portal no one has tried (see B-10), attach it, refresh
it, and file every problem you see as a new item. A scouting report is a `CHANNEL.md` message plus the
items it produced.

## Rules

- Never force-push, rebase published commits or rewrite history, on `coord` or `main`.
- Commit only coordination files on `coord`, and only code and docs on `main`.
- `CHANNEL.md` is append-only. Use one line per message:
  `- YYYY-MM-DD HH:MM <name>: <message>`. Use your real current time in CET (UTC+1), e.g. from
  `TZ=Europe/Berlin date '+%F %H:%M'`, since claim ages are judged by it. Address people with `@lead` or `@<name>`. On a merge conflict,
  keep both sides.
- **Liveness.** While you hold a claim, refresh its **Last seen** at least every 30 minutes: commit
  and push the updated time on `coord`. A claim whose **Last seen** is older than 90 minutes counts as
  dead. Anyone may then set it back to `open`, adding a note with the old claimer's findings so far, and
  must post that in `CHANNEL.md`. If you know you'll stop, release your claim yourself.
- Don't put secrets, API keys or raw logs that might contain them in any file.
- Keep model spend low: about $1 at most per attach, and `WATCHTOWER_MAX_DETAILS=10` while testing.
- **Found a problem while working?**
  - **Small and related** (you can fix it in under about 30 minutes): add it to `BOARD.md` already
    claimed by you and fix it right after your current item. This saves the round trip of someone else
    claiming it.
  - **Bigger or unrelated:** add it as `open` for others.
  - Either way, it goes on the board, so nobody else starts the same fix.
- **Adding an item:**
  1. `git pull`.
  2. Take the next number from the file, not from memory:
     `grep -oE '^### B-[0-9]+' BOARD.md | grep -oE '[0-9]+' | sort -n | tail -1` gives the highest; add 1.
  3. Commit the new item on its own and push it right away, like a claim. If the push is rejected,
     merge and check again, then renumber your item if its number is now taken.
- **Duplicate check before every push to `coord`:**
  `grep -oE '^### [BS]-[A-Za-z0-9-]+' BOARD.md | sort | uniq -d` must print nothing. If it prints a
  duplicate, renumber the newer item (the one added later in `git log`), fix references to it, and say so
  in `CHANNEL.md`.
  Also `grep -nE '^(<<<<<<<|=======|>>>>>>>)' *.md` must print nothing: no conflict markers left from a
  merge. The Workers list conflicts often because its lines sit next to each other. Keep both sides and
  take the newer `last seen` for each worker.
- Item format for new items:
  ```
  ### B-NN Short title
  - **Status:** open
  - **Problem:** what is wrong, with the portal URL and what you saw
  - **Idea:** optional
  - **Findings:**
  ```

## Changing this protocol

`PROTOCOL.md` is changed by the lead, or after a proposal in `CHANNEL.md` that the lead accepts.
Announce every change in `CHANNEL.md`. Everyone re-reads the file whenever it changes (Work loop, step 1).
The operation's goal can be replaced the same way when the focus moves to another task.

## Improving the process itself

The protocol is expected to get better as we use it.
- **Anyone who hits friction proposes a fix.** Examples: claim races, waiting around, vague items,
  merge pain, tests that are too slow, missing tooling. Post a `CHANNEL.md` line starting with
  `proposal:`, saying the problem and the change you suggest to this file, `BOARD.md`'s format,
  `docs/testing.md` or the tooling.
- **Tooling and docs go through the board.** Improvements to scripts, test helpers or docs on `main`
  are ordinary board items. File them like any other problem.
- **The lead reviews regularly.** At least whenever five items are done, the lead reads `CHANNEL.md` for
  proposals and friction, applies what helps, and posts what changed and why. Small fixes that make this
  file clearer without changing a rule (typos, examples, wording) may be made by anyone directly.
- **Silence doesn't block.** If the lead hasn't answered a proposal within 2 hours, two other workers
  agreeing in `CHANNEL.md` may apply it. They tag `@lead`.
- **The rules for change can change too.** This section and "Changing this protocol" can be improved
  the same way, as can who leads. Keep every change small and announced, so everyone can follow.
