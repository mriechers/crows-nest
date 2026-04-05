import os
import sys

# pipeline/ must be on path so 'from config import ...' resolves inside summarizer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../pipeline"))

from datetime import date
from pipeline.summarizer import _append_to_weekly_log

CONTENT_TYPE_SECTION_MAP = {
    "youtube": "Videos",
    "social_video": "Videos",
    "web_page": "Articles",
    "podcast": "Podcasts",
    "audio": "Podcasts",
    "image": "Images",
}


def test_creates_weekly_log_on_first_capture(tmp_path):
    """First capture of the week creates the weekly log file."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Test Article",
        url="https://example.com/article",
        content_type="web_page",
        source="Signal",
        capture_date=date(2026, 3, 30),
    )

    log_path = tmp_path / "Weekly Links — 2026-W14.md"
    assert log_path.exists()

    content = log_path.read_text()
    assert 'title: "Weekly Links — 2026-W14"' in content
    assert "tags:" in content
    assert "## Videos" in content
    assert "## Articles" in content
    assert "## Podcasts" in content
    assert "[[Test Article]]" in content
    assert "https://example.com/article" in content


def test_appends_to_existing_weekly_log(tmp_path):
    """Second capture appends to existing file under correct section."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="First Video",
        url="https://youtube.com/watch?v=123",
        content_type="youtube",
        source="Signal",
        capture_date=date(2026, 3, 30),
    )
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Second Article",
        url="https://example.com/news",
        content_type="web_page",
        source="Signal",
        capture_date=date(2026, 3, 31),
    )

    log_path = tmp_path / "Weekly Links — 2026-W14.md"
    content = log_path.read_text()
    assert "[[First Video]]" in content
    assert "[[Second Article]]" in content


def test_wikilink_uses_sanitized_title(tmp_path):
    """Wikilinks should use the sanitized title to match the actual filename."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Claude How-To: From Basics to Advanced",
        url="https://example.com/video",
        content_type="social_video",
        source="cli",
        capture_date=date(2026, 3, 30),
    )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    assert "[[Claude How-To From Basics to Advanced]]" in content
    assert "[[Claude How-To:" not in content


def test_maps_content_types_to_sections(tmp_path):
    """Each content type maps to the correct section header."""
    for ctype, section in CONTENT_TYPE_SECTION_MAP.items():
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title=f"Test {ctype}",
            url=f"https://example.com/{ctype}",
            content_type=ctype,
            source="Signal",
            capture_date=date(2026, 3, 30),
        )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    assert "Test youtube" in content.split("## Videos")[1].split("## ")[0]
    assert "Test web_page" in content.split("## Articles")[1].split("## ")[0]
    assert "Test podcast" in content.split("## Podcasts")[1].split("## ")[0]
    assert "Test image" in content.split("## Images")[1].split("## ")[0]
