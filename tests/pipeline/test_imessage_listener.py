# tests/pipeline/test_imessage_listener.py
"""Tests for the iMessage self-message listener."""

import os
import sqlite3
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))


def _create_mock_imessage_db(db_path: str, messages: list[dict]) -> None:
    """Create a minimal iMessage-like DB with test messages."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            is_from_me INTEGER DEFAULT 0,
            date INTEGER,
            cache_roomnames TEXT
        )
    """)
    for msg in messages:
        conn.execute(
            "INSERT INTO message (text, is_from_me, date, cache_roomnames) VALUES (?, ?, ?, ?)",
            (msg.get("text"), msg.get("is_from_me", 1), msg.get("date", 0), msg.get("cache_roomnames")),
        )
    conn.commit()
    conn.close()


class TestFetchSelfMessages:
    def test_finds_self_sent_urls(self, tmp_path):
        from imessage_listener import fetch_self_messages

        imsg_db = str(tmp_path / "chat.db")
        _create_mock_imessage_db(imsg_db, [
            {"text": "https://example.com/cool", "is_from_me": 1, "date": 100},
            {"text": "Hey what's up", "is_from_me": 1, "date": 200},
            {"text": "https://tiktok.com/t/abc", "is_from_me": 0, "date": 300},
        ])

        results = fetch_self_messages(imsg_db, since_rowid=0)
        assert len(results) == 1
        assert results[0]["text"] == "https://example.com/cool"

    def test_respects_since_rowid(self, tmp_path):
        from imessage_listener import fetch_self_messages

        imsg_db = str(tmp_path / "chat.db")
        _create_mock_imessage_db(imsg_db, [
            {"text": "https://example.com/old", "is_from_me": 1, "date": 100},
            {"text": "https://example.com/new", "is_from_me": 1, "date": 200},
        ])

        results = fetch_self_messages(imsg_db, since_rowid=1)
        assert len(results) == 1
        assert results[0]["text"] == "https://example.com/new"

    def test_skips_group_messages(self, tmp_path):
        from imessage_listener import fetch_self_messages

        imsg_db = str(tmp_path / "chat.db")
        _create_mock_imessage_db(imsg_db, [
            {"text": "https://example.com/group", "is_from_me": 1, "date": 100, "cache_roomnames": "chat123"},
            {"text": "https://example.com/direct", "is_from_me": 1, "date": 200, "cache_roomnames": None},
        ])

        results = fetch_self_messages(imsg_db, since_rowid=0)
        assert len(results) == 1
        assert "direct" in results[0]["text"]


class TestProcessSelfMessages:
    def test_ingests_urls_from_self_messages(self, tmp_path):
        from imessage_listener import process_self_messages
        from db import init_db, get_connection

        pipeline_db = str(tmp_path / "pipeline.db")
        init_db(pipeline_db)

        imsg_db = str(tmp_path / "chat.db")
        _create_mock_imessage_db(imsg_db, [
            {"text": "https://example.com/save-this", "is_from_me": 1, "date": 100},
            {"text": "check this out https://youtu.be/xyz and also https://tiktok.com/t/abc", "is_from_me": 1, "date": 200},
        ])

        state_file = str(tmp_path / "imessage_state.json")
        count = process_self_messages(imsg_db, pipeline_db, state_file)
        assert count == 3

        conn = get_connection(pipeline_db)
        rows = conn.execute("SELECT url, source_type FROM links ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 3
        assert all(r["source_type"] == "imessage" for r in rows)

    def test_remembers_last_rowid(self, tmp_path):
        from imessage_listener import process_self_messages
        from db import init_db

        pipeline_db = str(tmp_path / "pipeline.db")
        init_db(pipeline_db)

        imsg_db = str(tmp_path / "chat.db")
        _create_mock_imessage_db(imsg_db, [
            {"text": "https://example.com/first", "is_from_me": 1, "date": 100},
        ])

        state_file = str(tmp_path / "imessage_state.json")

        count1 = process_self_messages(imsg_db, pipeline_db, state_file)
        assert count1 == 1

        count2 = process_self_messages(imsg_db, pipeline_db, state_file)
        assert count2 == 0
