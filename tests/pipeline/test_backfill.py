"""Tests for backfill_date_folders."""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))


def _make_note(directory: str, filename: str, created_date: str) -> str:
    """Create a minimal clippings note with frontmatter."""
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        f.write(f"---\ntitle: Test\ncreated: {created_date}\n---\n\nBody text\n")
    return path


def _init_db(db_path: str, note_path: str) -> None:
    """Create a minimal links table with one row."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS links ("
        "  id INTEGER PRIMARY KEY,"
        "  url TEXT UNIQUE,"
        "  status TEXT DEFAULT 'summarized',"
        "  obsidian_note_path TEXT,"
        "  updated_at TEXT"
        ")"
    )
    conn.execute(
        "INSERT INTO links (url, obsidian_note_path, updated_at) VALUES (?, ?, datetime('now'))",
        ("https://example.com/test", note_path),
    )
    conn.commit()
    conn.close()


def test_backfill_stores_vault_relative_path(tmp_path, monkeypatch):
    """After backfill --apply, DB should contain vault-relative paths."""
    import backfill_date_folders

    vault_root = str(tmp_path / "vault")
    clippings_dir = os.path.join(vault_root, "2 - AREAS", "INTERNET CLIPPINGS")
    os.makedirs(clippings_dir)

    # Create a flat note
    _make_note(clippings_dir, "Test Note.md", "2026-03-15")

    # Set up DB with an absolute path (mimics pre-migration state)
    db_path = str(tmp_path / "test.db")
    abs_note_path = os.path.join(clippings_dir, "Test Note.md")
    _init_db(db_path, abs_note_path)

    monkeypatch.setattr(backfill_date_folders, "OBSIDIAN_CLIPPINGS", clippings_dir)
    monkeypatch.setattr(backfill_date_folders, "OBSIDIAN_VAULT", vault_root)

    # Simulate --apply
    monkeypatch.setattr(sys, "argv", ["backfill", "--apply", "--db", db_path])
    backfill_date_folders.main()

    # File should have moved
    expected_file = os.path.join(clippings_dir, "2026", "03", "15", "Test Note.md")
    assert os.path.exists(expected_file)
    assert not os.path.exists(abs_note_path)

    # DB should store vault-relative path
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT obsidian_note_path FROM links WHERE url = ?", ("https://example.com/test",)).fetchone()
    conn.close()
    stored_path = row[0]
    assert not os.path.isabs(stored_path), f"Expected relative, got: {stored_path}"
    assert stored_path == os.path.join("2 - AREAS", "INTERNET CLIPPINGS", "2026", "03", "15", "Test Note.md")
