import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import SQLITE_BUSY, SQLITE_LOCKED
from uuid import uuid4

import aiosqlite

from .models import ExplorationConfig, ExplorationResult
from .urls import sanitize

SCHEMA = Path(__file__).with_name("schema.sql")
CONFIG_FIELDS = tuple(ExplorationConfig.model_fields)
CONFIG_COLUMNS = ", ".join(CONFIG_FIELDS)
CONFIG_PARAMETERS = ", ".join(f":{name}" for name in CONFIG_FIELDS)
CONFIG_ASSIGNMENTS = ", ".join(f"{name} = :{name}" for name in CONFIG_FIELDS)
RUN_USAGE_ASSIGNMENTS = """
    http_requests_used = :http_requests, unique_pages_used = :unique_pages,
    tool_calls_used = :tool_calls, llm_calls_used = :llm_calls, input_tokens_used = :input_tokens,
    output_tokens_used = :output_tokens, estimated_cost_usd = :estimated_cost_usd
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    # Match timestamps already stored in SQLite so runs can be ordered across versions.
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


async def _execute(db: aiosqlite.Connection, sql: str, parameters=()) -> int:
    async with db.execute(sql, parameters) as cursor:
        return cursor.rowcount


async def _one(db: aiosqlite.Connection, sql: str, parameters=()) -> aiosqlite.Row | None:
    async with db.execute(sql, parameters) as cursor:
        return await cursor.fetchone()


async def _read_config(db: aiosqlite.Connection, portal_id: str) -> ExplorationConfig | None:
    row = await _one(
        db, f"SELECT {CONFIG_COLUMNS} FROM portal_exploration_config WHERE portal_id = ?", (portal_id,)
    )
    return ExplorationConfig.model_validate(dict(row)) if row is not None else None


class ExplorationStore:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or os.environ.get("WATCHTOWER_DB", "data/watchtower.db"))
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _connection(self):
        # Keep one connection per store, including for an in-memory database.
        # Hold the lock until the current transaction finishes.
        async with self._lock:
            if self._db is None:
                db = aiosqlite.connect(self.path, isolation_level=None, timeout=5)
                try:
                    await db
                    db.row_factory = aiosqlite.Row
                    await _execute(db, "PRAGMA foreign_keys = ON")
                except BaseException:
                    await db.close()
                    raise
                self._db = db
            yield self._db

    @asynccontextmanager
    async def _transaction(self, *, write: bool = True):
        async with self._connection() as db:
            try:
                await _execute(db, "BEGIN IMMEDIATE" if write else "BEGIN")
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def initialize(self):
        async with self._connection() as db:
            try:
                await _execute(db, "PRAGMA journal_mode = WAL")
            except aiosqlite.OperationalError as exc:
                # A concurrent opener can hold the lock needed to change journal mode.
                # Schema creation still uses SQLite's busy timeout.
                if exc.sqlite_errorcode not in {SQLITE_BUSY, SQLITE_LOCKED}:
                    raise
            try:
                async with db.executescript(SCHEMA.read_text(encoding="utf-8")):
                    pass
            except BaseException:
                await db.rollback()
                raise

    async def close(self):
        async with self._lock:
            if self._db is not None:
                db, self._db = self._db, None
                await db.close()

    async def start(
        self, url: str, overrides: dict, resumed_from: str | None
    ) -> tuple[str, str, ExplorationConfig]:
        async with self._transaction() as db:
            now = _timestamp(utcnow())
            await _execute(
                db,
                """
                INSERT INTO portal (id, url, normalized_url, created_at, updated_at, latest_exploration_status)
                VALUES (?, ?, ?, ?, ?, 'running') ON CONFLICT (normalized_url) DO NOTHING
                """,
                (uuid4().hex, url, url, now, now),
            )
            portal = await _one(db, "SELECT id FROM portal WHERE normalized_url = ?", (url,))
            portal_id = portal["id"]
            saved = await _read_config(db, portal_id)
            if saved is None:
                saved = ExplorationConfig()
                await _execute(
                    db,
                    f"""
                    INSERT INTO portal_exploration_config (portal_id, updated_at, {CONFIG_COLUMNS})
                    VALUES (:portal_id, :updated_at, {CONFIG_PARAMETERS})
                    """,
                    saved.model_dump() | {"portal_id": portal_id, "updated_at": now},
                )
            config = ExplorationConfig.model_validate(saved.model_dump() | overrides)
            if resumed_from:
                prior = await _one(db, "SELECT portal_id FROM exploration_run WHERE id = ?", (resumed_from,))
                if prior is None or prior["portal_id"] != portal_id:
                    raise ValueError("Resume requires a prior run for the same portal")
            run_id = uuid4().hex
            await _execute(
                db,
                """
                INSERT INTO exploration_run (
                    id, portal_id, status, started_at, resumed_from, config_json, progress_json,
                    http_requests_used, unique_pages_used, tool_calls_used, llm_calls_used, input_tokens_used,
                    output_tokens_used, estimated_cost_usd, duration_seconds, best_candidate_method
                ) VALUES (?, ?, 'running', ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 'unknown')
                """,
                (run_id, portal_id, now, resumed_from, config.model_dump_json(), json.dumps({"url": url})),
            )
            await _execute(
                db,
                """
                UPDATE portal SET latest_exploration_status = 'running', latest_run_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (run_id, now, portal_id),
            )
        return portal_id, run_id, config

    async def get_portal_config(self, portal_id: str) -> ExplorationConfig:
        async with self._connection() as db:
            config = await _read_config(db, portal_id)
            if config is None:
                raise KeyError(portal_id)
            return config

    async def update_portal_config(self, portal_id: str, updates: dict) -> ExplorationConfig:
        async with self._transaction() as db:
            saved = await _read_config(db, portal_id)
            if saved is None:
                raise KeyError(portal_id)
            config = ExplorationConfig.model_validate(saved.model_dump() | updates)
            await _execute(
                db,
                f"""
                UPDATE portal_exploration_config SET updated_at = :updated_at, {CONFIG_ASSIGNMENTS}
                WHERE portal_id = :portal_id
                """,
                config.model_dump() | {"portal_id": portal_id, "updated_at": _timestamp(utcnow())},
            )
        return config

    async def checkpoint(self, run_id: str, progress: dict, step: dict | None = None):
        async with self._transaction() as db:
            changed = await _execute(
                db,
                f"""
                UPDATE exploration_run SET progress_json = :progress_json, duration_seconds = :duration_seconds,
                    best_candidate_method = :best_candidate_method, {RUN_USAGE_ASSIGNMENTS}
                WHERE id = :run_id AND status = 'running'
                """,
                {
                    "run_id": run_id,
                    "progress_json": json.dumps(sanitize(progress)),
                    "duration_seconds": progress["duration_seconds"],
                    "best_candidate_method": progress["best_candidate_method"],
                    **progress["usage"],
                },
            )
            if changed and step is not None:
                await _execute(
                    db,
                    """
                    INSERT INTO exploration_step (
                        id, run_id, step_number, tool_name, arguments_json, observation_json, started_at,
                        finished_at, duration_ms, http_requests_used, success, error
                    ) VALUES (
                        :id, :run_id, :step_number, :tool_name, :arguments_json, :observation_json, :started_at,
                        :finished_at, :duration_ms, :http_requests_used, :success, :error
                    )
                    """,
                    step
                    | {
                        "id": uuid4().hex,
                        "run_id": run_id,
                        "arguments_json": json.dumps(step["arguments_json"]),
                        "observation_json": json.dumps(step["observation_json"]),
                        "started_at": _timestamp(step["started_at"]),
                        "finished_at": _timestamp(step["finished_at"]),
                    },
                )

    async def finish(self, result: ExplorationResult, resume_checkpoint: dict | None = None):
        payload = sanitize(result.model_dump(mode="json"))
        payload["configuration"] = result.configuration.model_dump(mode="json")
        now = _timestamp(utcnow())
        async with self._transaction() as db:
            changed = await _execute(
                db,
                f"""
                UPDATE exploration_run SET status = :status, finished_at = :finished_at, stop_reason = :stop_reason,
                    duration_seconds = :duration_seconds, best_candidate_method = :best_candidate_method,
                    result_json = :result_json, progress_json = :progress_json, {RUN_USAGE_ASSIGNMENTS}
                WHERE id = :run_id AND status = 'running'
                """,
                {
                    "run_id": result.exploration_id,
                    "status": result.status,
                    "stop_reason": result.stop_reason.value,
                    "finished_at": now,
                    "duration_seconds": result.duration_seconds,
                    "best_candidate_method": result.best_candidate_method.value,
                    "result_json": json.dumps(payload),
                    "progress_json": json.dumps({"resume_checkpoint": resume_checkpoint or {}}),
                    **payload["usage"],
                },
            )
            if not changed:
                return
            await _execute(
                db,
                """
                UPDATE portal SET latest_exploration_status = ?, updated_at = ?
                WHERE id = ? AND latest_run_id = ?
                """,
                (result.status, now, result.portal_id, result.exploration_id),
            )
            if not result.recipe:
                return
            recipe_id = uuid4().hex
            await _execute(
                db,
                """
                INSERT INTO extraction_recipe (
                    id, portal_id, exploration_run_id, method, recipe_json, validation_json,
                    verified, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipe_id,
                    result.portal_id,
                    result.exploration_id,
                    result.recipe.method.value,
                    json.dumps(payload["recipe"]),
                    json.dumps(payload["validation"] or {}),
                    result.candidate_verified,
                    now,
                    now,
                ),
            )
            if result.status == "succeeded" and result.candidate_verified:
                await _execute(
                    db,
                    """
                    UPDATE portal SET selected_recipe_id = :recipe_id, selected_extraction_method = :method
                    WHERE id = :portal_id AND NOT EXISTS (
                        SELECT 1 FROM extraction_recipe AS previous
                        JOIN exploration_run AS prior ON prior.id = previous.exploration_run_id
                        JOIN exploration_run AS current ON current.id = :run_id
                        WHERE previous.id = portal.selected_recipe_id AND prior.started_at > current.started_at
                    )
                    """,
                    {
                        "recipe_id": recipe_id,
                        "method": result.best_candidate_method.value,
                        "portal_id": result.portal_id,
                        "run_id": result.exploration_id,
                    },
                )

    async def get_exploration_progress(self, run_id: str) -> dict:
        async with self._transaction(write=False) as db:
            run = await _one(
                db,
                "SELECT portal_id, status, config_json, progress_json, result_json FROM exploration_run WHERE id = ?",
                (run_id,),
            )
            if run is None:
                raise KeyError(run_id)
            steps = await db.execute_fetchall(
                "SELECT * FROM exploration_step WHERE run_id = ? ORDER BY step_number", (run_id,)
            )
        progress = json.loads(run["progress_json"]) | (json.loads(run["result_json"] or "null") or {})
        return progress | {
            "exploration_id": run_id,
            "portal_id": run["portal_id"],
            "status": run["status"],
            "configuration": json.loads(run["config_json"]),
            "steps": [
                {
                    "number": step["step_number"],
                    "tool": step["tool_name"],
                    "success": bool(step["success"]),
                    "arguments": json.loads(step["arguments_json"]),
                    "observation": json.loads(step["observation_json"]),
                    "error": step["error"],
                    "started_at": datetime.fromisoformat(step["started_at"]).isoformat(),
                    "finished_at": datetime.fromisoformat(step["finished_at"]).isoformat(),
                    "duration_ms": step["duration_ms"],
                    "http_requests_used": step["http_requests_used"],
                }
                for step in steps
            ],
        }
