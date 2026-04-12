"""
Tests for summarizer.py — pure function tests, no Claude API calls.
"""

import sys
import os
import shutil

# Add the pipeline directory to path so imports resolve without package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from summarizer import build_frontmatter, generate_note_content, _copy_thumbnail_to_archive


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
        title="Shared Link",
        source="https://example.com/shared",
        content_type="youtube",
        tags=["video"],
        sender="Alice",
    )

    assert 'sender: "Alice"' in result
    assert "- video-clip" in result


def test_build_frontmatter_image():
    """Image frontmatter should include image-clip tag and image-count."""
    result = build_frontmatter(
        title="Screenshot Analysis",
        source="https://example.com/image",
        content_type="image",
        tags=["ai-tools"],
        sender="Bob",
        metadata={"image_count": 2},
    )
    assert "- image-clip" in result
    assert "image-count: 2" in result


def test_generate_note_content_image():
    """Image notes should have ![[]] embeds and extracted text section."""
    result = generate_note_content(
        title="Screenshot Test",
        source_url="https://example.com/image",
        content_type="image",
        summary="A screenshot of some code.",
        key_points=["Shows Python code"],
        transcript_text="",
        metadata={"vault_filenames": ["20260319-120000-1.jpg", "20260319-120000-2.jpg"],
                  "image_count": 2},
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
        source_url="https://example.com/sunset",
        content_type="image",
        summary="A photo of a sunset.",
        key_points=["Beautiful colors"],
        transcript_text="",
        metadata={"vault_filenames": ["20260319-130000-1.jpg"],
                  "image_count": 1},
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


# ---------------------------------------------------------------------------
# Thumbnail embed tests
# ---------------------------------------------------------------------------

def test_generate_note_content_with_thumbnail():
    """Thumbnail embed appears after sender callout and before summary."""
    result = generate_note_content(
        title="A Video",
        source_url="https://youtube.com/watch?v=abc",
        content_type="youtube",
        summary="Some video summary.",
        key_points=["Point one"],
        transcript_text="transcript",
        metadata={},
        sender="Alice",
        saved_at="2026-04-10",
        thumbnail_filename="a-video-thumb.jpg",
    )
    assert "![[a-video-thumb.jpg]]" in result
    # Thumbnail should appear before the summary callout
    assert result.index("![[a-video-thumb.jpg]]") < result.index("> [!summary]")


def test_generate_note_content_thumbnail_not_shown_for_image():
    """Image content type must NOT embed the thumbnail (uses vault_filenames instead)."""
    result = generate_note_content(
        title="Some Image",
        source_url="https://example.com/image-test",
        content_type="image",
        summary="An image.",
        key_points=[],
        transcript_text="",
        metadata={"vault_filenames": ["20260410-120000-1.jpg"], "image_count": 1},
        thumbnail_filename="some-image-thumb.jpg",
    )
    # The vault image embed should be there
    assert "![[20260410-120000-1.jpg]]" in result
    # The thumbnail embed should NOT be there (image type skips it)
    assert "![[some-image-thumb.jpg]]" not in result


def test_generate_note_content_no_thumbnail_when_none():
    """When thumbnail_filename is None, no thumbnail embed appears."""
    result = generate_note_content(
        title="Web Page",
        source_url="https://example.com/page",
        content_type="web_page",
        summary="Some article.",
        key_points=["Key point"],
        transcript_text="text",
        metadata={},
        thumbnail_filename=None,
    )
    assert "![[" not in result


def test_copy_thumbnail_to_archive_creates_file(tmp_path, monkeypatch):
    """_copy_thumbnail_to_archive copies thumbnail.jpg to OBSIDIAN_ARCHIVE."""
    import summarizer

    fake_archive = str(tmp_path / "archive")
    monkeypatch.setattr("summarizer.OBSIDIAN_ARCHIVE", fake_archive)

    # Create a fake media_dir with a transcript and a thumbnail
    media_dir = tmp_path / "media" / "some-clip"
    media_dir.mkdir(parents=True)
    transcript_path = str(media_dir / "transcript.txt")
    (media_dir / "transcript.txt").write_text("hello")
    (media_dir / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

    result = _copy_thumbnail_to_archive(
        transcript_path=transcript_path,
        title="Some Video Clip",
        content_type="youtube",
    )

    assert result is not None
    assert result.endswith(".jpg")
    assert os.path.exists(os.path.join(fake_archive, result))


def test_copy_thumbnail_to_archive_no_thumbnail(tmp_path, monkeypatch):
    """Returns None when no thumbnail.jpg exists."""
    import summarizer

    fake_archive = str(tmp_path / "archive")
    monkeypatch.setattr("summarizer.OBSIDIAN_ARCHIVE", fake_archive)

    media_dir = tmp_path / "media" / "no-thumb"
    media_dir.mkdir(parents=True)
    transcript_path = str(media_dir / "transcript.txt")
    (media_dir / "transcript.txt").write_text("hello")

    result = _copy_thumbnail_to_archive(
        transcript_path=transcript_path,
        title="No Thumb Here",
        content_type="web_page",
    )

    assert result is None


def test_copy_thumbnail_to_archive_collision_handling(tmp_path, monkeypatch):
    """Collision: a second copy gets a -1 suffix rather than overwriting."""
    import summarizer

    fake_archive = str(tmp_path / "archive")
    os.makedirs(fake_archive)
    monkeypatch.setattr("summarizer.OBSIDIAN_ARCHIVE", fake_archive)

    media_dir = tmp_path / "media" / "clip"
    media_dir.mkdir(parents=True)
    transcript_path = str(media_dir / "transcript.txt")
    (media_dir / "transcript.txt").write_text("hello")
    (media_dir / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

    # First call creates the archive file
    first = _copy_thumbnail_to_archive(
        transcript_path=transcript_path,
        title="Clip Title",
        content_type="youtube",
    )
    assert first is not None
    assert os.path.exists(os.path.join(fake_archive, first))

    # Second call: the archive destination already exists, so a suffix is added
    second = _copy_thumbnail_to_archive(
        transcript_path=transcript_path,
        title="Clip Title",
        content_type="youtube",
    )
    assert second is not None
    assert second != first
    assert "-1" in second
    assert os.path.exists(os.path.join(fake_archive, second))


def test_build_frontmatter_includes_intake():
    """Verify intake field appears in frontmatter when provided."""
    result = build_frontmatter(
        title="Ingest Link",
        source="https://example.com/test",
        content_type="web_page",
        tags=["test"],
        intake="ingest-api",
    )
    assert "intake: ingest-api" in result


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
