"""
SQLite database module for the Crow's Nest content preservation pipeline.

Provides schema initialization, connection helpers, and CRUD operations
for the links table (status-machine) and processing_log table.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

try:
    from pipeline.config import DB_PATH
except ImportError:
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
    share_url        TEXT,
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

"""

RSS_SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    title           TEXT,
    tier            INTEGER NOT NULL DEFAULT 2,
    category        TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    added_at        TEXT NOT NULL,
    last_fetched_at TEXT,
    last_error      TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id       INTEGER NOT NULL REFERENCES feeds(id),
    guid          TEXT NOT NULL UNIQUE,
    title         TEXT,
    url           TEXT,
    summary       TEXT,
    author        TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    score         REAL NOT NULL DEFAULT 0.0,
    surfaced      INTEGER NOT NULL DEFAULT 0,
    surfaced_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(score);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_surfaced ON articles(surfaced);
CREATE INDEX IF NOT EXISTS idx_feeds_active ON feeds(active);
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
        conn.executescript(RSS_SCHEMA)
        conn.commit()
        # Migrate existing databases: add columns if missing
        for col in ("video_path TEXT", "share_url TEXT"):
            try:
                conn.execute(f"ALTER TABLE links ADD COLUMN {col}")
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


# ---------------------------------------------------------------------------
# RSS feed and article functions
# ---------------------------------------------------------------------------


def add_feed(
    url: str,
    title: str = None,
    tier: int = 2,
    category: str = None,
    db_path: str = DB_PATH,
) -> int:
    """
    Insert a new feed. Returns the rowid.
    Deduplicates by URL — if the feed already exists, returns its existing ID
    without modifying it.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO feeds (url, title, tier, category, added_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (url, title, tier, category, _now()),
        )
        conn.commit()
        if cursor.lastrowid and cursor.lastrowid > 0 and cursor.rowcount == 1:
            return cursor.lastrowid
        # Row already existed — fetch the existing id
        cursor = conn.execute("SELECT id FROM feeds WHERE url = ?", (url,))
        return cursor.fetchone()["id"]
    finally:
        conn.close()


def list_feeds(
    active_only: bool = True,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Return all feeds, optionally filtering to active ones only."""
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM feeds"
        params: tuple = ()
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY added_at ASC"
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def add_article(
    feed_id: int,
    guid: str,
    title: str,
    url: str,
    summary: str,
    published_at: str,
    score: float = 0.0,
    author: str = None,
    db_path: str = DB_PATH,
) -> int | None:
    """
    Insert a new article. Returns the rowid, or None if the guid already exists.
    Deduplicates by guid — duplicate guids are silently ignored.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO articles
                (feed_id, guid, title, url, summary, author, published_at, fetched_at, score)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (feed_id, guid, title, url, summary, author, published_at, _now(), score),
        )
        conn.commit()
        if cursor.rowcount == 1:
            return cursor.lastrowid
        return None
    finally:
        conn.close()


def get_top_articles(
    limit: int = 8,
    max_age_days: int = 2,
    db_path: str = DB_PATH,
) -> list[dict]:
    """
    Return unsurfaced articles ordered by score descending.
    Only includes articles whose published_at is within max_age_days of now.
    Joins with feeds to include feed title and tier.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT
                a.*,
                f.title AS feed_title,
                f.tier  AS feed_tier
            FROM articles a
            JOIN feeds f ON f.id = a.feed_id
            WHERE a.surfaced = 0
              AND a.published_at >= ?
            ORDER BY a.score DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def mark_articles_surfaced(
    article_ids: list[int],
    db_path: str = DB_PATH,
) -> None:
    """Mark the given article IDs as surfaced so they are excluded from future queries."""
    if not article_ids:
        return
    now = _now()
    placeholders = ", ".join("?" for _ in article_ids)
    conn = get_connection(db_path)
    try:
        conn.execute(
            f"UPDATE articles SET surfaced = 1, surfaced_date = ? WHERE id IN ({placeholders})",
            [now] + list(article_ids),
        )
        conn.commit()
    finally:
        conn.close()


def get_pipeline_status(
    recent_limit: int = 20,
    db_path: str = DB_PATH,
) -> dict:
    """Return pipeline queue status: non-done items, recent completions, and counts."""
    conn = get_connection(db_path)
    try:
        # Queue: all non-done items
        queue_rows = conn.execute(
            """SELECT id, url, source_type, sender, context, content_type, status,
                      created_at, updated_at, error, retry_count
               FROM links
               WHERE status != 'done'
               ORDER BY created_at ASC""",
        ).fetchall()

        # Recent completions
        recent_rows = conn.execute(
            """SELECT id, url, source_type, sender, content_type, status,
                      created_at, updated_at, obsidian_note_path, share_url
               FROM links
               WHERE status = 'done'
               ORDER BY updated_at DESC
               LIMIT ?""",
            (recent_limit,),
        ).fetchall()

        # Counts by status
        count_rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM links GROUP BY status"
        ).fetchall()
        counts = {row["status"]: row["count"] for row in count_rows}

        return {
            "queue": [dict(r) for r in queue_rows],
            "recent": [dict(r) for r in recent_rows],
            "counts": counts,
        }
    finally:
        conn.close()


def expire_old_articles(
    max_age_days: int = 14,
    db_path: str = DB_PATH,
) -> int:
    """
    Delete articles older than max_age_days based on published_at.
    Returns the number of rows deleted.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM articles WHERE published_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
