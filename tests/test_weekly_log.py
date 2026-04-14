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
    _reclassify_entries,
)


def _mock_llm(category, reclassify=None):
    """Return a patch context that mocks _categorize_via_llm to return given category."""
    return patch(
        "pipeline.summarizer._categorize_via_llm",
        return_value={"category": category, "reclassify": reclassify or []},
    )


def test_creates_weekly_log_on_first_capture(tmp_path):
    """First capture of the week creates the weekly log file."""
    with _mock_llm("Work & Leadership"):
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
    with _mock_llm("AI & Dev Tools"):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title="First Video",
            url="https://youtube.com/watch?v=123",
            content_type="youtube",
            source="ingest-api",
            tags=["claude-code", "ai-agents"],
            capture_date=date(2026, 3, 30),
        )
    with _mock_llm("Horror & Film"):
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
    assert "## AI & Dev Tools" in content
    assert "## Horror & Film" in content


def test_wikilink_uses_sanitized_title(tmp_path):
    """Wikilinks should use the sanitized title to match the actual filename."""
    with _mock_llm("AI & Dev Tools"):
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
    with _mock_llm("Other"):
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


def test_dynamic_section_creation(tmp_path):
    """New sections are created dynamically before Other."""
    with _mock_llm("AI & Dev Tools"):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title="AI Tool",
            url="https://example.com/ai",
            content_type="social_video",
            source="cli",
            tags=["claude-code"],
            capture_date=date(2026, 3, 30),
        )
    with _mock_llm("Horror & Film"):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title="Horror Movie",
            url="https://example.com/horror",
            content_type="social_video",
            source="ingest-api",
            tags=["horror-film"],
            capture_date=date(2026, 3, 31),
        )
    with _mock_llm("Other"):
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
    with _mock_llm("Gaming"):
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
    assert content.count("## Gaming") == 1
    gaming_section = content.split("## Gaming")[1].split("## ")[0]
    for i in range(3):
        assert f"[[Marathon Tip {i}]]" in gaming_section


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
# _reclassify_entries tests
# ---------------------------------------------------------------------------

def test_reclassify_moves_entry_between_sections():
    """Entry is removed from Other and placed under the target section."""
    lines = [
        "## AI & Dev Tools\n",
        "- 2026-03-30 — [[Claude Tips]] · [youtube](https://yt.com/1) · via ingest-api\n",
        "\n",
        "## Other\n",
        "- 2026-03-31 — [[LLM Patterns]] · [web_page](https://ex.com) · via cli\n",
        "- 2026-03-31 — [[Random Link]] · [web_page](https://ex.com/r) · via cli\n",
    ]
    reclassify = [{"title": "LLM Patterns", "to": "AI & Dev Tools"}]
    result = _reclassify_entries(lines, reclassify)

    text = "".join(result)
    # LLM Patterns should now be under AI & Dev Tools
    ai_section = text.split("## AI & Dev Tools")[1].split("## ")[0]
    assert "[[LLM Patterns]]" in ai_section
    # And removed from Other
    other_section = text.split("## Other")[1]
    assert "[[LLM Patterns]]" not in other_section
    assert "[[Random Link]]" in other_section


def test_reclassify_creates_new_section_before_other():
    """If the target section doesn't exist, it's created before Other."""
    lines = [
        "## Other\n",
        "- 2026-03-31 — [[Horror Movie]] · [web_page](https://ex.com) · via cli\n",
    ]
    reclassify = [{"title": "Horror Movie", "to": "Horror & Film"}]
    result = _reclassify_entries(lines, reclassify)

    text = "".join(result)
    assert "## Horror & Film" in text
    # New section should appear before Other
    horror_pos = text.index("## Horror & Film")
    other_pos = text.index("## Other")
    assert horror_pos < other_pos
    # Entry moved
    horror_section = text.split("## Horror & Film")[1].split("## ")[0]
    assert "[[Horror Movie]]" in horror_section


def test_reclassify_no_match_is_noop():
    """If the title doesn't match any entry, lines are unchanged."""
    lines = [
        "## Other\n",
        "- 2026-03-31 — [[Something]] · [web_page](https://ex.com) · via cli\n",
    ]
    reclassify = [{"title": "Nonexistent Title", "to": "AI & Dev Tools"}]
    result = _reclassify_entries(lines, reclassify)
    assert result == lines


def test_reclassify_empty_instructions():
    """Empty reclassify list returns lines unchanged."""
    lines = ["## Other\n", "- entry\n"]
    result = _reclassify_entries(lines, [])
    assert result == lines


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


def test_categorize_via_llm_falls_back_on_network_error():
    """Network error returns the safe fallback dict."""
    existing_sections = {"Other": []}

    with patch("pipeline.summarizer.get_secret", return_value="fake-key"), \
         patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = _categorize_via_llm(
            title="Some Article",
            url="https://example.com/article",
            content_type="web_page",
            tags=[],
            existing_sections=existing_sections,
        )

    assert result == {"category": "Other", "reclassify": []}


def test_categorize_via_llm_sanitizes_adversarial_category():
    """Category with newlines or injection attempts is sanitized."""
    existing_sections = {"Other": []}

    # Simulate LLM returning a category with newlines (prompt injection attempt)
    adversarial_category = "Other\n- 2026-01-01 — [[Injected]] · [web](http://evil.com) · via attacker"
    mock_resp = _make_openrouter_response(adversarial_category)

    with patch("pipeline.summarizer.get_secret", return_value="fake-key"), \
         patch("urllib.request.urlopen", return_value=mock_resp):
        result = _categorize_via_llm(
            title="Test",
            url="https://example.com",
            content_type="web_page",
            tags=[],
            existing_sections=existing_sections,
        )

    # Newlines stripped, no injection possible
    assert "\n" not in result["category"]
    assert "[[Injected]]" not in result["category"]
    assert len(result["category"]) <= 50


def test_categorize_via_llm_empty_category_falls_back():
    """Empty or whitespace-only category falls back to Other."""
    existing_sections = {"Other": []}
    mock_resp = _make_openrouter_response("   ")

    with patch("pipeline.summarizer.get_secret", return_value="fake-key"), \
         patch("urllib.request.urlopen", return_value=mock_resp):
        result = _categorize_via_llm(
            title="Test",
            url="https://example.com",
            content_type="web_page",
            tags=[],
            existing_sections=existing_sections,
        )

    assert result["category"] == "Other"


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


# ---------------------------------------------------------------------------
# Integration tests: _append_to_weekly_log with mocked LLM
# ---------------------------------------------------------------------------

def test_append_uses_llm_categorization(tmp_path):
    """_append_to_weekly_log uses LLM to pick the section."""
    llm_result = {"category": "AI & Dev Tools", "reclassify": []}

    with patch("pipeline.summarizer._categorize_via_llm", return_value=llm_result):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title="Claude Tips",
            url="https://example.com/tips",
            content_type="youtube",
            source="ingest-api",
            tags=["claude-code"],
            capture_date=date(2026, 3, 30),
        )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    assert "## AI & Dev Tools" in content
    assert "[[Claude Tips]]" in content
    # Entry is under AI & Dev Tools, not Other
    ai_section = content.split("## AI & Dev Tools")[1].split("## ")[0]
    assert "[[Claude Tips]]" in ai_section


def test_append_llm_fallback_lands_in_other(tmp_path):
    """When LLM falls back to Other, entry goes under Other."""
    llm_result = {"category": "Other", "reclassify": []}

    with patch("pipeline.summarizer._categorize_via_llm", return_value=llm_result):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title="Random Link",
            url="https://example.com/random",
            content_type="web_page",
            source="cli",
            tags=[],
            capture_date=date(2026, 3, 30),
        )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    other_section = content.split("## Other")[1]
    assert "[[Random Link]]" in other_section


def test_append_with_reclassification(tmp_path):
    """LLM reclassify instructions move existing entries between sections."""
    # First entry goes to Other (no LLM reclassify on first call)
    with patch("pipeline.summarizer._categorize_via_llm",
               return_value={"category": "Other", "reclassify": []}):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title="Early AI Article",
            url="https://example.com/ai1",
            content_type="web_page",
            source="ingest-api",
            tags=[],
            capture_date=date(2026, 3, 30),
        )

    # Second entry — LLM now recognizes the AI theme and reclassifies
    with patch("pipeline.summarizer._categorize_via_llm",
               return_value={
                   "category": "AI & Dev Tools",
                   "reclassify": [{"title": "Early AI Article", "to": "AI & Dev Tools"}],
               }):
        _append_to_weekly_log(
            inbox_dir=str(tmp_path),
            title="Claude Deep Dive",
            url="https://example.com/ai2",
            content_type="youtube",
            source="ingest-api",
            tags=["claude-code"],
            capture_date=date(2026, 3, 31),
        )

    content = (tmp_path / "Weekly Links — 2026-W14.md").read_text()
    ai_section = content.split("## AI & Dev Tools")[1].split("## ")[0]
    assert "[[Claude Deep Dive]]" in ai_section
    assert "[[Early AI Article]]" in ai_section
    # Other should no longer have Early AI Article
    other_section = content.split("## Other")[1]
    assert "[[Early AI Article]]" not in other_section
