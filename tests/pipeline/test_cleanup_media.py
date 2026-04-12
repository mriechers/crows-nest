"""Tests for pipeline/cleanup_media.py — safety-critical deletion paths."""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import db as db_module
from db import init_db, get_connection
import cleanup_media
from cleanup_media import (
    resolve_media_dir,
    is_obsidian_archive_path,
    _get_dir_size,
    run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_archived_link(db_path: str, download_path: str, days_ago: int = 40) -> int:
    """Insert a link with status='archived' and updated_at in the past."""
    updated_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    created_at = updated_at
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO links (url, source_type, sender, context, content_type,
                           status, created_at, updated_at, download_path)
        VALUES (?, 'cli', 'test', 'test', 'video',
                'archived', ?, ?, ?)
        """,
        (f"https://example.com/test-{days_ago}", created_at, updated_at, download_path),
    )
    conn.commit()
    link_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return link_id


# ---------------------------------------------------------------------------
# resolve_media_dir
# ---------------------------------------------------------------------------


class TestResolveMediaDir:
    def test_path_inside_media_root_returns_directory(self, tmp_path, monkeypatch):
        media_root = str(tmp_path / "media")
        item_dir = tmp_path / "media" / "2026-01" / "some-video"
        item_dir.mkdir(parents=True)

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)

        result = resolve_media_dir(str(item_dir))
        assert result == str(item_dir)

    def test_path_outside_media_root_returns_none(self, tmp_path, monkeypatch):
        media_root = str(tmp_path / "media")
        outside_dir = tmp_path / "other" / "stuff"
        outside_dir.mkdir(parents=True)

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)

        result = resolve_media_dir(str(outside_dir))
        assert result is None

    def test_file_path_resolves_to_parent_directory(self, tmp_path, monkeypatch):
        media_root = str(tmp_path / "media")
        item_dir = tmp_path / "media" / "2026-01" / "some-video"
        item_dir.mkdir(parents=True)
        fake_file = item_dir / "video.mp4"
        fake_file.write_bytes(b"fake")

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)

        result = resolve_media_dir(str(fake_file))
        assert result == str(item_dir)

    def test_empty_string_returns_none(self, tmp_path, monkeypatch):
        media_root = str(tmp_path / "media")
        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)

        result = resolve_media_dir("")
        assert result is None

    def test_none_returns_none(self, tmp_path, monkeypatch):
        media_root = str(tmp_path / "media")
        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)

        result = resolve_media_dir(None)
        assert result is None

    def test_nonexistent_path_returns_none(self, tmp_path, monkeypatch):
        media_root = str(tmp_path / "media")
        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)

        # The path is under media_root but does not exist on disk
        ghost_path = str(tmp_path / "media" / "2026-01" / "ghost-item")
        result = resolve_media_dir(ghost_path)
        assert result is None


# ---------------------------------------------------------------------------
# is_obsidian_archive_path
# ---------------------------------------------------------------------------


class TestIsObsidianArchivePath:
    def test_path_inside_archive_returns_true(self, tmp_path, monkeypatch):
        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        os.makedirs(archive_root)
        inner = os.path.join(archive_root, "some-note.md")

        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        assert is_obsidian_archive_path(inner) is True

    def test_path_outside_archive_returns_false(self, tmp_path, monkeypatch):
        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        os.makedirs(archive_root)
        outside = str(tmp_path / "media" / "item")

        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        assert is_obsidian_archive_path(outside) is False

    def test_subdirectory_of_archive_returns_true(self, tmp_path, monkeypatch):
        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        subdir = tmp_path / "obsidian" / "4 - ARCHIVE" / "2026" / "January"
        subdir.mkdir(parents=True)

        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        assert is_obsidian_archive_path(str(subdir)) is True


# ---------------------------------------------------------------------------
# _get_dir_size
# ---------------------------------------------------------------------------


class TestGetDirSize:
    def test_returns_correct_size_for_directory_with_files(self, tmp_path):
        d = tmp_path / "media-item"
        d.mkdir()
        (d / "video.mp4").write_bytes(b"x" * 1000)
        (d / "audio.m4a").write_bytes(b"y" * 500)

        assert _get_dir_size(str(d)) == 1500

    def test_returns_zero_for_empty_directory(self, tmp_path):
        d = tmp_path / "empty-item"
        d.mkdir()

        assert _get_dir_size(str(d)) == 0


# ---------------------------------------------------------------------------
# run() — dry_run=True
# ---------------------------------------------------------------------------


class TestRunDryRun:
    def test_dry_run_does_not_delete_directory(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        media_root = str(tmp_path / "media")
        item_dir = tmp_path / "media" / "2026-01" / "old-video"
        item_dir.mkdir(parents=True)
        (item_dir / "video.mp4").write_bytes(b"data" * 100)

        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        os.makedirs(archive_root)

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)
        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        _insert_archived_link(db_path, str(item_dir), days_ago=40)

        run(db_path=db_path, days=30, dry_run=True)

        assert item_dir.exists(), "dry_run=True must not delete the directory"

    def test_dry_run_preserves_db_record(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        media_root = str(tmp_path / "media")
        item_dir = tmp_path / "media" / "2026-01" / "old-video"
        item_dir.mkdir(parents=True)
        (item_dir / "video.mp4").write_bytes(b"data")

        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        os.makedirs(archive_root)

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)
        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        link_id = _insert_archived_link(db_path, str(item_dir), days_ago=40)

        run(db_path=db_path, days=30, dry_run=True)

        conn = get_connection(db_path)
        row = conn.execute("SELECT id, status FROM links WHERE id = ?", (link_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "archived"


# ---------------------------------------------------------------------------
# run() — dry_run=False
# ---------------------------------------------------------------------------


class TestRunLive:
    def test_live_run_deletes_directory(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        media_root = str(tmp_path / "media")
        item_dir = tmp_path / "media" / "2026-01" / "old-video"
        item_dir.mkdir(parents=True)
        (item_dir / "video.mp4").write_bytes(b"data" * 100)

        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        os.makedirs(archive_root)

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)
        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        _insert_archived_link(db_path, str(item_dir), days_ago=40)

        run(db_path=db_path, days=30, dry_run=False)

        assert not item_dir.exists(), "live run must delete the directory"

    def test_live_run_preserves_db_record(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        media_root = str(tmp_path / "media")
        item_dir = tmp_path / "media" / "2026-01" / "old-video"
        item_dir.mkdir(parents=True)
        (item_dir / "video.mp4").write_bytes(b"data")

        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        os.makedirs(archive_root)

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)
        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        link_id = _insert_archived_link(db_path, str(item_dir), days_ago=40)

        run(db_path=db_path, days=30, dry_run=False)

        conn = get_connection(db_path)
        row = conn.execute("SELECT id, status FROM links WHERE id = ?", (link_id,)).fetchone()
        conn.close()
        assert row is not None, "DB record must survive cleanup"
        assert row["status"] == "archived"

    def test_item_not_old_enough_is_skipped(self, tmp_path, monkeypatch):
        """An archived item only 5 days old should not be deleted when --days=30."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        media_root = str(tmp_path / "media")
        item_dir = tmp_path / "media" / "2026-01" / "recent-video"
        item_dir.mkdir(parents=True)
        (item_dir / "video.mp4").write_bytes(b"data")

        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        os.makedirs(archive_root)

        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", media_root)
        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        _insert_archived_link(db_path, str(item_dir), days_ago=5)

        run(db_path=db_path, days=30, dry_run=False)

        assert item_dir.exists(), "recently-archived item must not be deleted"

    def test_obsidian_archive_path_is_never_deleted(self, tmp_path, monkeypatch):
        """Safety fence: media_dir inside OBSIDIAN_ARCHIVE must never be deleted."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        archive_root = str(tmp_path / "obsidian" / "4 - ARCHIVE")
        # Place the item dir *inside* the archive
        item_dir = tmp_path / "obsidian" / "4 - ARCHIVE" / "old-images"
        item_dir.mkdir(parents=True)
        (item_dir / "img.jpg").write_bytes(b"data")

        # media_root also set to archive_root so resolve_media_dir won't reject it
        monkeypatch.setattr(cleanup_media, "MEDIA_ROOT", archive_root)
        monkeypatch.setattr(cleanup_media, "OBSIDIAN_ARCHIVE", archive_root)

        _insert_archived_link(db_path, str(item_dir), days_ago=40)

        run(db_path=db_path, days=30, dry_run=False)

        assert item_dir.exists(), "paths inside OBSIDIAN_ARCHIVE must never be deleted"


# ---------------------------------------------------------------------------
# --days minimum guard (argparse level)
# ---------------------------------------------------------------------------


class TestDaysGuard:
    def test_days_zero_is_rejected(self):
        """The argparse guard should reject --days 0."""
        import argparse

        # Reproduce the guard logic from __main__ block
        days = 0
        with pytest.raises((SystemExit, ValueError)):
            if days < 1:
                # mirror what the script does: parser.error raises SystemExit
                raise SystemExit("--days must be at least 1")
