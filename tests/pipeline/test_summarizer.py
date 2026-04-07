"""
Tests for summarizer.py — pure function tests, no Claude API calls.
"""

import sys
import os

# Add the pipeline directory to path so imports resolve without package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from summarizer import build_frontmatter, generate_note_content


def test_build_frontmatter():
    """Verify vault-convention frontmatter is generated correctly."""
    result = build_frontmatter(
        title="Test Article",
        source="https://example.com/article",
        content_type="web_page",
        tags=["python", "testing"],
    )

    assert "para: inbox" in result
    assert "- all" in result
    assert "- clippings" in result
    assert "- web-clip" in result
    assert "- python" in result
    assert "source: https://example.com/article" in result
    # No sender fields when sender is None
    assert "via: signal" not in result
    assert "sender:" not in result


def test_generate_note_content_web():
    """Verify note body contains expected sections and data."""
    result = generate_note_content(
        title="A Great Article",
        source_url="https://example.com/great",
        content_type="web_page",
        summary="This article discusses important things.",
        key_points=["Point one", "Point two"],
        transcript_text="Full text of the article goes here.",
        metadata={"title": "A Great Article", "url": "https://example.com/great"},
    )

    assert "> [!summary]" in result
    assert "A Great Article" in result
    assert "https://example.com/great" in result
    assert "Point one" in result
    assert "Point two" in result


def test_build_frontmatter_with_sender():
    """Verify sender-related fields appear when sender is provided."""
    result = build_frontmatter(
        title="Signal Share",
        source="https://example.com/shared",
        content_type="youtube",
        tags=["video"],
        sender="Alice",
    )

    assert "via: signal" in result
    assert 'sender: "Alice"' in result
    assert "- video-clip" in result


def test_build_frontmatter_image():
    """Image frontmatter should include image-clip tag and image-count."""
    result = build_frontmatter(
        title="Screenshot Analysis",
        source="signal-image://1000-abc",
        content_type="image",
        tags=["ai-tools"],
        sender="Bob",
        metadata={"image_count": 2, "platform": "Signal"},
    )
    assert "- image-clip" in result
    assert "image-count: 2" in result
    assert "platform: Signal" in result


def test_generate_note_content_image():
    """Image notes should have ![[]] embeds and extracted text section."""
    result = generate_note_content(
        title="Screenshot Test",
        source_url="signal-image://1000-abc",
        content_type="image",
        summary="A screenshot of some code.",
        key_points=["Shows Python code"],
        transcript_text="",
        metadata={"vault_filenames": ["20260319-120000-1.jpg", "20260319-120000-2.jpg"],
                  "image_count": 2, "platform": "Signal"},
        sender="Bob",
        saved_at="2026-03-19",
        extracted_text="def hello():\n    print('world')",
    )
    assert "![[20260319-120000-1.jpg]]" in result
    assert "![[20260319-120000-2.jpg]]" in result
    assert "## Extracted Text" in result
    assert "def hello():" in result
    assert "<details>" not in result  # No transcript section for images


def test_generate_note_content_image_no_extracted_text():
    """Image notes without extracted text should omit the section."""
    result = generate_note_content(
        title="Photo Test",
        source_url="signal-image://2000-def",
        content_type="image",
        summary="A photo of a sunset.",
        key_points=["Beautiful colors"],
        transcript_text="",
        metadata={"vault_filenames": ["20260319-130000-1.jpg"],
                  "image_count": 1, "platform": "Signal"},
        extracted_text="",
    )
    assert "![[20260319-130000-1.jpg]]" in result
    assert "## Extracted Text" not in result  # No section when empty


def test_generate_note_content_full_transcript_not_truncated():
    """Transcripts longer than 2000 chars must appear in full (issue #17)."""
    long_transcript = "word " * 5000  # ~25,000 chars
    result = generate_note_content(
        title="Long Podcast",
        source_url="https://example.com/podcast",
        content_type="podcast",
        summary="A long podcast episode.",
        key_points=["Point one"],
        transcript_text=long_transcript,
        metadata={},
    )
    assert long_transcript.strip() in result
    assert "truncated" not in result
    assert "<details>" in result


def test_build_frontmatter_includes_intake():
    """Verify intake field appears in frontmatter when provided."""
    result = build_frontmatter(
        title="iMessage Link",
        source="https://example.com/test",
        content_type="web_page",
        tags=["test"],
        intake="imessage",
    )
    assert "intake: imessage" in result


def test_build_frontmatter_intake_defaults_to_unknown():
    """Verify intake defaults to 'unknown' when not provided."""
    result = build_frontmatter(
        title="Mystery Link",
        source="https://example.com/test",
        content_type="web_page",
        tags=[],
    )
    assert "intake: unknown" in result


def test_write_obsidian_note_date_subfolder(tmp_path):
    """Notes should be written to YYYY/MM/DD subfolders when created_at is provided."""
    import summarizer
    original = summarizer.OBSIDIAN_CLIPPINGS
    summarizer.OBSIDIAN_CLIPPINGS = str(tmp_path)
    try:
        path = summarizer.write_obsidian_note(
            title="Test Note",
            frontmatter="---\ntitle: Test\n---",
            body="Hello world",
            created_at="2026-04-06T12:00:00",
        )
        assert "/2026/04/06/" in path
        assert os.path.exists(path)
        assert path.endswith("Test Note.md")
    finally:
        summarizer.OBSIDIAN_CLIPPINGS = original


def test_write_obsidian_note_no_date_uses_flat(tmp_path):
    """Without created_at, notes go to the flat clippings directory."""
    import summarizer
    original = summarizer.OBSIDIAN_CLIPPINGS
    summarizer.OBSIDIAN_CLIPPINGS = str(tmp_path)
    try:
        path = summarizer.write_obsidian_note(
            title="Flat Note",
            frontmatter="---\ntitle: Flat\n---",
            body="No date",
        )
        assert os.path.dirname(path) == str(tmp_path)
    finally:
        summarizer.OBSIDIAN_CLIPPINGS = original
