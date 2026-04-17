"""Tests for pipeline_queue tool."""
import sqlite3
from datetime import datetime

import pytest


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary pipeline DB with test data."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            source_type TEXT,
            sender TEXT,
            context TEXT,
            content_type TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            download_path TEXT,
            transcript_path TEXT,
            obsidian_note_path TEXT,
            archive_path TEXT,
            video_path TEXT,
            share_url TEXT,
            error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            metadata TEXT
        );
    """)
    now = datetime.now().isoformat()
    conn.executemany(
        """INSERT INTO links (url, source_type, sender, content_type, status, created_at, updated_at, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("https://youtube.com/watch?v=abc", "signal", "Mark", "youtube", "pending", now, now, None),
            ("https://example.com/article", "signal", "Mark", "article", "processing", now, now, None),
            ("https://broken.com/page", "signal", "Mark", "article", "error", now, now, "timeout"),
            ("https://done.com/video", "signal", "Mark", "youtube", "archived", now, now, None),
            ("https://done2.com/article", "signal", "Mark", "article", "archived", now, now, None),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_get_pipeline_status_returns_structure(db_path):
    from pipeline.db import get_pipeline_status
    result = get_pipeline_status(db_path=db_path)
    assert "queue" in result
    assert "recent" in result
    assert "counts" in result


def test_queue_excludes_done(db_path):
    from pipeline.db import get_pipeline_status
    result = get_pipeline_status(db_path=db_path)
    statuses = {item["status"] for item in result["queue"]}
    assert "archived" not in statuses
    assert len(result["queue"]) == 3  # pending + processing + error


def test_recent_only_done(db_path):
    from pipeline.db import get_pipeline_status
    result = get_pipeline_status(db_path=db_path)
    assert all(item["status"] == "archived" for item in result["recent"])
    assert len(result["recent"]) == 2


def test_counts_correct(db_path):
    from pipeline.db import get_pipeline_status
    result = get_pipeline_status(db_path=db_path)
    assert result["counts"]["pending"] == 1
    assert result["counts"]["processing"] == 1
    assert result["counts"]["error"] == 1
    assert result["counts"]["archived"] == 2


def test_recent_limit(db_path):
    from pipeline.db import get_pipeline_status
    result = get_pipeline_status(recent_limit=1, db_path=db_path)
    assert len(result["recent"]) == 1
