"""Tests for the mcp_knowledge.media_loader module."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mcp_knowledge.media_loader import load_media_documents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_media_item(
    root: Path,
    month: str,
    dirname: str,
    *,
    transcript: str | None = None,
    transcript_filename: str | None = None,
    page_txt: str | None = None,
    article_md: str | None = None,
    metadata: dict | None = None,
) -> Path:
    """Create a fake media item directory tree."""
    item_dir = root / month / dirname
    item_dir.mkdir(parents=True, exist_ok=True)

    if metadata is not None:
        (item_dir / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )

    if page_txt is not None:
        (item_dir / "page.txt").write_text(page_txt, encoding="utf-8")

    if article_md is not None:
        (item_dir / "article.md").write_text(article_md, encoding="utf-8")

    if transcript is not None:
        fname = transcript_filename or f"{dirname}.txt"
        (item_dir / fname).write_text(transcript, encoding="utf-8")

    return item_dir


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestLoadMediaDocuments:
    def test_finds_transcripts(self, tmp_path: Path) -> None:
        """Item with transcript + metadata.json is loaded with correct fields."""
        metadata = {
            "title": "My Great Video",
            "creator": "Some Creator",
            "platform": "youtube",
            "url": "https://youtube.com/watch?v=abc",
        }
        item_dir = make_media_item(
            tmp_path,
            "2026-03",
            "my-great-video",
            transcript="Hello world transcript content.",
            transcript_filename="my-great-video.txt",
            metadata=metadata,
        )

        docs = load_media_documents(str(tmp_path))

        assert len(docs) == 1
        doc = docs[0]
        assert doc["title"] == "My Great Video"
        assert doc["text"] == "Hello world transcript content."
        assert doc["path"] == str(item_dir)
        assert doc["metadata"]["platform"] == "youtube"
        assert doc["metadata"]["url"] == "https://youtube.com/watch?v=abc"

    def test_skips_items_without_text_content(self, tmp_path: Path) -> None:
        """Item with only metadata.json and no text file is skipped."""
        make_media_item(
            tmp_path,
            "2026-03",
            "metadata-only",
            metadata={"title": "No Text Here"},
        )

        docs = load_media_documents(str(tmp_path))

        assert docs == []

    def test_includes_web_pages_via_page_txt(self, tmp_path: Path) -> None:
        """Item with page.txt is loaded as a document."""
        metadata = {
            "title": "Some Article",
            "platform": "web_page",
            "url": "https://example.com/article",
        }
        item_dir = make_media_item(
            tmp_path,
            "2026-02",
            "some-article",
            page_txt="Article body content here.",
            metadata=metadata,
        )

        docs = load_media_documents(str(tmp_path))

        assert len(docs) == 1
        doc = docs[0]
        assert doc["title"] == "Some Article"
        assert doc["text"] == "Article body content here."
        assert doc["path"] == str(item_dir)

    def test_page_txt_takes_priority_over_transcript(self, tmp_path: Path) -> None:
        """When both page.txt and a transcript exist, page.txt is preferred."""
        make_media_item(
            tmp_path,
            "2026-03",
            "mixed-item",
            page_txt="Page content.",
            transcript="Transcript content.",
            transcript_filename="mixed-item.txt",
            metadata={"title": "Mixed Item"},
        )

        docs = load_media_documents(str(tmp_path))

        assert len(docs) == 1
        assert docs[0]["text"] == "Page content."

    def test_article_md_takes_priority_over_transcript(self, tmp_path: Path) -> None:
        """When both article.md and a transcript exist, article.md is preferred."""
        make_media_item(
            tmp_path,
            "2026-03",
            "md-item",
            article_md="Article markdown content.",
            transcript="Transcript content.",
            transcript_filename="md-item.txt",
            metadata={"title": "MD Item"},
        )

        docs = load_media_documents(str(tmp_path))

        assert len(docs) == 1
        assert docs[0]["text"] == "Article markdown content."

    def test_falls_back_to_dirname_when_no_metadata(self, tmp_path: Path) -> None:
        """When metadata.json is absent, title falls back to the directory name."""
        item_dir = make_media_item(
            tmp_path,
            "2026-03",
            "no-metadata-item",
            transcript="Some transcript.",
            transcript_filename="no-metadata-item.txt",
        )

        docs = load_media_documents(str(tmp_path))

        assert len(docs) == 1
        assert docs[0]["title"] == "no-metadata-item"
        assert docs[0]["metadata"] == {}

    def test_loads_multiple_items_across_months(self, tmp_path: Path) -> None:
        """Documents are collected from multiple month directories."""
        make_media_item(
            tmp_path,
            "2026-01",
            "jan-item",
            transcript="January content.",
            transcript_filename="jan-item.txt",
            metadata={"title": "January Item"},
        )
        make_media_item(
            tmp_path,
            "2026-02",
            "feb-item",
            page_txt="February page.",
            metadata={"title": "February Item"},
        )

        docs = load_media_documents(str(tmp_path))

        assert len(docs) == 2
        titles = {d["title"] for d in docs}
        assert titles == {"January Item", "February Item"}

    def test_skips_media_files_not_text(self, tmp_path: Path) -> None:
        """Media files (.m4a, .mp4) in the item dir do not cause errors or false loads."""
        item_dir = tmp_path / "2026-03" / "video-with-media"
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "video.m4a").write_bytes(b"\x00\x01\x02")
        (item_dir / "video.mp4").write_bytes(b"\x00\x01\x02")
        # No text content — should be skipped
        docs = load_media_documents(str(tmp_path))
        assert docs == []

    def test_returns_empty_list_for_empty_media_root(self, tmp_path: Path) -> None:
        """Empty media root returns empty list without error."""
        docs = load_media_documents(str(tmp_path))
        assert docs == []

    def test_returns_empty_list_for_nonexistent_media_root(self) -> None:
        """Nonexistent media root returns empty list without raising."""
        docs = load_media_documents("/nonexistent/path/that/does/not/exist")
        assert docs == []
