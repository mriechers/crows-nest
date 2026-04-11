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


# ---------------------------------------------------------------------------
# /add-link tests
# ---------------------------------------------------------------------------

def _add_link_client(tmp_path, *, token="test-token"):
    """Build a test client with a per-test SQLite db and a known token."""
    mock_semantic = MagicMock()
    mock_semantic.search.return_value = []
    mock_semantic.get_status.return_value = {}
    db_path = str(tmp_path / "test.db")
    app = create_api(
        semantic_index=mock_semantic,
        api_token=token,
        db_path=db_path,
    )
    return TestClient(app), db_path


def test_add_link_requires_token(tmp_path):
    client, _ = _add_link_client(tmp_path)
    r = client.post("/add-link", json={"url": "https://example.com"})
    assert r.status_code == 401


def test_add_link_rejects_bad_token(tmp_path):
    client, _ = _add_link_client(tmp_path)
    r = client.post(
        "/add-link",
        json={"url": "https://example.com"},
        headers={"X-Crows-Nest-Token": "wrong"},
    )
    assert r.status_code == 401


def test_add_link_disabled_when_token_blank(tmp_path):
    """Blank token means the endpoint is administratively disabled."""
    client, _ = _add_link_client(tmp_path, token="")
    r = client.post(
        "/add-link",
        json={"url": "https://example.com"},
        headers={"X-Crows-Nest-Token": ""},
    )
    assert r.status_code == 503


def test_add_link_requires_url(tmp_path):
    client, _ = _add_link_client(tmp_path)
    r = client.post(
        "/add-link",
        json={},
        headers={"X-Crows-Nest-Token": "test-token"},
    )
    assert r.status_code == 400


def test_add_link_rejects_non_string_url(tmp_path):
    client, _ = _add_link_client(tmp_path)
    r = client.post(
        "/add-link",
        json={"url": 42},
        headers={"X-Crows-Nest-Token": "test-token"},
    )
    assert r.status_code == 400


def test_add_link_queues_url(tmp_path):
    client, db_path = _add_link_client(tmp_path)
    r = client.post(
        "/add-link",
        json={"url": "https://example.com", "context": "from the API"},
        headers={"X-Crows-Nest-Token": "test-token"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["source_type"] == "http"
    assert body["content_type"]  # classify_url returned something
    assert isinstance(body["id"], int)

    # Verify the row landed in the database with the expected source_type.
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT url, source_type, context, status FROM links WHERE id = ?",
            (body["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("https://example.com", "http", "from the API", "pending")


def test_add_link_honours_source_type_override(tmp_path):
    client, _ = _add_link_client(tmp_path)
    r = client.post(
        "/add-link",
        json={"url": "https://example.com/2", "source_type": "shortcut"},
        headers={"X-Crows-Nest-Token": "test-token"},
    )
    assert r.status_code == 201
    assert r.json()["source_type"] == "shortcut"


def test_add_link_duplicate_returns_409(tmp_path):
    client, _ = _add_link_client(tmp_path)
    headers = {"X-Crows-Nest-Token": "test-token"}
    first = client.post(
        "/add-link", json={"url": "https://dup.example"}, headers=headers,
    )
    assert first.status_code == 201
    second = client.post(
        "/add-link", json={"url": "https://dup.example"}, headers=headers,
    )
    assert second.status_code == 409


def test_add_link_invalid_json(tmp_path):
    client, _ = _add_link_client(tmp_path)
    r = client.post(
        "/add-link",
        content=b"not json",
        headers={
            "X-Crows-Nest-Token": "test-token",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400
