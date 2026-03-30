import sys
import os
import sqlite3
import inspect
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import db
import processor


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


def test_process_video_function_signature():
    """Verify process_video has the expected parameters."""
    sig = inspect.signature(processor.process_video)
    params = list(sig.parameters.keys())
    assert "link_id" in params
    assert "url" in params
    assert "content_type" in params
    assert "media_dir" in params
    assert "context" in params
    assert "db_path" in params


def test_video_download_format_string_in_source():
    """Verify the yt-dlp format string for video download is present in processor source."""
    source = inspect.getsource(processor.process_video)
    # The video download should use the mp4-preferred format selector
    assert "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" in source
    # Should use --merge-output-format mp4
    assert "--merge-output-format" in source
    # Should extract audio via ffmpeg (not just yt-dlp --extract-audio)
    assert "ffmpeg" in source
    assert "-vn" in source
    assert "-acodec" in source


def test_video_download_preserves_fallback_logic():
    """Verify the audio-only fallback is still present in processor source."""
    source = inspect.getsource(processor.process_video)
    # Audio-only fallback should still exist
    assert "--extract-audio" in source
    assert "--audio-format" in source
    # RSS fallback should still exist
    assert "rss_audio_url" in source
    assert "RSS audio fallback" in source
