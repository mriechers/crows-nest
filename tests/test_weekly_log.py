import json
import os
import sys
from unittest.mock import patch, MagicMock

# pipeline/ must be on path so 'from config import ...' resolves inside summarizer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../pipeline"))

from datetime import date
from pipeline.summarizer import (
    _append_to_weekly_log,
    _categorize_via_llm,
    _parse_weekly_sections,
    categorize_from_tags,
)


def test_creates_weekly_log_on_first_capture(tmp_path):
    """First capture of the week creates the weekly log file."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Test Article",
        url="https://example.com/article",
        content_type="web_page",
        source="ingest-api",
        tags=["career-coaching", "burnout-recovery"],
        capture_date=date(2026, 3, 30),
    )

    log_path = tmp_path / "Weekly Links — 2026-W14.md"
    assert log_path.exists()

    content = log_path.read_text()
    assert 'title: "Weekly Links — 2026-W14"' in content
    assert "tags:" in content
    assert "## Work & Leadership" in content
    assert "[[Test Article]]" in content
    assert "https://example.com/article" in content


def test_appends_to_existing_weekly_log(tmp_path):
    """Second capture appends to existing file under correct section."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="First Video",
        url="https://youtube.com/watch?v=123",
        content_type="youtube",
        source="ingest-api",
        tags=["claude-code", "ai-agents"],
        capture_date=date(2026, 3, 30),
    )
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Second Article",
        url="https://example.com/news",
        content_type="web_page",
        source="ingest-api",
        tags=["horror-film", "movie-review"],
        capture_date=date(2026, 3, 31),
    )

    log_path = tmp_path / "Weekly Links — 2026-W14.md"
    content = log_path.read_text()
    assert "[[First Video]]" in content
    assert "[[Second Article]]" in content
    # Each should be in its own section
    assert "## AI & Dev Tools" in content
    assert "## Horror & Film" in content


def test_wikilink_uses_sanitized_title(tmp_path):
    """Wikilinks should use the sanitized title to match the actual filename."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Claude How-To: From Basics to Advanced",
        url="https://example.com/video",
        content_type="social_video",
        source="cli",
        tags=["claude-code"],
        capture_date=date(2026, 3, 30),
    )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    assert "[[Claude How-To From Basics to Advanced]]" in content
    assert "[[Claude How-To:" not in content


def test_wikilink_preserves_collision_suffix(tmp_path):
    """When caller passes a filename stem with collision suffix, wikilink uses it as-is."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Duplicate Title (1)",
        url="https://example.com/dup",
        content_type="web_page",
        source="ingest-api",
        tags=[],
        capture_date=date(2026, 3, 30),
    )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    assert "[[Duplicate Title (1)]]" in content


def test_categorize_from_tags_priority():
    """First matching rule wins when tags span multiple categories."""
    # Marathon-specific tags should match Gaming, not the broader gaming rule
    assert categorize_from_tags(["marathon-game", "pvp-strategy"]) == "Gaming"
    # AI tags win
    assert categorize_from_tags(["claude-code", "prompt-engineering"]) == "AI & Dev Tools"
    # Horror tags
    assert categorize_from_tags(["horror-film", "psychological-horror"]) == "Horror & Film"
    # Work tags
    assert categorize_from_tags(["burnout-recovery", "career-coaching"]) == "Work & Leadership"


def test_categorize_from_tags_fallback():
    """No matching tags falls back to content-type, then Other."""
    assert categorize_from_tags([], "podcast") == "News & Current Events"
    assert categorize_from_tags([], "image") == "Images"
    assert categorize_from_tags([], "web_page") == "Other"
    assert categorize_from_tags([]) == "Other"


def test_dynamic_section_creation(tmp_path):
    """New sections are created dynamically before Other."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="AI Tool",
        url="https://example.com/ai",
        content_type="social_video",
        source="cli",
        tags=["claude-code"],
        capture_date=date(2026, 3, 30),
    )
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Horror Movie",
        url="https://example.com/horror",
        content_type="social_video",
        source="ingest-api",
        tags=["horror-film"],
        capture_date=date(2026, 3, 31),
    )
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="Random Link",
        url="https://example.com/random",
        content_type="web_page",
        source="ingest-api",
        tags=[],
        capture_date=date(2026, 3, 31),
    )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    # Topic sections should appear before Other
    ai_pos = content.index("## AI & Dev Tools")
    horror_pos = content.index("## Horror & Film")
    other_pos = content.index("## Other")
    assert ai_pos < other_pos
    assert horror_pos < other_pos
    # Entries land under their own section, not under Other
    # split("## ")[1] gives text after the header up to the next section
    ai_section = content[ai_pos:].split("## ")[1]
    assert "[[AI Tool]]" in ai_section
    horror_section = content[horror_pos:].split("## ")[1]
    assert "[[Horror Movie]]" in horror_section
    other_section = content[other_pos:]
    assert "[[Random Link]]" in other_section
    assert "[[AI Tool]]" not in other_section
    assert "[[Horror Movie]]" not in other_section


def test_entries_land_under_correct_section(tmp_path):
    """Multiple entries in the same category all go under the same section."""
    for i in range(3):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title=f"Marathon Tip {i}",
            url=f"https://example.com/marathon{i}",
            content_type="social_video",
            source="ingest-api",
            tags=["marathon-game", "gaming-tips"],
            capture_date=date(2026, 3, 30),
        )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    # Only one Gaming section header
    assert content.count("## Gaming") == 1
    # All three entries are under the Gaming section, not Other
    gaming_section = content.split("## Gaming")[1].split("## ")[0]
    for i in range(3):
        assert f"[[Marathon Tip {i}]]" in gaming_section


def test_no_tags_uses_content_type_fallback(tmp_path):
    """Entries with no tags use content-type fallback for categorization."""
    _append_to_weekly_log(
        inbox_dir=str(tmp_path),
        title="News Podcast",
        url="https://example.com/podcast",
        content_type="podcast",
        source="ingest-api",
        tags=[],
        capture_date=date(2026, 3, 30),
    )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    assert "## News & Current Events" in content
    assert "[[News Podcast]]" in content


# ---------------------------------------------------------------------------
# _parse_weekly_sections tests
# ---------------------------------------------------------------------------

def test_parse_weekly_sections_basic():
    """Extracts section names and entry titles from weekly log content."""
    content = """\
---
title: "Weekly Links — 2026-W14"
---
# Weekly Links — 2026-W14

## AI & Dev Tools
- 2026-03-30 — [[Claude Code Tips]] · [youtube](https://yt.com/1) · via ingest-api
- 2026-03-31 — [[LLM Patterns]] · [web_page](https://example.com) · via cli

## Horror & Film
- 2026-03-30 — [[Nosferatu Review]] · [web_page](https://example.com/horror) · via ingest-api

## Other
- 2026-03-31 — [[Random Link]] · [web_page](https://example.com/random) · via ingest-api
"""
    result = _parse_weekly_sections(content)
    assert result == {
        "AI & Dev Tools": ["Claude Code Tips", "LLM Patterns"],
        "Horror & Film": ["Nosferatu Review"],
        "Other": ["Random Link"],
    }


def test_parse_weekly_sections_empty_sections():
    """Sections with no entries return empty lists."""
    content = """\
---
title: "Weekly Links — 2026-W14"
---
# Weekly Links — 2026-W14

## Gaming

## Other
"""
    result = _parse_weekly_sections(content)
    assert result == {"Gaming": [], "Other": []}


def test_parse_weekly_sections_no_sections():
    """File with no ## headers returns empty dict."""
    content = """\
---
title: "Weekly Links — 2026-W14"
---
# Weekly Links — 2026-W14
"""
    result = _parse_weekly_sections(content)
    assert result == {}


# ---------------------------------------------------------------------------
# _categorize_via_llm tests
# ---------------------------------------------------------------------------

def _make_openrouter_response(category: str, reclassify: list | None = None) -> MagicMock:
    """Build a mock urllib response that returns an OpenRouter-shaped JSON payload."""
    payload = json.dumps({
        "choices": [{
            "message": {
                "content": json.dumps({
                    "category": category,
                    "reclassify": reclassify or [],
                })
            }
        }]
    }).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_categorize_via_llm_picks_existing_section():
    """LLM response naming an existing section is returned as-is."""
    existing_sections = {
        "AI & Dev Tools": ["Some AI Article"],
        "Gaming": ["Marathon Clip"],
        "Other": [],
    }

    mock_resp = _make_openrouter_response("AI & Dev Tools")

    with patch("pipeline.summarizer.get_secret", return_value="fake-key"), \
         patch("urllib.request.urlopen", return_value=mock_resp):
        result = _categorize_via_llm(
            title="Claude Code Tips",
            url="https://youtube.com/watch?v=abc",
            content_type="youtube",
            tags=["claude-code", "ai-agents"],
            existing_sections=existing_sections,
        )

    assert result["category"] == "AI & Dev Tools"
    assert result["reclassify"] == []


def test_categorize_via_llm_falls_back_on_api_error():
    """Any API failure returns the safe fallback dict."""
    existing_sections = {"Other": []}

    with patch("pipeline.summarizer.get_secret", return_value="fake-key"), \
         patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
        result = _categorize_via_llm(
            title="Some Article",
            url="https://example.com/article",
            content_type="web_page",
            tags=[],
            existing_sections=existing_sections,
        )

    assert result == {"category": "Other", "reclassify": []}


def test_categorize_via_llm_returns_reclassify_items():
    """LLM response with reclassify items is returned correctly."""
    existing_sections = {
        "AI & Dev Tools": ["Some AI Article"],
        "Other": ["Random Link", "Old Article"],
    }
    reclassify_items = [
        {"title": "Random Link", "to": "AI & Dev Tools"},
    ]
    mock_resp = _make_openrouter_response("Gaming", reclassify_items)

    with patch("pipeline.summarizer.get_secret", return_value="fake-key"), \
         patch("urllib.request.urlopen", return_value=mock_resp):
        result = _categorize_via_llm(
            title="Marathon Highlight",
            url="https://example.com/marathon",
            content_type="social_video",
            tags=["marathon-game"],
            existing_sections=existing_sections,
        )

    assert result["category"] == "Gaming"
    assert result["reclassify"] == reclassify_items
