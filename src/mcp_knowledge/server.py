"""MCP server exposing tools and resources for a domain-specific knowledge base.

This file is intentionally thin — it wires FastMCP to the knowledge module.
When forking, you should rarely need to edit this file. Customization points:
  - config.py: server name, description, paths, search tuning
  - knowledge/: your domain documents (.md files)
  - knowledge/sources.json: manifest of your source documents
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("mcp_knowledge.server")

from . import config, knowledge

# ---------------------------------------------------------------------------
# Pipeline db import — pipeline/ is a package at the project root, above src/
# Add the project root to sys.path so `from pipeline.db import ...` resolves.
# ---------------------------------------------------------------------------

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from pipeline.db import (
        add_feed as _db_add_feed,
        get_connection as _db_get_connection,
        get_top_articles as _db_get_top_articles,
        list_feeds as _db_list_feeds,
        mark_articles_surfaced as _db_mark_articles_surfaced,
    )
    from pipeline.config import DB_PATH as _DB_PATH
    _RSS_AVAILABLE = True
except ImportError as _rss_exc:
    logger.warning("RSS db unavailable — pipeline import failed: %s", _rss_exc)
    _RSS_AVAILABLE = False

mcp = FastMCP(
    config.SERVER_NAME,
    host=config.MCP_SSE_HOST,
    port=config.MCP_SSE_PORT,
)

_semantic_index = None


def _get_semantic_index():
    """Lazy-init the semantic index. Returns None if deps not installed."""
    global _semantic_index
    if _semantic_index is None:
        try:
            from .embeddings import EmbeddingProvider
            from .semantic import SemanticIndex
            provider = EmbeddingProvider(model_name=config.EMBEDDING_MODEL)
            _semantic_index = SemanticIndex(
                data_path=config.SEMANTIC_DATA_DIR,
                embedding_provider=provider,
            )
        except ImportError as exc:
            logger.warning("Semantic search unavailable (missing deps): %s", exc)
    return _semantic_index


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_knowledge(
    query: str,
    category: str | None = None,
    max_results: int = 5,
    full_document: bool = False,
) -> list[dict]:
    """Search the knowledge base using keyword matching with title boosting.

    Returns the most relevant documents ranked by score. By default returns
    a short excerpt around the first matching term; set full_document=True
    to retrieve the complete document text.

    Args:
        query: Free-text search string.
        category: Optional top-level category to restrict the search.
        max_results: Maximum number of results to return (default 5).
        full_document: When True, return full document text instead of excerpt.
    """
    return knowledge.search_knowledge(
        query=query,
        category=category,
        max_results=max_results,
        full_document=full_document,
    )


@mcp.tool()
def list_topics() -> list[dict]:
    """List all knowledge base categories with their document counts.

    Useful for discovery — call this first to understand what topics are
    covered before running search_knowledge. Returns roughly 100 tokens.
    """
    categories = knowledge.list_categories()
    result: list[dict] = []
    for cat in categories:
        doc_count = len(knowledge.list_documents(category=cat))
        result.append({"category": cat, "document_count": doc_count})
    return result


@mcp.tool()
def get_document(path: str) -> str:
    """Retrieve the full text of a specific knowledge document by path.

    Use list_topics() or knowledge://documents to discover available paths.

    Args:
        path: Relative path within knowledge/, e.g. "category/topic/doc.md".
    """
    content = knowledge.get_document(path)
    if content is None:
        return f"Document not found: {path}"
    return content


@mcp.tool()
def get_server_info() -> dict:
    """Return metadata about this knowledge server instance.

    Includes the server name, domain description, total document count,
    available categories, and the last-refresh timestamp from sources.json.
    """
    sources = knowledge.load_sources()
    all_docs = knowledge.list_documents()
    categories = knowledge.list_categories()

    # Extract last_refreshed from sources manifest if present.
    last_refreshed: str | None = sources.get("last_refreshed")
    if not last_refreshed:
        source_list = sources.get("sources", [])
        if source_list and isinstance(source_list[-1], dict):
            last_refreshed = source_list[-1].get("last_fetched")

    return {
        "name": config.SERVER_NAME,
        "description": config.SERVER_DESCRIPTION,
        "document_count": len(all_docs),
        "categories": categories,
        "last_refreshed": last_refreshed,
    }


@mcp.tool()
def semantic_search(
    query: str,
    n_results: int = 10,
    platform: str | None = None,
) -> list[dict]:
    """Search media archive transcripts using semantic similarity.

    Args:
        query: Natural language search query
        n_results: Max results to return (default 10)
        platform: Optional filter by platform (YouTube, TikTok, etc.)
    """
    index = _get_semantic_index()
    if index is None:
        return [{"error": "Semantic search not available — install with pip install -e '.[semantic]'"}]
    return index.search(query=query, n_results=n_results, platform=platform)


@mcp.tool()
def reindex_media() -> dict:
    """Reindex the media archive for semantic search."""
    index = _get_semantic_index()
    if index is None:
        return {"error": "Semantic search not available"}
    from .media_loader import load_media_documents
    docs = load_media_documents(config.MEDIA_ROOT)
    count = index.index_documents(docs)
    return {"indexed": count, "status": "complete"}


@mcp.tool()
def media_status() -> dict:
    """Get semantic search index status."""
    index = _get_semantic_index()
    if index is None:
        return {"status": "unavailable", "reason": "semantic deps not installed"}
    return index.get_status()


# ---------------------------------------------------------------------------
# RSS tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_recent_articles(limit: int = 8, max_age_hours: int = 48) -> list[dict]:
    """Return top-scored unsurfaced RSS articles from the ephemeral cache.

    Articles are ranked by score (tier + recency + keyword signal). Use this
    to populate a morning briefing or discover high-value items.

    Args:
        limit: Maximum number of articles to return (default 8).
        max_age_hours: Only include articles published within this many hours
            of now (default 48).
    """
    if not _RSS_AVAILABLE:
        return [{"error": "RSS db unavailable — pipeline deps not installed"}]
    max_age_days = max_age_hours / 24
    return _db_get_top_articles(limit=limit, max_age_days=max_age_days, db_path=_DB_PATH)


@mcp.tool()
def search_articles(query: str, max_results: int = 10) -> list[dict]:
    """Keyword search across RSS article titles and summaries.

    Case-insensitive substring match. Returns results ordered by score
    descending so the most relevant items come first.

    Args:
        query: Search string to match against article title and summary.
        max_results: Maximum number of results to return (default 10).
    """
    if not _RSS_AVAILABLE:
        return [{"error": "RSS db unavailable — pipeline deps not installed"}]
    conn = _db_get_connection(_DB_PATH)
    try:
        pattern = f"%{query.lower()}%"
        rows = conn.execute(
            """SELECT a.id, a.title, a.url, a.summary, a.score, a.published_at,
                      a.surfaced, f.title AS feed_title, f.tier AS feed_tier
               FROM articles a
               JOIN feeds f ON a.feed_id = f.id
               WHERE (LOWER(a.title) LIKE ? OR LOWER(a.summary) LIKE ?)
               ORDER BY a.score DESC
               LIMIT ?""",
            (pattern, pattern, max_results),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@mcp.tool()
def mark_surfaced(article_ids: list[int]) -> dict:
    """Mark RSS articles as surfaced so they are excluded from future queries.

    Call this after including articles in a briefing or reading digest to
    prevent them from appearing in subsequent list_recent_articles calls.

    Args:
        article_ids: List of article IDs (from list_recent_articles or
            search_articles) to mark as surfaced.
    """
    if not _RSS_AVAILABLE:
        return {"error": "RSS db unavailable — pipeline deps not installed"}
    if not article_ids:
        return {"marked": 0}
    _db_mark_articles_surfaced(article_ids, db_path=_DB_PATH)
    return {"marked": len(article_ids), "article_ids": article_ids}


@mcp.tool()
def manage_feeds(
    action: str,
    url: str | None = None,
    title: str | None = None,
    tier: int | None = None,
) -> dict | list[dict]:
    """Manage RSS feed subscriptions.

    Actions:
      - "list": Return all active feeds with metadata.
      - "add": Add a new feed. Requires url. tier defaults to 2 if omitted.
      - "stats": Return feed and article counts from the database.

    Args:
        action: One of "list", "add", or "stats".
        url: Feed URL (required for "add").
        title: Human-readable feed title (optional for "add").
        tier: Priority tier 1–3 for scoring (optional for "add", default 2).
    """
    if not _RSS_AVAILABLE:
        return {"error": "RSS db unavailable — pipeline deps not installed"}

    if action == "list":
        return _db_list_feeds(active_only=True, db_path=_DB_PATH)

    if action == "add":
        if not url:
            return {"error": "url is required for action='add'"}
        feed_id = _db_add_feed(
            url=url,
            title=title,
            tier=tier if tier is not None else 2,
            db_path=_DB_PATH,
        )
        return {"feed_id": feed_id, "url": url, "title": title, "tier": tier or 2}

    if action == "stats":
        conn = _db_get_connection(_DB_PATH)
        try:
            feed_count = conn.execute(
                "SELECT COUNT(*) FROM feeds WHERE active = 1"
            ).fetchone()[0]
            article_count = conn.execute(
                "SELECT COUNT(*) FROM articles"
            ).fetchone()[0]
            unsurfaced_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE surfaced = 0"
            ).fetchone()[0]
            return {
                "active_feeds": feed_count,
                "total_articles": article_count,
                "unsurfaced_articles": unsurfaced_count,
            }
        finally:
            conn.close()

    return {"error": f"Unknown action '{action}'. Use 'list', 'add', or 'stats'."}


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("knowledge://sources")
def get_sources() -> str:
    """The sources.json manifest — lists all knowledge source documents and metadata."""
    return json.dumps(knowledge.load_sources(), indent=2)


@mcp.resource("knowledge://documents")
def list_all_documents() -> str:
    """All available document paths, one per line."""
    docs = knowledge.list_documents()
    return "\n".join(docs)


@mcp.resource("knowledge://document/{path}")
def get_knowledge_document(path: str) -> str:
    """Read a specific knowledge document by its relative path."""
    content = knowledge.get_document(path)
    if content is None:
        return f"Document not found: {path}"
    return content


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Transport: CLI arg > config > default (stdio)
    transport = sys.argv[1] if len(sys.argv) > 1 else config.MCP_TRANSPORT

    if config.ENABLE_HTTP_API:
        index = _get_semantic_index()
        if index is None:
            logger.warning("HTTP API requires semantic deps; skipping API start")
        else:
            import threading
            def _run_api():
                try:
                    import asyncio
                    from .api import start_api_server
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(
                        start_api_server(
                            semantic_index=index,
                            host=config.HTTP_HOST,
                            port=config.HTTP_PORT,
                        )
                    )
                except Exception:
                    logger.exception("HTTP API thread failed to start")
            api_thread = threading.Thread(target=_run_api, daemon=True)
            api_thread.start()

    logger.info("Starting MCP server with transport=%s", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
