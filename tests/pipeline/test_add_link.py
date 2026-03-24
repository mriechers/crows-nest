"""Tests for the add_link.py CLI tool."""

import sqlite3
import subprocess
import sys

SCRIPT = "/PATH/TO/crows-nest/pipeline/add_link.py"


def test_add_link_cli(tmp_path):
    db = str(tmp_path / "test.db")
    result = subprocess.run(
        [sys.executable, SCRIPT, "https://example.com/article", "--db", db],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM links WHERE url = ?", ("https://example.com/article",)).fetchone()
    conn.close()

    assert row is not None
    # columns: id, url, source_type, sender, context, content_type, status, ...
    assert row[2] == "cli"        # source_type
    assert row[5] == "web_page"   # content_type


def test_add_link_cli_with_context(tmp_path):
    db = str(tmp_path / "test.db")
    result = subprocess.run(
        [
            sys.executable, SCRIPT,
            "https://youtu.be/abc123",
            "--context", "Speaker: Dr. Smith",
            "--db", db,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM links WHERE url = ?", ("https://youtu.be/abc123",)).fetchone()
    conn.close()

    assert row is not None
    assert row[5] == "youtube"         # content_type
    assert row[4] == "Speaker: Dr. Smith"  # context


def test_add_link_cli_duplicate(tmp_path):
    db = str(tmp_path / "test.db")
    url = "https://example.com/dupe"

    # First insertion — should succeed
    r1 = subprocess.run(
        [sys.executable, SCRIPT, url, "--db", db],
        capture_output=True,
        text=True,
    )
    assert r1.returncode == 0, r1.stderr

    # Second insertion — same URL, should not crash and should say "already"
    r2 = subprocess.run(
        [sys.executable, SCRIPT, url, "--db", db],
        capture_output=True,
        text=True,
    )
    assert r2.returncode == 0, r2.stderr
    assert "already" in r2.stdout.lower()
