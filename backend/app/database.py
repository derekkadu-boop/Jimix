"""Small SQLite persistence layer for Watchtower's Stage 1 data."""
import os
import sqlite3
from pathlib import Path


DATABASE_PATH = os.getenv("WATCHTOWER_DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "watchtower.sqlite3"))


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DATABASE_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize_database() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS portals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL DEFAULT 'integrating',
            reason TEXT,
            tender_count INTEGER NOT NULL DEFAULT 0,
            last_success_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tenders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portal_id INTEGER NOT NULL REFERENCES portals(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            reference TEXT,
            url TEXT,
            changed_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portal_id INTEGER NOT NULL REFERENCES portals(id) ON DELETE CASCADE,
            tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
            url TEXT,
            title TEXT,
            content_type TEXT,
            stored_path TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portal_id INTEGER NOT NULL REFERENCES portals(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'running',
            created_at TEXT NOT NULL,
            finished_at TEXT,
            detail TEXT
        );
        CREATE INDEX IF NOT EXISTS runs_portal_status ON runs(portal_id, status);
        """)
        # Upgrade databases created before changed_at was introduced.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(tenders)")}
        if "changed_at" not in columns:
            db.execute("ALTER TABLE tenders ADD COLUMN changed_at TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS tenders_portal_changed ON tenders(portal_id, changed_at)")
