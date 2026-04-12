"""Tests for video download path logic in processor.process_video (issue #26).

Replaces the previous inspect.getsource tests with behavioral mocks that
verify actual download/fallback behavior.
"""

import sys
import os
import sqlite3
import json
from unittest.mock import MagicMock, patch
import subprocess as _subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import db
import processor


def test_video_path_column_exists(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(links)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    assert "video_path" in columns


def test_update_status_sets_video_path(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    link_id = db.add_link(
        url="https://example.com/video",
        source_type="cli",
        sender="+15551234567",
        context="test video",
        content_type="video",
        db_path=db_path,
    )

    db.update_status(
        link_id=link_id,
        status="transcribed",
        video_path="/media/abc123/video.mp4",
        db_path=db_path,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT status, video_path FROM links WHERE id = ?", (link_id,))
    row = cursor.fetchone()
    conn.close()

    assert row["status"] == "transcribed"
    assert row["video_path"] == "/media/abc123/video.mp4"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_link(tmp_path, url="https://youtube.com/watch?v=abc", content_type="youtube"):
    """Init DB and create a test link."""
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)
    link_id = db.add_link(
        url=url, source_type="cli", sender="test",
        context="", content_type=content_type, db_path=db_path,
    )
    return link_id, db_path


def _setup_media(tmp_path, files):
    """Create media_dir and touch files, return media_dir path."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    for f in files:
        (media_dir / f).write_bytes(b"fake")
    return str(media_dir)


# ---------------------------------------------------------------------------
# Behavioral tests
# ---------------------------------------------------------------------------


class TestProcessVideoSuccessPath:
    """Video downloads successfully, ffmpeg extracts audio, Whisper transcribes."""

    @patch("processor.subprocess.run")
    @patch("processor.update_status")
    @patch("processor.log_processing")
    @patch("processor._try_fetch_subtitles", return_value=None)
    @patch("processor._fetch_page", return_value=None)
    @patch("processor._find_transcript", return_value="/tmp/transcript.txt")
    def test_update_status_called_with_video_path(
        self, mock_find, mock_fetch_page, mock_subs,
        mock_log, mock_update, mock_run, tmp_path,
    ):
        media_dir = _setup_media(tmp_path, ["Cool Video.mp4", "Cool Video.m4a"])

        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr=""),   # metadata
            MagicMock(returncode=0, stdout="", stderr=""),   # video download
            MagicMock(returncode=0, stdout="", stderr=""),   # ffmpeg
            MagicMock(returncode=0, stdout="", stderr=""),   # whisper
        ]

        link_id, db_path = _make_link(tmp_path)
        processor.process_video(
            link_id=link_id, url="https://youtube.com/watch?v=abc",
            content_type="youtube", media_dir=media_dir,
            context="", db_path=db_path,
        )

        # Final update_status should include video_path
        video_path_calls = [c for c in mock_update.call_args_list
                            if c.kwargs.get("video_path")]
        assert len(video_path_calls) >= 1
        assert video_path_calls[0].kwargs["video_path"].endswith(".mp4")


class TestProcessVideoTimeoutFallback:
    """Video download times out, falls back to audio-only."""

    @patch("processor.subprocess.run")
    @patch("processor.update_status")
    @patch("processor.log_processing")
    @patch("processor._try_fetch_subtitles", return_value=None)
    @patch("processor._fetch_page", return_value=None)
    @patch("processor._find_transcript", return_value="/tmp/transcript.txt")
    def test_falls_back_to_audio_only_on_timeout(
        self, mock_find, mock_fetch_page, mock_subs,
        mock_log, mock_update, mock_run, tmp_path,
    ):
        media_dir = _setup_media(tmp_path, ["episode.m4a"])

        def run_side_effect(cmd, **kwargs):
            if "--merge-output-format" in cmd:
                raise _subprocess.TimeoutExpired(cmd, 600)
            if "--extract-audio" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            # metadata + whisper
            return MagicMock(returncode=0 if cmd[0] != "yt-dlp" else 1,
                             stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        link_id, db_path = _make_link(tmp_path)
        processor.process_video(
            link_id=link_id, url="https://youtube.com/watch?v=abc",
            content_type="youtube", media_dir=media_dir,
            context="", db_path=db_path,
        )

        audio_calls = [c for c in mock_run.call_args_list
                       if "--extract-audio" in c.args[0]]
        assert len(audio_calls) == 1


class TestProcessVideoFfmpegFailure:
    """Video downloads but ffmpeg fails, falls back to audio-only."""

    @patch("processor.subprocess.run")
    @patch("processor.update_status")
    @patch("processor.log_processing")
    @patch("processor._try_fetch_subtitles", return_value=None)
    @patch("processor._fetch_page", return_value=None)
    @patch("processor._find_transcript", return_value="/tmp/transcript.txt")
    def test_falls_back_to_audio_only_when_ffmpeg_fails(
        self, mock_find, mock_fetch_page, mock_subs,
        mock_log, mock_update, mock_run, tmp_path,
    ):
        media_dir = _setup_media(tmp_path, ["video.mp4", "audio.m4a"])

        def run_side_effect(cmd, **kwargs):
            if "--dump-json" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            if "--merge-output-format" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "ffmpeg":
                return MagicMock(returncode=1, stdout="", stderr="fail")
            if "--extract-audio" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        # ffmpeg output path won't exist → triggers fallback
        original_exists = os.path.exists

        def exists_patch(path):
            # ffmpeg audio output won't exist, triggering fallback
            if path.endswith(".m4a") and "video" in os.path.basename(path).lower():
                return False
            return original_exists(path)

        with patch("processor.os.path.exists", side_effect=exists_patch):
            link_id, db_path = _make_link(tmp_path)
            processor.process_video(
                link_id=link_id, url="https://youtube.com/watch?v=abc",
                content_type="youtube", media_dir=media_dir,
                context="", db_path=db_path,
            )

        audio_calls = [c for c in mock_run.call_args_list
                       if "--extract-audio" in c.args[0]]
        assert len(audio_calls) == 1


class TestProcessVideoRssFallback:
    """yt-dlp fails entirely, falls back to RSS audio URL."""

    @patch("processor.subprocess.run")
    @patch("processor.update_status")
    @patch("processor.log_processing")
    @patch("processor._try_fetch_subtitles", return_value=None)
    @patch("processor._try_fetch_podcast_transcript")
    @patch("processor._try_scrape_page_transcript", return_value=None)
    @patch("processor._fetch_page", return_value=None)
    @patch("processor._find_transcript", return_value="/tmp/transcript.txt")
    def test_uses_rss_audio_url_when_ytdlp_fails(
        self, mock_find, mock_fetch_page, mock_scrape,
        mock_podcast, mock_subs, mock_log, mock_update, mock_run,
        tmp_path,
    ):
        media_dir = _setup_media(tmp_path, ["episode.mp3"])
        mock_podcast.return_value = (None, "https://cdn.example.com/episode.mp3")

        def run_side_effect(cmd, **kwargs):
            if "--dump-json" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            if "--merge-output-format" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="blocked")
            if "--extract-audio" in cmd:
                return MagicMock(returncode=1, stderr="also failed")
            if cmd[0] == "curl":
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        link_id, db_path = _make_link(
            tmp_path, url="https://podcast.example.com/ep1",
            content_type="podcast",
        )
        processor.process_video(
            link_id=link_id, url="https://podcast.example.com/ep1",
            content_type="podcast", media_dir=media_dir,
            context="", db_path=db_path,
        )

        curl_calls = [c for c in mock_run.call_args_list
                      if c.args[0][0] == "curl"]
        assert len(curl_calls) == 1
        assert "https://cdn.example.com/episode.mp3" in curl_calls[0].args[0]

    @patch("processor.subprocess.run")
    @patch("processor.update_status")
    @patch("processor.log_processing")
    @patch("processor._try_fetch_subtitles", return_value=None)
    @patch("processor._fetch_page", return_value=None)
    def test_raises_when_ytdlp_fails_without_rss_fallback(
        self, mock_fetch_page, mock_subs, mock_log, mock_update, mock_run,
        tmp_path,
    ):
        media_dir = _setup_media(tmp_path, [])

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="DRM blocked")

        link_id, db_path = _make_link(tmp_path)

        import pytest
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            processor.process_video(
                link_id=link_id, url="https://youtube.com/watch?v=blocked",
                content_type="youtube", media_dir=media_dir,
                context="", db_path=db_path,
            )
