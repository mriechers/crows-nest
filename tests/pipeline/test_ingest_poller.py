"""Tests for the ingest poller that drains the D1 queue into the local pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))


class FakeResponse:
    """Minimal requests.Response stand-in."""

    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class TestFetchPending:
    @patch("ingest_poller.requests.get")
    def test_returns_items(self, mock_get):
        from ingest_poller import fetch_pending
        mock_get.return_value = FakeResponse({
            "items": [
                {"id": 1, "url": "https://example.com/a", "context": None, "source": "shortcut", "created_at": "2026-04-06T12:00:00Z"},
                {"id": 2, "url": "https://example.com/b", "context": "cool thing", "source": "shortcut", "created_at": "2026-04-06T12:01:00Z"},
            ]
        })
        items = fetch_pending("https://test.api/api", "fake-token")
        assert len(items) == 2
        assert items[0]["url"] == "https://example.com/a"
        assert items[1]["context"] == "cool thing"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "Bearer fake-token" in call_args.kwargs.get("headers", {}).get("Authorization", "")

    @patch("ingest_poller.requests.get")
    def test_returns_empty_list_on_no_items(self, mock_get):
        from ingest_poller import fetch_pending
        mock_get.return_value = FakeResponse({"items": []})
        items = fetch_pending("https://test.api/api", "fake-token")
        assert items == []

    @patch("ingest_poller.requests.get")
    def test_returns_empty_list_on_http_error(self, mock_get):
        from ingest_poller import fetch_pending
        mock_get.return_value = FakeResponse({"error": "unauthorized"}, status_code=401)
        items = fetch_pending("https://test.api/api", "fake-token")
        assert items == []


class TestMarkSynced:
    @patch("ingest_poller.requests.post")
    def test_marks_ids(self, mock_post):
        from ingest_poller import mark_synced
        mock_post.return_value = FakeResponse({"synced": 2})
        result = mark_synced("https://test.api/api", "fake-token", [1, 2])
        assert result is True
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json", {})
        assert body["ids"] == [1, 2]

    @patch("ingest_poller.requests.post")
    def test_returns_false_on_error(self, mock_post):
        from ingest_poller import mark_synced
        mock_post.return_value = FakeResponse({"error": "fail"}, status_code=500)
        result = mark_synced("https://test.api/api", "fake-token", [1])
        assert result is False


class TestPollAndDrain:
    @patch("ingest_poller.mark_synced")
    @patch("ingest_poller.add_link")
    @patch("ingest_poller.fetch_pending")
    def test_drains_items_into_local_db(self, mock_fetch, mock_add_link, mock_mark):
        from ingest_poller import poll_and_drain
        mock_fetch.return_value = [
            {"id": 10, "url": "https://example.com/vid", "context": "test", "source": "shortcut", "created_at": "2026-04-06T12:00:00Z"},
        ]
        mock_add_link.return_value = 42
        mock_mark.return_value = True
        result = poll_and_drain("https://test.api/api", "fake-token", db_path=":memory:")
        assert result == 1
        mock_add_link.assert_called_once()
        call_kwargs = mock_add_link.call_args.kwargs
        assert call_kwargs["url"] == "https://example.com/vid"
        assert call_kwargs["source_type"] == "ingest-api"
        assert call_kwargs["context"] == "test"
        mock_mark.assert_called_once_with("https://test.api/api", "fake-token", [10])

    @patch("ingest_poller.mark_synced")
    @patch("ingest_poller.add_link")
    @patch("ingest_poller.fetch_pending")
    def test_skips_duplicate_urls(self, mock_fetch, mock_add_link, mock_mark):
        import sqlite3
        from ingest_poller import poll_and_drain
        mock_fetch.return_value = [
            {"id": 20, "url": "https://example.com/dupe", "context": None, "source": "shortcut", "created_at": "2026-04-06T12:00:00Z"},
        ]
        mock_add_link.side_effect = sqlite3.IntegrityError("UNIQUE constraint")
        mock_mark.return_value = True
        result = poll_and_drain("https://test.api/api", "fake-token", db_path=":memory:")
        assert result == 0
        mock_mark.assert_called_once_with("https://test.api/api", "fake-token", [20])

    @patch("ingest_poller.fetch_pending")
    def test_noop_when_queue_empty(self, mock_fetch):
        from ingest_poller import poll_and_drain
        mock_fetch.return_value = []
        result = poll_and_drain("https://test.api/api", "fake-token", db_path=":memory:")
        assert result == 0
