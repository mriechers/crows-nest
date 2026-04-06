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
