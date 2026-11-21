# Protocol

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
the PDF's judging describes. Until it does, keep going: after each item, pick the next one or scout.

**Focus right now: documents.** Delivering each tender's actual document files is the core feature and
our weakest point (see the `Priority: high` items). Work on document delivery first; side features (the
dashboard, cost reporting, log streaming) only when no document item is open.

**How we get there:** everyone runs real portals, files each problem they find as a small item on the
board, and fixes items one at a time, with tests.

**Lead:** the main Claude session on matiiss's machine. The lead only keeps this file (the meta layer).
The board belongs to everyone: anyone adds items, picks them, sets priorities and finishes them without
asking. Anyone can propose changes to this file in `CHANNEL.md`.

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
2. **Claim.** Pick one `open` item. Prefer items marked `**Priority:** high`, then lower numbers.
   Anyone may mark an item high priority, with a one-line reason.
   - Set its **Status** to `claimed by <name> (<machine/session>) <date time>`.
   - Commit only that change as `claim B-NN`, then push.
   - If the push is rejected, run `git pull` (a merge, never a rebase), check whether someone claimed it
     first, and either push again or pick another item.
   - Hold one claim at a time.
3. **Work.** On `main`'s side, in your normal clone:
   - branch `b-NN-short-name` from an up-to-date `main`;
   - keep the change small and inside the item's scope;
   - run the unit tests, plus the real-portal check when the item touches portal behavior
     (`docs/testing.md`).
4. **Deliver.**
   - `git pull` on `main`, merge your branch, run the tests again, and push.
   - Or open a PR if you want review.
5. **Report.** On `coord`:
   - set the item's **Status** to `done (<commit>)` and write what you learned under **Findings**;
   - add new items for problems you noticed;
   - commit and push.
6. **Repeat** from step 1. Don't stop after one item: keep taking the next one (or scouting) until the
   "Done means" goal above holds, then say so in `CHANNEL.md`.

**Nothing open?** Don't wait. Scout instead: pick a portal no one has tried (see B-10), attach it, refresh
it, and file every problem you see as a new item. A scouting report is a `CHANNEL.md` message plus the
items it produced.

## Rules

- Never force-push, rebase published commits or rewrite history, on `coord` or `main`.
- Commit only coordination files on `coord`, and only code and docs on `main`.
- `CHANNEL.md` is append-only. Use one line per message:
  `- YYYY-MM-DD HH:MM <name>: <message>`. Address people with `@lead` or `@<name>`. On a merge conflict,
  keep both sides.
- A claim with no update (commit, push or channel message) for 3 hours may be taken over. Say so in
  `CHANNEL.md` first.
- Don't put secrets, API keys or raw logs that might contain them in any file.
- Keep model spend low: about $1 at most per attach, and `WATCHTOWER_MAX_DETAILS=10` while testing.
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
