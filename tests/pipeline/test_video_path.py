import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import db


def test_video_path_column_exists(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(links)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    assert "video_path" in columns


def test_update_status_sets_video_path(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/video",
        source_type="signal",
        sender="+15551234567",
        context="test video",
        content_type="video",
        db_path=db_path,
    )

    db.update_status(
        link_id=link_id,
        status="transcribed",
        video_path="/media/abc123/video.mp4",
        db_path=db_path,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT status, video_path FROM links WHERE id = ?", (link_id,))
    row = cursor.fetchone()
    conn.close()

    assert row["status"] == "transcribed"
    assert row["video_path"] == "/media/abc123/video.mp4"
