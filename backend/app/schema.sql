CREATE TABLE IF NOT EXISTS portal (
    id         TEXT PRIMARY KEY,
    url        TEXT NOT NULL UNIQUE,
    state      TEXT NOT NULL DEFAULT 'integrating',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tender (
    portal_id            TEXT NOT NULL REFERENCES portal(id) ON DELETE CASCADE,
    id                   TEXT NOT NULL,
    url                  TEXT,
    title                TEXT,
    buyer_name           TEXT,
    publication_date     TEXT,
    submission_deadline  TEXT,
    status               TEXT,
    reference_number     TEXT,
    description          TEXT,
    procedure_type       TEXT,
    contract_nature      TEXT,
    cpv_codes            TEXT,  -- JSON list
    place_of_performance TEXT,  -- JSON object
    last_changed_at      TEXT NOT NULL,
    PRIMARY KEY (portal_id, id)
);

CREATE TABLE IF NOT EXISTS run (
    id          TEXT PRIMARY KEY,
    portal_id   TEXT NOT NULL REFERENCES portal(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('read', 'repair')),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    result      TEXT CHECK (result IN ('ok', 'failed'))
);
