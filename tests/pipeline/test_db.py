import sys
import os
import sqlite3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import db


def test_init_db_creates_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "links" in tables
    assert "processing_log" in tables


def test_init_db_is_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)
    db.init_db(db_path)  # should not raise

    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='links'"
    )
    count = cursor.fetchone()[0]
    conn.close()

    assert count == 1


def test_get_connection_returns_row_factory(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    conn = db.get_connection(db_path)
    assert conn.row_factory == sqlite3.Row
    conn.close()


def test_add_link_and_get_pending(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    rowid = db.add_link(
        url="https://example.com/video",
        source_type="imessage",
        sender="+15551234567",
        context="check this out",
        content_type="video",
        db_path=db_path,
    )
    assert rowid is not None
    assert rowid > 0

    pending = db.get_pending(status="pending", limit=10, db_path=db_path)
    assert len(pending) == 1
    assert pending[0]["url"] == "https://example.com/video"
    assert pending[0]["status"] == "pending"
    assert pending[0]["source_type"] == "imessage"


def test_add_link_duplicate_raises(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    db.add_link(
        url="https://example.com/video",
        source_type="imessage",
        sender="+15551234567",
        context="first",
        content_type="video",
        db_path=db_path,
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.add_link(
            url="https://example.com/video",
            source_type="imessage",
            sender="+15551234567",
            context="second",
            content_type="video",
            db_path=db_path,
        )


def test_claim_link_atomic(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/video",
        source_type="imessage",
        sender="+15551234567",
        context="test",
        content_type="video",
        db_path=db_path,
    )

    # First claim should succeed
    result = db.claim_link(
        link_id=link_id,
        from_status="pending",
        to_status="downloading",
        db_path=db_path,
    )
    assert result is True

    # Second claim with wrong from_status should fail
    result = db.claim_link(
        link_id=link_id,
        from_status="pending",
        to_status="downloading",
        db_path=db_path,
    )
    assert result is False


def test_update_status_changes_status_and_kwargs(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/video",
        source_type="imessage",
        sender="+15551234567",
        context="test",
        content_type="video",
        db_path=db_path,
    )

    db.update_status(
        link_id=link_id,
        status="failed",
        error="download timed out",
        db_path=db_path,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT status, error FROM links WHERE id = ?", (link_id,))
    row = cursor.fetchone()
    conn.close()

    assert row["status"] == "failed"
    assert row["error"] == "download timed out"


def test_log_processing_creates_entry(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/video",
        source_type="imessage",
        sender="+15551234567",
        context="test",
        content_type="video",
        db_path=db_path,
    )

    db.log_processing(
        link_id=link_id,
        stage="download",
        status="success",
        message="downloaded 42 MB",
        db_path=db_path,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM processing_log WHERE link_id = ?", (link_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0]["stage"] == "download"
    assert rows[0]["status"] == "success"
    assert rows[0]["message"] == "downloaded 42 MB"
