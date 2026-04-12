-- Ingest queue: links land here from any intake channel,
-- then get drained into the local pipeline DB by the poller.

CREATE TABLE IF NOT EXISTS ingest_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT    NOT NULL,
    context    TEXT,
    source     TEXT    NOT NULL DEFAULT 'shortcut',
    created_at TEXT    NOT NULL,
    synced     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ingest_queue_synced
    ON ingest_queue(synced);

-- Generalized job queue for cross-submodule coordination.
-- Payloads are POINTERS ONLY — IDs, batch refs, triggers. NEVER content.
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT    PRIMARY KEY,
    type       TEXT    NOT NULL,
    payload    TEXT    NOT NULL DEFAULT '{}',
    status     TEXT    NOT NULL DEFAULT 'pending',
    priority   INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    claimed_at TEXT,
    completed_at TEXT,
    error      TEXT,
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_type   ON jobs(type);

-- Node heartbeats: which machines are alive and what they can do.
CREATE TABLE IF NOT EXISTS nodes (
    node_id       TEXT PRIMARY KEY,
    hostname      TEXT,
    capabilities  TEXT NOT NULL DEFAULT '[]',
    last_heartbeat TEXT NOT NULL,
    current_job   TEXT
);
