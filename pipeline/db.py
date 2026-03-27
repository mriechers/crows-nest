"""
SQLite database module for the Crow's Nest content preservation pipeline.

Provides schema initialization, connection helpers, and CRUD operations
for the links table (status-machine) and processing_log table.
"""

import os
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    url              TEXT NOT NULL UNIQUE,
    source_type      TEXT,
    sender           TEXT,
    context          TEXT,
    content_type     TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    download_path    TEXT,
    transcript_path  TEXT,
    obsidian_note_path TEXT,
    archive_path     TEXT,
    video_path       TEXT,
    error            TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    metadata         TEXT
);

CREATE TABLE IF NOT EXISTS processing_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id   INTEGER NOT NULL REFERENCES links(id),
    stage     TEXT NOT NULL,
    status    TEXT NOT NULL,
    message   TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_links_status
    ON links(status);

CREATE INDEX IF NOT EXISTS idx_links_content_type
    ON links(content_type);

CREATE INDEX IF NOT EXISTS idx_processing_log_link_id
    ON processing_log(link_id);

CREATE TABLE IF NOT EXISTS signal_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender      TEXT,
    body        TEXT,
    group_name  TEXT,
    has_urls    BOOLEAN DEFAULT 0,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_signal_messages_sender ON signal_messages(sender);
CREATE INDEX IF NOT EXISTS idx_signal_messages_received_at ON signal_messages(received_at);
"""


def _now() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: str = DB_PATH) -> None:
    """Create the database and tables. Idempotent — safe to call multiple times."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        # Migrate existing databases: add video_path if missing
        try:
            conn.execute("ALTER TABLE links ADD COLUMN video_path TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
    finally:
        conn.close()


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Return a connection with Row factory, WAL mode, and busy timeout set."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def add_link(
    url: str,
    source_type: str = "manual",
    sender: str = None,
    context: str = None,
    content_type: str = None,
    metadata: str = None,
    db_path: str = DB_PATH,
) -> int:
    """
    Insert a new link. Returns the rowid.
    Raises sqlite3.IntegrityError if the URL already exists.
    metadata should be a JSON string if provided.
    """
    now = _now()
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO links
                (url, source_type, sender, context, content_type, metadata, status, created_at, updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (url, source_type, sender, context, content_type, metadata, now, now),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_status(
    link_id: int,
    status: str,
    db_path: str = DB_PATH,
    **kwargs,
) -> None:
    """
    Update a link's status and updated_at timestamp.
    Additional keyword arguments are applied as column updates.
    """
    now = _now()
    fields = {"status": status, "updated_at": now}
    fields.update(kwargs)

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [link_id]

    conn = get_connection(db_path)
    try:
        conn.execute(
            f"UPDATE links SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def log_processing(
    link_id: int,
    stage: str,
    status: str,
    message: str,
    db_path: str = DB_PATH,
) -> None:
    """Insert a processing log entry for the given link."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO processing_log (link_id, stage, status, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (link_id, stage, status, message, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_pending(
    status: str = "pending",
    limit: int = 10,
    db_path: str = DB_PATH,
) -> list:
    """Return links with the given status, oldest first."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM links WHERE status = ? ORDER BY created_at ASC LIMIT ?",
            (status, limit),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def claim_link(
    link_id: int,
    from_status: str,
    to_status: str,
    db_path: str = DB_PATH,
) -> bool:
    """
    Atomically transition a link from from_status to to_status.
    Returns True if the row was updated, False if the status did not match.
    """
    now = _now()
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE links
            SET status = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (to_status, now, link_id, from_status),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()
