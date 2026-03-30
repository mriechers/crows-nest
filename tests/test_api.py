"""Tests for the HTTP API layer."""
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from mcp_knowledge.api import create_api


def _make_test_client():
    mock_semantic = MagicMock()
    mock_semantic.search.return_value = [
        {
            "title": "Test",
            "snippet": "...",
            "score": 0.85,
            "similarity": 0.85,
            "source": "crows-nest",
            "search_type": "semantic",
            "path": "/test",
            "metadata": {},
        }
    ]
    mock_semantic.get_status.return_value = {
        "document_count": 42,
        "collection_name": "test",
    }

    app = create_api(semantic_index=mock_semantic)
    return TestClient(app), mock_semantic


def test_search_endpoint():
    client, mock = _make_test_client()
    r = client.post("/search", json={"query": "test"})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 1
    mock.search.assert_called_once()


def test_search_requires_query():
    client, _ = _make_test_client()
    r = client.post("/search", json={})
    assert r.status_code == 400


def test_search_passes_platform_and_n_results():
    client, mock = _make_test_client()
    r = client.post("/search", json={"query": "test", "n_results": 5, "platform": "youtube"})
    assert r.status_code == 200
    mock.search.assert_called_once_with(query="test", n_results=5, platform="youtube")


def test_search_with_knowledge_base():
    mock_semantic = MagicMock()
    mock_semantic.search.return_value = [{"title": "Semantic", "source": "crows-nest"}]
    mock_semantic.get_status.return_value = {}

    mock_kb = MagicMock()
    mock_kb.search_knowledge.return_value = [{"title": "Keyword", "source": "knowledge"}]

    app = create_api(semantic_index=mock_semantic, knowledge_base=mock_kb)
    client = TestClient(app)

    r = client.post("/search", json={"query": "test"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2
    titles = {item["title"] for item in results}
    assert titles == {"Semantic", "Keyword"}
    mock_kb.search_knowledge.assert_called_once_with(query="test", max_results=10)


def test_health_endpoint():
    client, _ = _make_test_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "crows-nest"


def test_status_endpoint():
    client, _ = _make_test_client()
    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()
    assert data["semantic"]["document_count"] == 42
    assert data["service"] == "crows-nest"


def test_reindex_not_implemented():
    client, _ = _make_test_client()
    r = client.post("/reindex")
    assert r.status_code == 501


def test_search_empty_results():
    mock_semantic = MagicMock()
    mock_semantic.search.return_value = []
    app = create_api(semantic_index=mock_semantic)
    client = TestClient(app)
    r = client.post("/search", json={"query": "nothing"})
    assert r.status_code == 200
    assert r.json()["results"] == []
