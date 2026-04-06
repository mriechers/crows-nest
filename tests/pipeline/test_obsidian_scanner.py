# tests/pipeline/test_obsidian_scanner.py
"""Tests for the Obsidian vault scanner that ingests links from pending-clippings notes."""

import os
import sqlite3
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))


class TestFindPendingNotes:
    """Test finding notes tagged with pending-clippings."""

    def test_finds_tagged_note(self, tmp_path):
        from obsidian_scanner import find_pending_notes

        note = tmp_path / "links.md"
        note.write_text(textwrap.dedent("""\
            ---
            tags:
              - pending-clippings
            ---
            https://example.com/a
        """))

        results = find_pending_notes(str(tmp_path))
        assert len(results) == 1
        assert results[0] == str(note)

    def test_skips_note_without_tag(self, tmp_path):
        from obsidian_scanner import find_pending_notes

        note = tmp_path / "other.md"
        note.write_text(textwrap.dedent("""\
            ---
            tags:
              - journal
            ---
            Some text
        """))

        results = find_pending_notes(str(tmp_path))
        assert results == []

    def test_finds_multiple_notes(self, tmp_path):
        from obsidian_scanner import find_pending_notes

        for name in ("a.md", "b.md"):
            (tmp_path / name).write_text(textwrap.dedent("""\
                ---
                tags:
                  - pending-clippings
                ---
                https://example.com
            """))

        results = find_pending_notes(str(tmp_path))
        assert len(results) == 2

    def test_handles_inline_tag_format(self, tmp_path):
        from obsidian_scanner import find_pending_notes

        note = tmp_path / "inline.md"
        note.write_text(textwrap.dedent("""\
            ---
            tags: [all, pending-clippings]
            ---
            https://example.com
        """))

        results = find_pending_notes(str(tmp_path))
        assert len(results) == 1


class TestExtractUrlsFromNote:
    """Test URL extraction from note body (excluding frontmatter)."""

    def test_extracts_urls_from_body(self):
        from obsidian_scanner import extract_urls_from_note

        content = textwrap.dedent("""\
            ---
            tags:
              - pending-clippings
            ---
            https://example.com/a
            https://example.com/b
            Some text without a URL
            https://tiktok.com/t/abc123
        """)

        urls = extract_urls_from_note(content)
        assert urls == [
            "https://example.com/a",
            "https://example.com/b",
            "https://tiktok.com/t/abc123",
        ]

    def test_returns_empty_for_no_urls(self):
        from obsidian_scanner import extract_urls_from_note

        content = textwrap.dedent("""\
            ---
            tags:
              - pending-clippings
            ---
            Just some text, no links here.
        """)

        urls = extract_urls_from_note(content)
        assert urls == []


class TestScanAndIngest:
    """Test the main scan_and_ingest orchestration."""

    def test_ingests_urls_and_deletes_note(self, tmp_path):
        from obsidian_scanner import scan_and_ingest
        from db import init_db, get_connection

        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        note = vault_dir / "saved-links.md"
        note.write_text(textwrap.dedent("""\
            ---
            tags:
              - pending-clippings
            ---
            https://example.com/article
            https://youtu.be/abc123
        """))

        count = scan_and_ingest(str(vault_dir), db_path=db_path)
        assert count == 2

        # Note should be deleted
        assert not note.exists()

        # URLs should be in the DB
        conn = get_connection(db_path)
        rows = conn.execute("SELECT url, source_type FROM links ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0]["url"] == "https://example.com/article"
        assert rows[0]["source_type"] == "obsidian"
        assert rows[1]["url"] == "https://youtu.be/abc123"

    def test_skips_duplicates_still_deletes_note(self, tmp_path):
        from obsidian_scanner import scan_and_ingest
        from db import init_db, add_link

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        add_link(url="https://example.com/dupe", source_type="cli", db_path=db_path)

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        note = vault_dir / "links.md"
        note.write_text(textwrap.dedent("""\
            ---
            tags:
              - pending-clippings
            ---
            https://example.com/dupe
        """))

        count = scan_and_ingest(str(vault_dir), db_path=db_path)
        assert count == 0  # dupe not counted
        assert not note.exists()  # note still deleted

    def test_noop_when_no_pending_notes(self, tmp_path):
        from obsidian_scanner import scan_and_ingest

        db_path = str(tmp_path / "test.db")
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()

        count = scan_and_ingest(str(vault_dir), db_path=db_path)
        assert count == 0
