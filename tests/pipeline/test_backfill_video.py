"""Tests for pipeline/backfill_video.py (issue #25)."""

import os
import sys
import sqlite3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from backfill_video import (
    existing_videos,
    find_new_video,
    query_candidates,
    storage_estimate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_db(path):
    """Create a links table matching the schema backfill_video expects."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE links (
            id INTEGER PRIMARY KEY,
            url TEXT,
            content_type TEXT,
            download_path TEXT,
            video_path TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# query_candidates
# ---------------------------------------------------------------------------

class TestQueryCandidates:
    def test_returns_audio_only_items(self, tmp_path):
        db = tmp_path / "test.db"
        conn = _create_test_db(db)
        conn.execute(
            "INSERT INTO links (id, url, content_type, download_path, video_path, status) "
            "VALUES (1, 'https://youtube.com/1', 'youtube', '/media/1/a.m4a', NULL, 'summarized')"
        )
        conn.execute(
            "INSERT INTO links (id, url, content_type, download_path, video_path, status) "
            "VALUES (2, 'https://youtube.com/2', 'youtube', '/media/2/a.m4a', '/media/2/v.mp4', 'summarized')"
        )
        conn.execute(
            "INSERT INTO links (id, url, content_type, download_path, video_path, status) "
            "VALUES (3, 'https://tiktok.com/3', 'social_video', '/media/3/a.m4a', '', 'archived')"
        )
        conn.commit()
        conn.close()

        results = query_candidates(str(db))
        ids = {r["id"] for r in results}
        assert ids == {1, 3}  # row 2 already has video

    def test_skip_podcasts(self, tmp_path):
        db = tmp_path / "test.db"
        conn = _create_test_db(db)
        conn.execute(
            "INSERT INTO links (id, url, content_type, download_path, video_path, status) "
            "VALUES (1, 'https://pod.co/1', 'podcast', '/media/1/a.m4a', NULL, 'transcribed')"
        )
        conn.execute(
            "INSERT INTO links (id, url, content_type, download_path, video_path, status) "
            "VALUES (2, 'https://youtube.com/2', 'youtube', '/media/2/a.m4a', NULL, 'summarized')"
        )
        conn.commit()
        conn.close()

        results = query_candidates(str(db), skip_podcasts=True)
        assert len(results) == 1
        assert results[0]["id"] == 2


# ---------------------------------------------------------------------------
# storage_estimate
# ---------------------------------------------------------------------------

class TestStorageEstimate:
    def test_known_types(self):
        candidates = [
            {"content_type": "social_video"},
            {"content_type": "youtube"},
        ]
        total_mb, cost = storage_estimate(candidates)
        assert total_mb == 50 + 500
        assert cost == pytest.approx((550 / 1024) * 0.015)

    def test_unknown_type_defaults_to_500(self):
        total_mb, _ = storage_estimate([{"content_type": "unknown_type"}])
        assert total_mb == 500


# ---------------------------------------------------------------------------
# find_new_video / existing_videos
# ---------------------------------------------------------------------------

class TestFindNewVideo:
    def test_finds_new_file(self, tmp_path):
        before = set()
        video = tmp_path / "new_video.mp4"
        video.touch()
        result = find_new_video(str(tmp_path), before)
        assert result == str(video)

    def test_returns_none_when_no_new(self, tmp_path):
        video = tmp_path / "old.mp4"
        video.touch()
        before = {str(video)}
        assert find_new_video(str(tmp_path), before) is None

    def test_ignores_non_video_extensions(self, tmp_path):
        (tmp_path / "file.txt").touch()
        (tmp_path / "file.m4a").touch()
        assert find_new_video(str(tmp_path), set()) is None


class TestExistingVideos:
    def test_returns_video_set(self, tmp_path):
        (tmp_path / "a.mp4").touch()
        (tmp_path / "b.mkv").touch()
        (tmp_path / "c.txt").touch()
        result = existing_videos(str(tmp_path))
        assert result == {
            os.path.join(str(tmp_path), "a.mp4"),
            os.path.join(str(tmp_path), "b.mkv"),
        }

    def test_handles_nonexistent_dir(self):
        result = existing_videos("/nonexistent/path")
        assert result == set()
