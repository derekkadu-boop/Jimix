import asyncio
import json
import logging
import os
import re
import sqlite3
import uuid
from collections import Counter

import httpx

from . import db
from .agent import Failed, Usage, explore
from .documents import fetch_documents
from .fetch import OVERSIZE_SKIP, Blocked, Deleted, Fetcher
from .recipe import Recipe, apply, check

RUN_TIMEOUT = float(os.environ.get("WATCHTOWER_RUN_TIMEOUT", "3600"))
MAX_DETAILS = int(os.environ["WATCHTOWER_MAX_DETAILS"]) if os.environ.get("WATCHTOWER_MAX_DETAILS") else None
log = logging.getLogger("watchtower")
SECRET = re.compile(r"sk-[\w-]{8,}")
_background: set[asyncio.Task] = set()


class Broken(Exception):
    pass


def start(portal_id: str) -> str:
    try:
        run_id = _new_run(portal_id, "read")
    except sqlite3.IntegrityError:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM run WHERE portal_id = ? AND finished_at IS NULL", (portal_id,)
            ).fetchone()
        return row["id"] if row else start(portal_id)
    task = asyncio.create_task(_cycle(portal_id, run_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return run_id


def recover() -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE run SET finished_at = ?, result = 'failed', reason = 'interrupted by a restart'"
            " WHERE finished_at IS NULL",
            (db.now(),),
        )
        pending = conn.execute("SELECT id FROM portal WHERE state IN ('integrating', 'healing')").fetchall()
    for row in pending:
        start(row["id"])


def _new_run(portal_id: str, kind: str) -> str:
    run_id = uuid.uuid4().hex
    log.info("portal %s: starting %s run %s", portal_id, kind, run_id)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO run (id, portal_id, kind, started_at) VALUES (?, ?, ?, ?)",
            (run_id, portal_id, kind, db.now()),
        )
    return run_id


def _finish(run_id: str, result: str, reason: str, usage: Usage) -> None:
    log.info("run %s %s: %s (%d model calls, $%.4f)", run_id, result, reason, usage.llm_calls, usage.cost_usd)
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE run SET finished_at = ?, result = ?, reason = ?,
                llm_calls = ?, input_tokens = ?, output_tokens = ?, cost_usd = ?
            WHERE id = ?
            """,
            (db.now(), result, reason, usage.llm_calls, usage.input_tokens, usage.output_tokens,
             usage.cost_usd, run_id),
        )


def _set_state(portal_id: str, state: str | None, reason: str = "") -> None:
    log.info("portal %s: state %s %s", portal_id, state or "unchanged", reason)
    with db.connect() as conn:
        conn.execute(
            "UPDATE portal SET state = COALESCE(?, state), reason = ? WHERE id = ?", (state, reason, portal_id)
        )


def _fail_open_runs(portal_id: str, reason: str) -> None:
    try:
        with db.connect() as conn:
            conn.execute(
                "UPDATE run SET finished_at = ?, result = 'failed', reason = ?"
                " WHERE portal_id = ? AND finished_at IS NULL",
                (db.now(), reason, portal_id),
            )
            conn.execute("UPDATE portal SET state = 'failed', reason = ? WHERE id = ?", (reason, portal_id))
    except sqlite3.Error:
        log.exception("could not record failure for portal %s", portal_id)


def _rate_limited(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _describe(exc: Exception) -> str:
    text = str(exc) if isinstance(exc, (Broken, Failed, Blocked)) else f"{type(exc).__name__}: {exc}"
    return SECRET.sub("[redacted]", text)[:300]


def _portal(portal_id: str) -> tuple[str, Recipe | None]:
    with db.connect() as conn:
        url = conn.execute("SELECT url FROM portal WHERE id = ?", (portal_id,)).fetchone()["url"]
        row = conn.execute("SELECT data FROM recipe WHERE portal_id = ? AND active", (portal_id,)).fetchone()
    return url, Recipe.model_validate_json(row["data"]) if row else None


def _save_recipe(portal_id: str, run_id: str, recipe: Recipe) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE recipe SET active = 0 WHERE portal_id = ?", (portal_id,))
        conn.execute(
            "INSERT INTO recipe (id, portal_id, run_id, data, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (uuid.uuid4().hex, portal_id, run_id, recipe.model_dump_json(), db.now()),
        )


def _save_tenders(portal_id: str, tenders: list[dict], close_missing: bool) -> None:
    now = db.now()
    rows = [(portal_id, t["id"], json.dumps(t, separators=(",", ":")), now) for t in tenders]
    ids = [t["id"] for t in tenders]
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO tender (portal_id, id, data, last_changed_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (portal_id, id) DO UPDATE SET data = excluded.data,
                last_changed_at = CASE WHEN tender.data = excluded.data
                    THEN tender.last_changed_at ELSE excluded.last_changed_at END
            """,
            rows,
        )
        if close_missing:
            conn.execute(
                f"""
                UPDATE tender SET data = json_set(data, '$.status', 'closed'), last_changed_at = ?
                WHERE portal_id = ? AND json_extract(data, '$.status') = 'open'
                    AND id NOT IN ({", ".join("?" * len(ids))})
                """,
                (now, portal_id, *ids),
            )


async def _deliver(portal_id: str, recipe: Recipe, fetcher: Fetcher) -> tuple[int, str | None, str]:
    tenders, complete = await apply(recipe, fetcher, max_details=MAX_DETAILS)
    with db.connect() as conn:
        rows = conn.execute("SELECT id, data FROM tender WHERE portal_id = ?", (portal_id,)).fetchall()
    known = {row["id"]: json.loads(row["data"]) for row in rows}
    if problem := check(tenders, sum(t["status"] == "open" for t in known.values())):
        raise Broken(problem)
    fetched = cached = blocked = blocked_pages = failed_pages = missing = pending = 0
    skips: Counter[str] = Counter()
    reason = None
    for tender in tenders:
        old = known.get(tender["id"])
        tender.pop("_documentChecked", None)
        if tender.pop("_documentPageError", False):
            if old:
                tender["documents"] = old.get("documents", [])
                tender["_documentUrls"] = old.get("_documentUrls", [])
                if "_documentSkips" in old:
                    tender["_documentSkips"] = old["_documentSkips"]
            tender["_documentsComplete"] = False
            failed_pages += 1
            continue
        if why := tender.pop("_documentBlock", None):
            tender["documents"] = old.get("documents", []) if old else []
            tender["_documentsComplete"] = False
            fetched += 1
            blocked += 1
            blocked_pages += 1
            reason = why
            continue
        urls = tender["_documentUrls"]
        missing += not urls
        same_urls = old and old.get("_documentUrls") == urls
        if same_urls and old.get("_documentsComplete") and (not urls or "_documentSkips" in old):
            tender["documents"], tender["_documentsComplete"] = old["documents"], True
            if "_documentSkips" in old:
                tender["_documentSkips"] = old["_documentSkips"]
                skips.update(old["_documentSkips"].values())
            cached += bool(old["documents"])
            continue
        all_blocked = not recipe.documents_by_host and blocked >= 5 and blocked == fetched
        if all_blocked or (MAX_DETAILS is not None and fetched >= MAX_DETAILS):
            if same_urls:
                tender["documents"] = old["documents"]
                if "_documentSkips" in old:
                    tender["_documentSkips"] = old["_documentSkips"]
            tender["_documentsComplete"] = False
            continue
        previous_skips = old.get("_documentSkips", {}) if old else {}
        tender_skips = {url: OVERSIZE_SKIP for url in urls if previous_skips.get(url) == OVERSIZE_SKIP}
        docs, done, why = await fetch_documents(urls, fetcher, skipped=tender_skips)
        if not done and same_urls:
            kept = {
                (doc["sha256"], doc["fileName"]): doc
                for doc in [*old.get("documents", []), *docs]
            }
            docs = list(kept.values())
        tender["documents"], tender["_documentsComplete"] = docs, done
        if urls:
            tender["_documentSkips"] = tender_skips
            skips.update(tender_skips.values())
        pending += not done
        fetched += bool(urls)
        if why:
            blocked, reason = blocked + 1, why
    _save_tenders(portal_id, tenders, close_missing=complete)
    attempted = fetched + cached if recipe.documents_by_host else fetched
    notes = []
    if skips:
        notes.append("Skipped documents: " + ", ".join(f"{reason} ({n})" for reason, n in sorted(skips.items())))
    if blocked and blocked * 2 >= attempted:
        failure = f"documents: {reason} ({blocked} of {attempted} tenders)"
        return len(tenders), ". ".join([failure, *notes]), ""
    if blocked_pages:
        notes.append(f"Document pages blocked for {blocked_pages} of {len(tenders)} tenders")
    if failed_pages:
        notes.append(f"Could not read document pages for {failed_pages} of {len(tenders)} tenders")
    if missing:
        notes.append(f"No document links found for {missing} of {len(tenders)} tenders")
    if pending:
        log.info("portal %s: documents incomplete for %d tenders, %d MB downloaded", portal_id, pending,
                 fetcher.downloaded // 2**20)
    return len(tenders), None, ". ".join(notes)


async def _cycle(portal_id: str, run_id: str) -> None:
    fetcher = Fetcher(portal_id)
    try:
        async with asyncio.timeout(RUN_TIMEOUT):
            await _read(portal_id, run_id, fetcher)
    except Deleted:
        pass
    except TimeoutError:
        _fail_open_runs(portal_id, f"run took longer than {RUN_TIMEOUT:.0f}s")
    except Exception as exc:
        log.exception("run for portal %s crashed", portal_id)
        _fail_open_runs(portal_id, f"internal error: {_describe(exc)}")
    finally:
        await fetcher.close()


def _finish_delivery(portal_id: str, run_id: str, usage: Usage, count: int, doc_block, note: str) -> None:
    if doc_block:
        _finish(run_id, "failed", doc_block, usage)
        _set_state(portal_id, "failed", f"Blocked: {doc_block}")
    else:
        summary = f"read {count} tenders"
        _finish(run_id, "ok", f"{summary}. {note}" if note else summary, usage)
        _set_state(portal_id, "serving", note)


async def _read(portal_id: str, run_id: str, fetcher: Fetcher) -> None:
    url, recipe = _portal(portal_id)
    fresh, usage = recipe is None, Usage()
    try:
        if fresh:
            recipe = await explore(url, fetcher, usage)
        count, doc_block, docs_note = await _deliver(portal_id, recipe, fetcher)
        if fresh:
            _save_recipe(portal_id, run_id, recipe)
    except Blocked as exc:
        _finish(run_id, "failed", _describe(exc), usage)
        _set_state(portal_id, "failed", f"Blocked: {_describe(exc)}")
        return
    except Exception as exc:  # noqa: BLE001
        problem = _describe(exc)
        _finish(run_id, "failed", problem, usage)
        if fresh:
            _set_state(portal_id, "failed", f"Could not attach: {problem}")
        elif _rate_limited(exc):
            _set_state(portal_id, None, "Last read was rate limited (HTTP 429)")
        else:
            _set_state(portal_id, "healing", f"Read failed: {problem}. Repairing.")
            await _repair(portal_id, url, recipe, problem, fetcher)
        return
    _finish_delivery(portal_id, run_id, usage, count, doc_block, docs_note)


async def _repair(portal_id: str, url: str, old: Recipe, problem: str, fetcher: Fetcher) -> None:
    run_id = _new_run(portal_id, "repair")
    usage = Usage()
    hint = (
        f"This portal was read with the recipe below until it failed with: {problem}\n"
        f"Find out what changed and write a new recipe.\n{old.model_dump_json()}"
    )
    try:
        recipe = await explore(url, fetcher, usage, hint)
        count, doc_block, docs_note = await _deliver(portal_id, recipe, fetcher)
        _save_recipe(portal_id, run_id, recipe)
    except Blocked as exc:
        _finish(run_id, "failed", _describe(exc), usage)
        _set_state(portal_id, "failed", f"Blocked: {_describe(exc)}")
        return
    except Exception as exc:  # noqa: BLE001
        _finish(run_id, "failed", _describe(exc), usage)
        _set_state(portal_id, "failed", f"Repair failed after '{problem}': {_describe(exc)}")
        return
    _finish_delivery(portal_id, run_id, usage, count, doc_block, f"Repaired after: {problem}. {docs_note}".strip())
