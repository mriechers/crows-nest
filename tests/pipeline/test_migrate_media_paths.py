"""Tests for pipeline/migrate_media_paths.py (issue #25)."""

import os
import sys
import sqlite3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from migrate_media_paths import _reroot, migrate


# ---------------------------------------------------------------------------
# _reroot
# ---------------------------------------------------------------------------

class TestReroot:
    def test_matching_prefix(self):
        assert _reroot("/old/root/sub/file.m4a", "/old/root", "/new/root") == "/new/root/sub/file.m4a"

    def test_non_matching_prefix(self):
        assert _reroot("/other/path/file.m4a", "/old/root", "/new/root") == "/other/path/file.m4a"

    def test_none_input(self):
        assert _reroot(None, "/old", "/new") is None

    def test_empty_string(self):
        assert _reroot("", "/old", "/new") == ""


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------

def _create_test_db(path, rows):
    """Create a minimal links table with the given rows."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE links (
            id INTEGER PRIMARY KEY,
            download_path TEXT,
            transcript_path TEXT
        )
    """)
    for row in rows:
        conn.execute(
            "INSERT INTO links (id, download_path, transcript_path) VALUES (?, ?, ?)",
            row,
        )
    conn.commit()
    conn.close()


class TestMigrate:
    def test_updates_matching_rows(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        _create_test_db(db, [
            (1, "/old/root/2024-01/item/audio.m4a", "/old/root/2024-01/item/audio.txt"),
            (2, "/other/path/audio.m4a", "/other/path/audio.txt"),
        ])
        monkeypatch.setattr("migrate_media_paths.OLD_ROOT", "/old/root")
        monkeypatch.setattr("migrate_media_paths.NEW_ROOT", "/new/root")

        count = migrate(str(db))
        assert count == 1

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = {r["id"]: r for r in conn.execute("SELECT * FROM links").fetchall()}
        conn.close()

        assert rows[1]["download_path"] == "/new/root/2024-01/item/audio.m4a"
        assert rows[1]["transcript_path"] == "/new/root/2024-01/item/audio.txt"
        assert rows[2]["download_path"] == "/other/path/audio.m4a"

    def test_idempotent(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        _create_test_db(db, [
            (1, "/old/root/audio.m4a", "/old/root/audio.txt"),
        ])
        monkeypatch.setattr("migrate_media_paths.OLD_ROOT", "/old/root")
        monkeypatch.setattr("migrate_media_paths.NEW_ROOT", "/new/root")

        assert migrate(str(db)) == 1
        assert migrate(str(db)) == 0  # already migrated

    def test_dry_run_no_changes(self, tmp_path, monkeypatch):
        db = tmp_path / "test.db"
        _create_test_db(db, [
            (1, "/old/root/audio.m4a", "/old/root/audio.txt"),
        ])
        monkeypatch.setattr("migrate_media_paths.OLD_ROOT", "/old/root")
        monkeypatch.setattr("migrate_media_paths.NEW_ROOT", "/new/root")

        count = migrate(str(db), dry_run=True)
        assert count == 1

        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT download_path FROM links WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == "/old/root/audio.m4a"  # unchanged

    def test_missing_db_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Database not found"):
            migrate(str(tmp_path / "nonexistent.db"))
