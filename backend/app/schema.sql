CREATE TABLE IF NOT EXISTS portal (
    id         TEXT PRIMARY KEY,
    url        TEXT NOT NULL UNIQUE,
    state      TEXT NOT NULL DEFAULT 'integrating'
               CHECK (state IN ('integrating', 'serving', 'healing', 'failed')),
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_location (
    portal_id    TEXT PRIMARY KEY NOT NULL REFERENCES portal(id) ON DELETE CASCADE,
    city         TEXT,
    country      TEXT,
    country_code TEXT,
    latitude     REAL,
    longitude    REAL
);

CREATE TABLE IF NOT EXISTS run (
    id            TEXT PRIMARY KEY,
    portal_id     TEXT NOT NULL REFERENCES portal(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('read', 'repair')),
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    result        TEXT CHECK (result IN ('ok', 'failed')),
    reason        TEXT NOT NULL DEFAULT '',
    llm_calls     INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS one_unfinished_run ON run (portal_id) WHERE finished_at IS NULL;

CREATE TABLE IF NOT EXISTS recipe (
    id         TEXT PRIMARY KEY,
    portal_id  TEXT NOT NULL REFERENCES portal(id) ON DELETE CASCADE,
    run_id     TEXT REFERENCES run(id) ON DELETE SET NULL,
    data       TEXT NOT NULL,  -- JSON
    active     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_recipe ON recipe (portal_id) WHERE active;

CREATE TABLE IF NOT EXISTS tender (
    portal_id       TEXT NOT NULL REFERENCES portal(id) ON DELETE CASCADE,
    id              TEXT NOT NULL,
    data            TEXT NOT NULL,  -- JSON
    last_changed_at TEXT NOT NULL,
    PRIMARY KEY (portal_id, id)
);
