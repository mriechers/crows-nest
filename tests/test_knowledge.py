"""Tests for the mcp_knowledge.knowledge module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mcp_knowledge.knowledge as knowledge_mod
from mcp_knowledge.config import EXCERPT_CONTEXT_CHARS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_doc(dir_: Path, rel_path: str, content: str) -> Path:
    """Write a markdown document at dir_ / rel_path and return the path."""
    target = dir_ / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def knowledge_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a temporary knowledge dir and redirect module globals to it."""
    k_dir = tmp_path / "knowledge"
    k_dir.mkdir()
    l_dir = tmp_path / "logs"
    l_dir.mkdir()

    monkeypatch.setattr(knowledge_mod, "_resolve_knowledge_dir", lambda: k_dir)
    monkeypatch.setattr(knowledge_mod, "_resolve_log_dir", lambda: l_dir)

    return tmp_path  # callers can use tmp_path / "knowledge" or / "logs"


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestSearchRanking:
    def test_title_boost_outscores_content_only(
        self, knowledge_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Doc with query term in title should rank above doc with many content mentions."""
        k_dir = knowledge_root / "knowledge"

        # Doc A: query term in title, sparse content
        make_doc(
            k_dir,
            "guides/alpha.md",
            "# Frobnicate\n\nThis document covers the basics.\n",
        )

        # Doc B: no title match, many content mentions
        body = "frobnicate " * 50
        make_doc(
            k_dir,
            "guides/beta.md",
            f"# Unrelated Topic\n\n{body}\n",
        )

        results = knowledge_mod.search_knowledge("frobnicate")
        assert len(results) >= 2

        # alpha must come before beta
        paths = [r["path"] for r in results]
        assert paths.index("guides/alpha.md") < paths.index("guides/beta.md")


class TestExcerptExtraction:
    def test_excerpt_contains_term(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        padding = "x " * 300  # 600 chars of filler before the target term
        make_doc(
            k_dir,
            "guides/doc.md",
            f"# Some Doc\n\n{padding}unique_search_term{padding}\n",
        )

        results = knowledge_mod.search_knowledge("unique_search_term")
        assert results, "expected at least one result"
        excerpt = results[0]["excerpt"]
        assert "unique_search_term" in excerpt

    def test_excerpt_approximate_length(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        padding = "x " * 500  # well over EXCERPT_CONTEXT_CHARS
        make_doc(
            k_dir,
            "guides/long.md",
            f"# Long Doc\n\n{padding}keyword{padding}\n",
        )

        results = knowledge_mod.search_knowledge("keyword")
        assert results
        excerpt = results[0]["excerpt"]
        # Should be roughly EXCERPT_CONTEXT_CHARS; allow generous tolerance for
        # ellipsis markers and window alignment.
        assert len(excerpt) <= EXCERPT_CONTEXT_CHARS + 50

    def test_excerpt_has_ellipsis_markers(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        padding = "word " * 200  # enough that keyword is neither at start nor end
        make_doc(
            k_dir,
            "guides/ellipsis.md",
            f"# Ellipsis Doc\n\n{padding}needle{padding}\n",
        )

        results = knowledge_mod.search_knowledge("needle")
        assert results
        excerpt = results[0]["excerpt"]
        # Keyword is deep inside a long document — both sides should be elided
        assert excerpt.startswith("..."), "expected leading ellipsis"
        assert excerpt.endswith("..."), "expected trailing ellipsis"


class TestGapLogging:
    def test_no_results_creates_log(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        l_dir = knowledge_root / "logs"
        # No documents — any search should log a gap
        results = knowledge_mod.search_knowledge("completely_absent_term_xyz")
        assert results == []

        log_file = l_dir / "missing_info.json"
        assert log_file.is_file(), "missing_info.json should be created"

        data = json.loads(log_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) >= 1

        entry = data[-1]
        assert entry["query"] == "completely_absent_term_xyz"
        assert entry["status"] == "no_results"
        # Timestamp should be a valid ISO string
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(entry["timestamp"])
        assert dt.tzinfo is not None, "timestamp should be timezone-aware"

    def test_no_results_returns_empty_list(self, knowledge_root: Path) -> None:
        results = knowledge_mod.search_knowledge("nothing_matches_zzz")
        assert results == []


class TestEmptyResults:
    def test_empty_knowledge_dir(self, knowledge_root: Path) -> None:
        # knowledge dir is empty (no .md files) — expect empty list
        results = knowledge_mod.search_knowledge("anything")
        assert results == []


class TestCategoryFiltering:
    def test_only_matching_category_returned(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "alpha/doc1.md", "# Alpha Doc\n\nshared keyword here\n")
        make_doc(k_dir, "beta/doc2.md", "# Beta Doc\n\nshared keyword here\n")

        results = knowledge_mod.search_knowledge("keyword", category="alpha")
        assert results, "expected results from alpha category"
        for r in results:
            assert r["path"].startswith("alpha/"), (
                f"unexpected path outside alpha: {r['path']}"
            )

    def test_no_results_for_wrong_category(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "alpha/doc.md", "# Alpha Doc\n\nkeyword here\n")

        results = knowledge_mod.search_knowledge("keyword", category="beta")
        assert results == []


class TestFullDocumentMode:
    def test_full_document_returns_content_key(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/full.md", "# Full Doc\n\nsome content here\n")

        results = knowledge_mod.search_knowledge("content", full_document=True)
        assert results
        for r in results:
            assert "content" in r, "full_document mode should return 'content' key"
            assert "excerpt" not in r, "full_document mode must not return 'excerpt'"

    def test_default_mode_returns_excerpt_key(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/excerpt.md", "# Excerpt Doc\n\nexcerpt content\n")

        results = knowledge_mod.search_knowledge("excerpt")
        assert results
        for r in results:
            assert "excerpt" in r
            assert "content" not in r


class TestListCategories:
    def test_all_categories_returned(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "cats/doc.md", "# Cat\n\nmeow\n")
        make_doc(k_dir, "dogs/doc.md", "# Dog\n\nbark\n")
        make_doc(k_dir, "birds/doc.md", "# Bird\n\ntweet\n")

        categories = knowledge_mod.list_categories()
        assert sorted(categories) == ["birds", "cats", "dogs"]

    def test_root_files_excluded(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        # File directly in knowledge/ root — should NOT appear as a category
        (k_dir / "README.md").write_text("# Root\n\nroot doc\n", encoding="utf-8")
        make_doc(k_dir, "real_category/doc.md", "# Cat\n\ncontent\n")

        categories = knowledge_mod.list_categories()
        assert "README" not in categories
        assert "real_category" in categories

    def test_empty_knowledge_dir(self, knowledge_root: Path) -> None:
        categories = knowledge_mod.list_categories()
        assert categories == []


class TestGetDocument:
    def test_loads_known_document(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/known.md", "# Known\n\nhello world\n")

        content = knowledge_mod.get_document("guides/known.md")
        assert content is not None
        assert "hello world" in content

    def test_returns_none_for_nonexistent_path(self, knowledge_root: Path) -> None:
        result = knowledge_mod.get_document("no/such/file.md")
        assert result is None
