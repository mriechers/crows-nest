"""Tests for the mcp_knowledge MCP adapter and knowledge tools.

Checks tool registration and the shape of tool return values.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so pipeline imports resolve (or fail
# gracefully) the same way they do at runtime.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp_knowledge import knowledge as knowledge_mod
from mcp_knowledge.mcp_adapter import (
    _get_server_info,
    _list_topics,
    _search_knowledge,
    create_mcp_server,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_doc(dir_: Path, rel_path: str, content: str) -> Path:
    target = dir_ / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


@pytest.fixture()
def knowledge_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect knowledge and log dirs to tmp_path subdirs."""
    k_dir = tmp_path / "knowledge"
    k_dir.mkdir()
    l_dir = tmp_path / "logs"
    l_dir.mkdir()

    monkeypatch.setattr(knowledge_mod, "_resolve_knowledge_dir", lambda: k_dir)
    monkeypatch.setattr(knowledge_mod, "_resolve_log_dir", lambda: l_dir)

    return tmp_path


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def test_all_tools_registered(self) -> None:
        from mcp.types import ListToolsRequest

        server = create_mcp_server()
        handler = server.request_handlers[ListToolsRequest]
        req = ListToolsRequest(method="tools/list", params=None)
        result = asyncio.run(handler(req))
        # ServerResult is a root-model union; unwrap via .root
        tool_names = {t.name for t in result.root.tools}
        expected = {
            "search_knowledge",
            "list_topics",
            "get_document",
            "get_server_info",
            "list_recent_articles",
            "search_articles",
            "mark_surfaced",
            "manage_feeds",
            "list_all_articles",
            "pipeline_queue",
            "pipeline_retry",
        }
        assert expected == tool_names, (
            f"Expected tools {expected}, got {tool_names}"
        )


# ---------------------------------------------------------------------------
# get_server_info shape
# ---------------------------------------------------------------------------


class TestGetServerInfo:
    def test_returns_expected_keys(self, knowledge_root: Path) -> None:
        result = _get_server_info()
        assert isinstance(result, dict)
        required_keys = {"name", "description", "document_count", "categories", "last_refreshed"}
        assert required_keys == set(result.keys()), (
            f"Missing or unexpected keys. Got: {set(result.keys())}"
        )

    def test_document_count_reflects_files(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/a.md", "# A\n\ncontent\n")
        make_doc(k_dir, "guides/b.md", "# B\n\ncontent\n")

        result = _get_server_info()
        assert result["document_count"] == 2

    def test_categories_list(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/a.md", "# A\n\ncontent\n")
        make_doc(k_dir, "policies/b.md", "# B\n\ncontent\n")

        result = _get_server_info()
        assert sorted(result["categories"]) == ["guides", "policies"]

    def test_name_and_description_present(self, knowledge_root: Path) -> None:
        result = _get_server_info()
        assert isinstance(result["name"], str) and result["name"]
        assert isinstance(result["description"], str) and result["description"]


# ---------------------------------------------------------------------------
# list_topics shape
# ---------------------------------------------------------------------------


class TestListTopics:
    def test_returns_list_of_dicts(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/doc.md", "# Guide\n\ncontent\n")

        result = _list_topics()
        assert isinstance(result, list)

    def test_each_item_has_required_keys(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/doc1.md", "# G1\n\ncontent\n")
        make_doc(k_dir, "guides/doc2.md", "# G2\n\ncontent\n")
        make_doc(k_dir, "policies/pol.md", "# P\n\ncontent\n")

        result = _list_topics()
        assert len(result) == 2  # "guides" and "policies"

        for item in result:
            assert "category" in item, "missing 'category' key"
            assert "document_count" in item, "missing 'document_count' key"

    def test_document_counts_are_accurate(self, knowledge_root: Path) -> None:
        k_dir = knowledge_root / "knowledge"
        make_doc(k_dir, "guides/a.md", "# A\n\ncontent\n")
        make_doc(k_dir, "guides/b.md", "# B\n\ncontent\n")
        make_doc(k_dir, "policies/p.md", "# P\n\ncontent\n")

        result = _list_topics()
        counts = {item["category"]: item["document_count"] for item in result}
        assert counts["guides"] == 2
        assert counts["policies"] == 1
