import sys
import os
import sqlite3
import json
import shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import db
import processor
from db import init_db, add_link, get_connection
from processor import process_image


def test_process_web_page_saves_markdown(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/article",
        source_type="imessage",
        sender="+15551234567",
        context="interesting article",
        content_type="web_page",
        db_path=db_path,
    )

    media_dir = str(tmp_path / "media" / "test-article")
    os.makedirs(media_dir, exist_ok=True)

    processor.process_web_page(
        link_id=link_id,
        url="https://example.com/article",
        content="This is the article content.",
        title="Test Article",
        media_dir=media_dir,
        db_path=db_path,
    )

    # article.md should exist
    article_path = os.path.join(media_dir, "article.md")
    assert os.path.exists(article_path), "article.md was not created"

    # metadata.json should exist
    metadata_path = os.path.join(media_dir, "metadata.json")
    assert os.path.exists(metadata_path), "metadata.json was not created"

    # DB status should be "transcribed"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
    conn.close()

    assert row["status"] == "transcribed"
    assert row["transcript_path"] is not None
    assert "article.md" in row["transcript_path"]


def test_retry_increments_count(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/fail",
        source_type="imessage",
        sender="+15551234567",
        context="test",
        content_type="web_page",
        db_path=db_path,
    )

    # Monkey-patch fetch_web_content to always raise
    monkeypatch.setattr(
        "processor.fetch_web_content",
        lambda url: (_ for _ in ()).throw(RuntimeError("test error")),
    )

    processor.run(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
    conn.close()

    assert row["retry_count"] >= 1
    assert row["status"] in ("pending", "failed")
    assert row["error"] is not None
    assert "test error" in row["error"]


def test_recover_stale_claims(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/stale",
        source_type="imessage",
        sender="+15551234567",
        context="stale test",
        content_type="web_page",
        db_path=db_path,
    )

    # Manually set status to "downloading" with an updated_at 1 hour ago
    one_hour_ago = "2026-03-18T00:00:00+00:00"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE links SET status = 'downloading', updated_at = ? WHERE id = ?",
        (one_hour_ago, link_id),
    )
    conn.commit()
    conn.close()

    # Verify it's stuck
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status FROM links WHERE id = ?", (link_id,)).fetchone()
    conn.close()
    assert row["status"] == "downloading"

    # Recover stale claims
    processor.recover_stale_claims(db_path, stale_minutes=30)

    # Should be reset to pending
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status FROM links WHERE id = ?", (link_id,)).fetchone()
    conn.close()

    assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# Image processor tests
# ---------------------------------------------------------------------------

def test_process_image_copies_to_media(tmp_path, monkeypatch):
    """process_image copies images to media_dir and writes metadata.json."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    # Redirect OBSIDIAN_ARCHIVE to tmp_path so we don't touch the real vault
    fake_archive = str(tmp_path / "obsidian-archive")
    monkeypatch.setattr("processor.OBSIDIAN_ARCHIVE", fake_archive)

    # Create a fake JPEG attachment
    src_dir = tmp_path / "signal-attachments"
    src_dir.mkdir()
    fake_img = src_dir / "test123.jpg"
    fake_img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    link_id = add_link(
        url="signal-image://1000000-abcd1234",
        source_type="signal",
        sender="Test User",
        context="check this",
        content_type="image",
        db_path=db_path,
    )

    media_dir = str(tmp_path / "media" / "2026-03" / "test")
    metadata = {
        "attachment_paths": [str(fake_img)],
        "image_count": 1,
        "dimensions": [{"width": 100, "height": 100}],
        "batch_timestamps": [1000000],
    }

    process_image(
        link_id=link_id,
        media_dir=media_dir,
        metadata=metadata,
        context="check this",
        timestamp_slug="20260319-120000",
        db_path=db_path,
    )

    # Image and metadata.json should exist in media_dir
    assert os.path.exists(os.path.join(media_dir, "20260319-120000-1.jpg"))
    assert os.path.exists(os.path.join(media_dir, "metadata.json"))

    # DB should be updated
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT status, download_path, transcript_path FROM links WHERE id = ?", (link_id,)
    ).fetchone()
    conn.close()
    assert row["status"] == "transcribed"
    assert row["download_path"] == media_dir
    assert "metadata.json" in row["transcript_path"]


def test_process_image_metadata_has_vault_filenames(tmp_path, monkeypatch):
    """metadata.json should include vault_filenames for the summarizer."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    fake_archive = str(tmp_path / "obsidian-archive")
    monkeypatch.setattr("processor.OBSIDIAN_ARCHIVE", fake_archive)

    src_dir = tmp_path / "signal-attachments"
    src_dir.mkdir()
    fake_img = src_dir / "test456.jpg"
    fake_img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    link_id = add_link(
        url="signal-image://2000000-efgh5678",
        source_type="signal",
        sender="Test User",
        context="",
        content_type="image",
        db_path=db_path,
    )

    media_dir = str(tmp_path / "media" / "test")

    process_image(
        link_id=link_id,
        media_dir=media_dir,
        metadata={
            "attachment_paths": [str(fake_img)],
            "image_count": 1,
            "dimensions": [{"width": 100, "height": 100}],
            "batch_timestamps": [2000000],
        },
        context="",
        timestamp_slug="20260319-130000",
        db_path=db_path,
    )

    with open(os.path.join(media_dir, "metadata.json")) as f:
        saved_meta = json.load(f)

    assert "vault_filenames" in saved_meta
    assert saved_meta["vault_filenames"][0] == "20260319-130000-1.jpg"


def test_process_image_run_routing(tmp_path, monkeypatch):
    """run() routes content_type='image' to process_image, not web page handler."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    called_with = {}

    def fake_process_image(link_id, media_dir, metadata, context, timestamp_slug, db_path):
        called_with["link_id"] = link_id
        called_with["content_type"] = "image"
        # Mark transcribed so run() doesn't error
        db.update_status(link_id=link_id, status="transcribed",
                         download_path=media_dir,
                         transcript_path=os.path.join(media_dir, "metadata.json"),
                         db_path=db_path)

    monkeypatch.setattr("processor.process_image", fake_process_image)

    # Also monkeypatch fetch_web_content to detect if it gets called unexpectedly
    web_called = []
    monkeypatch.setattr("processor.fetch_web_content",
                        lambda url: web_called.append(url) or ("title", "content"))

    attachment_meta = json.dumps({
        "attachment_paths": [str(tmp_path / "img.jpg")],
        "image_count": 1,
        "dimensions": [{"width": 100, "height": 100}],
        "batch_timestamps": [3000000],
    })

    link_id = add_link(
        url="signal-image://3000000-xyz99999",
        source_type="signal",
        sender="Test Sender",
        context="routing test",
        content_type="image",
        metadata=attachment_meta,
        db_path=db_path,
    )

    processor.run(db_path)

    assert called_with.get("link_id") == link_id, "process_image was not called"
    assert web_called == [], "fetch_web_content should not have been called for image"
