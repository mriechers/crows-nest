"""Tests for SemanticIndex: ChromaDB-backed semantic search."""
import tempfile

import pytest

from mcp_knowledge.embeddings import EmbeddingProvider
from mcp_knowledge.semantic import SemanticIndex


@pytest.fixture(scope="module")
def provider():
    return EmbeddingProvider()


def make_docs():
    return [
        {
            "title": "Python Asyncio Deep Dive",
            "text": "Asyncio is a Python library for writing concurrent code using async and await syntax. It enables cooperative multitasking and is ideal for IO-bound workloads.",
            "path": "youtube/asyncio-deep-dive",
            "metadata": {
                "platform": "youtube",
                "creator": "ArjanCodes",
                "content_type": "video",
                "url": "https://youtube.com/watch?v=abc123",
            },
        },
        {
            "title": "Introduction to Sourdough Baking",
            "text": "Sourdough bread relies on wild yeast fermentation. A healthy starter produces carbon dioxide bubbles which leaven the dough.",
            "path": "youtube/sourdough-baking",
            "metadata": {
                "platform": "youtube",
                "creator": "BreadBaker",
                "content_type": "video",
                "url": "https://youtube.com/watch?v=xyz789",
            },
        },
    ]


def test_index_and_search(provider):
    """Index two documents; searching for async programming should rank the Python doc first."""
    with tempfile.TemporaryDirectory() as tmp:
        idx = SemanticIndex(tmp, provider)
        count = idx.index_documents(make_docs())
        assert count == 2

        results = idx.search("concurrent programming with async await", n_results=2)
        assert len(results) > 0
        assert results[0]["path"] == "youtube/asyncio-deep-dive"

        # Result fields present
        first = results[0]
        assert "title" in first
        assert "snippet" in first
        assert "score" in first
        assert "similarity" in first
        assert first["source"] == "crows-nest"
        assert first["search_type"] == "semantic"
        assert len(first["snippet"]) <= 300


def test_search_empty_index(provider):
    """Searching an empty collection should return an empty list, not raise."""
    with tempfile.TemporaryDirectory() as tmp:
        idx = SemanticIndex(tmp, provider)
        results = idx.search("anything at all")
        assert results == []


def test_reindex_replaces_documents(provider):
    """Re-indexing the same paths with different content should replace, not duplicate."""
    with tempfile.TemporaryDirectory() as tmp:
        idx = SemanticIndex(tmp, provider)
        idx.index_documents(make_docs())
        assert idx.document_count() == 2

        # Re-index with updated docs (same paths, different text)
        updated = [
            {
                "title": "Python Asyncio Updated",
                "text": "Updated content about event loops.",
                "path": "youtube/asyncio-deep-dive",
                "metadata": {
                    "platform": "youtube",
                    "creator": "ArjanCodes",
                    "content_type": "video",
                    "url": "https://youtube.com/watch?v=abc123",
                },
            }
        ]
        idx.index_documents(updated)
        # Should still be 2, not 3 — upsert replaced the existing doc
        assert idx.document_count() == 2


def test_get_status(provider):
    """Status dict should include collection_name and correct document_count."""
    with tempfile.TemporaryDirectory() as tmp:
        idx = SemanticIndex(tmp, provider)
        idx.index_documents(make_docs())

        status = idx.get_status()
        assert status["collection_name"] == SemanticIndex.COLLECTION_NAME
        assert status["document_count"] == 2


def test_platform_filter(provider):
    """Platform filter should narrow results to matching platform only."""
    docs = make_docs() + [
        {
            "title": "Async Podcast Episode",
            "text": "A podcast discussing Python's async ecosystem, event loops, and concurrency patterns.",
            "path": "podcast/async-episode",
            "metadata": {
                "platform": "podcast",
                "creator": "PythonPodcast",
                "content_type": "audio",
                "url": "https://podcast.example.com/ep1",
            },
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        idx = SemanticIndex(tmp, provider)
        idx.index_documents(docs)

        results = idx.search("async programming", n_results=5, platform="podcast")
        assert all(r["metadata"].get("platform") == "podcast" for r in results)


def test_clear_resets_collection(provider):
    """After clear(), document_count should be 0."""
    with tempfile.TemporaryDirectory() as tmp:
        idx = SemanticIndex(tmp, provider)
        idx.index_documents(make_docs())
        assert idx.document_count() == 2

        idx.clear()
        assert idx.document_count() == 0
