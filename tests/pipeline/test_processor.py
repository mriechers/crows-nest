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
from processor import process_image, extract_thumbnail, fetch_web_content


def test_process_web_page_saves_markdown(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/article",
        source_type="cli",
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
        source_type="cli",
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
        source_type="cli",
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
    src_dir = tmp_path / "test-attachments"
    src_dir.mkdir()
    fake_img = src_dir / "test123.jpg"
    fake_img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    link_id = add_link(
        url="https://example.com/image/1000000-abcd1234",
        source_type="cli",
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

    src_dir = tmp_path / "test-attachments"
    src_dir.mkdir()
    fake_img = src_dir / "test456.jpg"
    fake_img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    link_id = add_link(
        url="https://example.com/image/2000000-efgh5678",
        source_type="cli",
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


# ---------------------------------------------------------------------------
# Thumbnail extraction tests
# ---------------------------------------------------------------------------

def test_extract_thumbnail_web_page_with_og_image(tmp_path, monkeypatch):
    """extract_thumbnail downloads og_image for web_page content type."""
    media_dir = str(tmp_path / "media")
    os.makedirs(media_dir)
    thumbnail_path = os.path.join(media_dir, "thumbnail.jpg")

    # Fake minimal JPEG bytes returned by curl
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 2000

    def fake_run(cmd, **kwargs):
        # Simulate curl writing a file
        if cmd[0] == "curl" and "-o" in cmd:
            out_idx = cmd.index("-o") + 1
            with open(cmd[out_idx], "wb") as f:
                f.write(fake_jpeg)
        result = type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        return result

    monkeypatch.setattr("processor.subprocess.run", fake_run)
    # resize_image is a no-op in tests (no sips/magick needed)
    monkeypatch.setattr("processor.resize_image", lambda path, max_dim=800: None)

    metadata = {"og_image": "https://example.com/og.jpg"}
    result = extract_thumbnail(media_dir, "web_page", metadata)

    assert result is True
    assert os.path.exists(thumbnail_path)


def test_extract_thumbnail_web_page_no_og_image(tmp_path):
    """extract_thumbnail returns False for web_page with no og_image."""
    media_dir = str(tmp_path / "media")
    os.makedirs(media_dir)

    result = extract_thumbnail(media_dir, "web_page", {})
    assert result is False
    assert not os.path.exists(os.path.join(media_dir, "thumbnail.jpg"))


def test_extract_thumbnail_image_type(tmp_path, monkeypatch):
    """extract_thumbnail copies the first image to thumbnail.jpg for image content."""
    media_dir = str(tmp_path / "media")
    os.makedirs(media_dir)

    # Create a fake image file in media_dir
    fake_img = os.path.join(media_dir, "20260410-120000-1.jpg")
    with open(fake_img, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 200)

    monkeypatch.setattr("processor.resize_image", lambda path, max_dim=800: None)

    metadata = {"vault_filenames": ["20260410-120000-1.jpg"]}
    result = extract_thumbnail(media_dir, "image", metadata)

    assert result is True
    assert os.path.exists(os.path.join(media_dir, "thumbnail.jpg"))


def test_extract_thumbnail_audio_skipped(tmp_path):
    """Audio content type produces no thumbnail."""
    media_dir = str(tmp_path / "media")
    os.makedirs(media_dir)

    result = extract_thumbnail(media_dir, "audio", {})
    assert result is False
    assert not os.path.exists(os.path.join(media_dir, "thumbnail.jpg"))


def test_extract_thumbnail_idempotent(tmp_path):
    """If thumbnail.jpg already exists, extract_thumbnail returns True without redoing work."""
    media_dir = str(tmp_path / "media")
    os.makedirs(media_dir)
    existing = os.path.join(media_dir, "thumbnail.jpg")
    with open(existing, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0")

    result = extract_thumbnail(media_dir, "web_page", {"og_image": "https://example.com/img.jpg"})
    assert result is True
    # Verify file wasn't changed (still tiny 4 bytes)
    assert os.path.getsize(existing) == 4


def test_fetch_web_content_returns_og_image(monkeypatch):
    """fetch_web_content extracts og:image from HTML and returns it as third element."""
    html = """<html><head>
    <title>Test Page</title>
    <meta property="og:image" content="https://example.com/og-img.jpg" />
    </head><body>Some content here.</body></html>"""

    def fake_run(cmd, **kwargs):
        return type("R", (), {"returncode": 0, "stdout": html, "stderr": ""})()

    monkeypatch.setattr("processor.subprocess.run", fake_run)

    title, content, og_image = fetch_web_content("https://example.com/test")

    assert title == "Test Page"
    assert og_image == "https://example.com/og-img.jpg"
    assert "content" in content.lower() or "Some content" in content


def test_fetch_web_content_no_og_image(monkeypatch):
    """fetch_web_content returns empty string for og_image when tag absent."""
    html = "<html><head><title>No Image</title></head><body>text</body></html>"

    def fake_run(cmd, **kwargs):
        return type("R", (), {"returncode": 0, "stdout": html, "stderr": ""})()

    monkeypatch.setattr("processor.subprocess.run", fake_run)

    title, content, og_image = fetch_web_content("https://example.com/noimg")

    assert title == "No Image"
    assert og_image == ""


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
        url="https://example.com/image/3000000-xyz99999",
        source_type="cli",
        sender="Test Sender",
        context="routing test",
        content_type="image",
        metadata=attachment_meta,
        db_path=db_path,
    )

    processor.run(db_path)

    assert called_with.get("link_id") == link_id, "process_image was not called"
    assert web_called == [], "fetch_web_content should not have been called for image"
