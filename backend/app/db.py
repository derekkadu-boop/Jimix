import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("JIMIX_DB", "data/jimix.db"))
SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    # FastAPI can enter and exit the request dependency on different worker threads.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA.read_text())


def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()
