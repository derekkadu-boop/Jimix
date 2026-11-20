BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS portal (
    id                       TEXT PRIMARY KEY NOT NULL,
    url                      TEXT NOT NULL,
    normalized_url           TEXT NOT NULL UNIQUE,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    latest_exploration_status TEXT NOT NULL,
    latest_run_id            TEXT,
    selected_extraction_method TEXT,
    selected_recipe_id       TEXT REFERENCES extraction_recipe(id)
);

CREATE TABLE IF NOT EXISTS portal_exploration_config (
    portal_id                    TEXT PRIMARY KEY NOT NULL REFERENCES portal(id),
    max_tool_calls               INTEGER NOT NULL,
    max_llm_calls                INTEGER NOT NULL,
    max_http_requests            INTEGER NOT NULL,
    max_unique_pages             INTEGER NOT NULL,
    max_duration_seconds         REAL NOT NULL,
    max_input_tokens             INTEGER NOT NULL,
    max_output_tokens            INTEGER NOT NULL,
    max_estimated_cost_usd        REAL NOT NULL,
    max_response_bytes           INTEGER NOT NULL,
    max_document_probe_bytes     INTEGER NOT NULL,
    max_browser_navigations      INTEGER NOT NULL,
    max_documentation_depth      INTEGER NOT NULL,
    max_consecutive_failures     INTEGER NOT NULL,
    max_repeated_tool_calls      INTEGER NOT NULL,
    request_interval_seconds     REAL NOT NULL,
    http_timeout_seconds         REAL NOT NULL,
    llm_timeout_seconds          REAL NOT NULL,
    browser_timeout_seconds      REAL NOT NULL,
    user_agent                   TEXT NOT NULL,
    accept                       TEXT NOT NULL,
    accept_language              TEXT NOT NULL,
    ambiguous_robots             TEXT NOT NULL,
    max_redirects                INTEGER NOT NULL,
    max_json_depth               INTEGER NOT NULL,
    max_candidate_urls           INTEGER NOT NULL,
    max_html_chars               INTEGER NOT NULL,
    max_observation_chars        INTEGER NOT NULL,
    max_model_output_per_call    INTEGER NOT NULL,
    max_llm_retries              INTEGER NOT NULL,
    exact_input_token_count      INTEGER NOT NULL,
    model                        TEXT NOT NULL,
    input_usd_per_million        REAL NOT NULL,
    output_usd_per_million       REAL NOT NULL,
    updated_at                   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exploration_run (
    id                    TEXT PRIMARY KEY NOT NULL,
    portal_id             TEXT NOT NULL REFERENCES portal(id),
    status                TEXT NOT NULL,
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    stop_reason           TEXT,
    http_requests_used    INTEGER NOT NULL,
    unique_pages_used     INTEGER NOT NULL,
    tool_calls_used       INTEGER NOT NULL,
    llm_calls_used        INTEGER NOT NULL,
    input_tokens_used     INTEGER NOT NULL,
    output_tokens_used    INTEGER NOT NULL,
    estimated_cost_usd    REAL NOT NULL,
    duration_seconds      REAL NOT NULL,
    best_candidate_method TEXT NOT NULL,
    config_json           TEXT NOT NULL,
    progress_json         TEXT NOT NULL,
    result_json           TEXT,
    resumed_from          TEXT REFERENCES exploration_run(id)
);

CREATE TABLE IF NOT EXISTS exploration_step (
    id                 TEXT PRIMARY KEY NOT NULL,
    run_id             TEXT NOT NULL REFERENCES exploration_run(id),
    step_number        INTEGER NOT NULL,
    tool_name          TEXT NOT NULL,
    arguments_json     TEXT NOT NULL,
    observation_json   TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT NOT NULL,
    duration_ms        INTEGER NOT NULL,
    http_requests_used INTEGER NOT NULL,
    success            INTEGER NOT NULL,
    error              TEXT,
    UNIQUE (run_id, step_number)
);

CREATE TABLE IF NOT EXISTS extraction_recipe (
    id                 TEXT PRIMARY KEY NOT NULL,
    portal_id          TEXT NOT NULL REFERENCES portal(id),
    exploration_run_id TEXT NOT NULL UNIQUE REFERENCES exploration_run(id),
    method             TEXT NOT NULL,
    recipe_json        TEXT NOT NULL,
    validation_json    TEXT NOT NULL,
    verified           INTEGER NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_exploration_run_portal_id ON exploration_run (portal_id);
CREATE INDEX IF NOT EXISTS ix_exploration_step_run_id ON exploration_step (run_id);
CREATE INDEX IF NOT EXISTS ix_extraction_recipe_portal_id ON extraction_recipe (portal_id);

COMMIT;
