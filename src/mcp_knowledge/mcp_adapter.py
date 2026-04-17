"""MCP adapter for crows-nest knowledge server.

Low-level MCP Server wrapping all tool implementations.
No sys.path hacks here — pipeline imports are handled in __main__.py.
"""

from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.types import TextContent, Tool

from . import config, knowledge

logger = logging.getLogger("mcp_knowledge.mcp_adapter")

# ---------------------------------------------------------------------------
# Pipeline db imports — attempted at module level; graceful fallback if absent
# ---------------------------------------------------------------------------

try:
    from pipeline.db import (
        add_feed as _db_add_feed,
        get_connection as _db_get_connection,
        get_pipeline_status as _db_get_pipeline_status,
        get_top_articles as _db_get_top_articles,
        list_feeds as _db_list_feeds,
        mark_articles_surfaced as _db_mark_articles_surfaced,
    )
    from pipeline.config import DB_PATH as _DB_PATH

    _RSS_AVAILABLE = True
except ImportError as _rss_exc:
    logger.warning("RSS db unavailable — pipeline import failed: %s", _rss_exc)
    _RSS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Tool implementations (sync — called from async call_tool)
# ---------------------------------------------------------------------------


def _search_knowledge(
    query: str,
    category: str | None = None,
    max_results: int = 5,
    full_document: bool = False,
) -> list[dict]:
    return knowledge.search_knowledge(
        query=query,
        category=category,
        max_results=max_results,
        full_document=full_document,
    )


def _list_topics() -> list[dict]:
    categories = knowledge.list_categories()
    result: list[dict] = []
    for cat in categories:
        doc_count = len(knowledge.list_documents(category=cat))
        result.append({"category": cat, "document_count": doc_count})
    return result


def _get_document(path: str) -> str:
    content = knowledge.get_document(path)
    if content is None:
        return f"Document not found: {path}"
    return content


def _get_server_info() -> dict:
    sources = knowledge.load_sources()
    all_docs = knowledge.list_documents()
    categories = knowledge.list_categories()

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


def _list_recent_articles(limit: int = 8, max_age_hours: int = 48) -> list[dict]:
    if not _RSS_AVAILABLE:
        return [{"error": "RSS db unavailable — pipeline deps not installed"}]
    max_age_days = max_age_hours / 24
    return _db_get_top_articles(limit=limit, max_age_days=max_age_days, db_path=_DB_PATH)


def _search_articles(query: str, max_results: int = 10) -> list[dict]:
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


def _mark_surfaced(article_ids: list[int]) -> dict:
    if not _RSS_AVAILABLE:
        return {"error": "RSS db unavailable — pipeline deps not installed"}
    if not article_ids:
        return {"marked": 0}
    _db_mark_articles_surfaced(article_ids, db_path=_DB_PATH)
    return {"marked": len(article_ids), "article_ids": article_ids}


def _manage_feeds(
    action: str,
    url: str | None = None,
    title: str | None = None,
    tier: int | None = None,
) -> dict | list[dict]:
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
            article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
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

    if action == "deactivate":
        if not url:
            return {"error": "url is required for action='deactivate'"}
        conn = _db_get_connection(_DB_PATH)
        try:
            cursor = conn.execute(
                "UPDATE feeds SET active = 0 WHERE url = ?", (url,)
            )
            conn.commit()
            if cursor.rowcount == 0:
                return {"error": f"Feed not found: {url}"}
            return {"deactivated": True, "url": url}
        finally:
            conn.close()

    return {
        "error": f"Unknown action '{action}'. Use 'list', 'add', 'stats', or 'deactivate'."
    }


def _list_all_articles(
    feed_url: str | None = None,
    surfaced: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if not _RSS_AVAILABLE:
        return {"error": "RSS db unavailable"}
    conn = _db_get_connection(_DB_PATH)
    try:
        conditions = []
        params: list = []

        if feed_url:
            conditions.append("f.url = ?")
            params.append(feed_url)
        if surfaced is not None:
            conditions.append("a.surfaced = ?")
            params.append(1 if surfaced else 0)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_sql = f"SELECT COUNT(*) FROM articles a JOIN feeds f ON a.feed_id = f.id {where}"
        total = conn.execute(count_sql, params).fetchone()[0]

        sql = f"""SELECT a.id, a.title, a.url, a.summary, a.score, a.published_at,
                         a.surfaced, f.title AS feed_title, f.url AS feed_url, f.tier
                  FROM articles a
                  JOIN feeds f ON a.feed_id = f.id
                  {where}
                  ORDER BY a.published_at DESC
                  LIMIT ? OFFSET ?"""
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()

        return {
            "articles": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


def _pipeline_queue(recent_limit: int = 20) -> dict:
    if not _RSS_AVAILABLE:
        return {"error": "Pipeline db unavailable — pipeline deps not installed"}
    return _db_get_pipeline_status(recent_limit=recent_limit, db_path=_DB_PATH)


def _pipeline_retry(link_id: int) -> dict:
    if not _RSS_AVAILABLE:
        return {"error": "Pipeline db unavailable"}
    from pipeline.db import claim_link

    success = claim_link(link_id, from_status="error", to_status="pending", db_path=_DB_PATH)
    if success:
        return {"retried": True, "link_id": link_id}
    return {"retried": False, "error": f"Link {link_id} not in error state"}


# ---------------------------------------------------------------------------
# MCP Server factory
# ---------------------------------------------------------------------------

_TOOLS: list[Tool] = [
    Tool(
        name="search_knowledge",
        description=(
            "Search the knowledge base using keyword matching with title boosting. "
            "Returns the most relevant documents ranked by score. By default returns "
            "a short excerpt around the first matching term; set full_document=True "
            "to retrieve the complete document text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search string."},
                "category": {
                    "type": "string",
                    "description": "Optional top-level category to restrict the search.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5).",
                    "default": 5,
                },
                "full_document": {
                    "type": "boolean",
                    "description": "When True, return full document text instead of excerpt.",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="list_topics",
        description=(
            "List all knowledge base categories with their document counts. "
            "Useful for discovery — call this first to understand what topics are "
            "covered before running search_knowledge."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_document",
        description=(
            "Retrieve the full text of a specific knowledge document by path. "
            "Use list_topics() to discover available paths."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": 'Relative path within knowledge/, e.g. "category/topic/doc.md".',
                }
            },
            "required": ["path"],
        },
    ),
    Tool(
        name="get_server_info",
        description=(
            "Return metadata about this knowledge server instance. "
            "Includes the server name, domain description, total document count, "
            "available categories, and the last-refresh timestamp from sources.json."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_recent_articles",
        description=(
            "Return top-scored unsurfaced RSS articles from the ephemeral cache. "
            "Articles are ranked by score (tier + recency + keyword signal)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of articles to return (default 8).",
                    "default": 8,
                },
                "max_age_hours": {
                    "type": "integer",
                    "description": "Only include articles published within this many hours (default 48).",
                    "default": 48,
                },
            },
        },
    ),
    Tool(
        name="search_articles",
        description=(
            "Keyword search across RSS article titles and summaries. "
            "Case-insensitive substring match, ordered by score descending."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search string to match against article title and summary.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="mark_surfaced",
        description=(
            "Mark RSS articles as surfaced so they are excluded from future queries. "
            "Call after including articles in a briefing or reading digest."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "article_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of article IDs to mark as surfaced.",
                }
            },
            "required": ["article_ids"],
        },
    ),
    Tool(
        name="manage_feeds",
        description=(
            "Manage RSS feed subscriptions. "
            'Actions: "list", "add", "stats", "deactivate".'
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": 'One of "list", "add", "stats", or "deactivate".',
                },
                "url": {"type": "string", "description": 'Feed URL (required for "add").'},
                "title": {
                    "type": "string",
                    "description": 'Human-readable feed title (optional for "add").',
                },
                "tier": {
                    "type": "integer",
                    "description": 'Priority tier 1-3 for scoring (optional for "add", default 2).',
                },
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="list_all_articles",
        description=(
            "List RSS articles with optional filtering by feed and surfaced status. "
            "Returns articles ordered by published date (newest first) with pagination."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "feed_url": {
                    "type": "string",
                    "description": "Filter to articles from this feed URL only.",
                },
                "surfaced": {
                    "type": "boolean",
                    "description": "Filter by surfaced status. Omit for all.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum articles to return (default 50).",
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip this many articles for pagination (default 0).",
                    "default": 0,
                },
            },
        },
    ),
    Tool(
        name="pipeline_queue",
        description=(
            "Return the current state of the content preservation pipeline. "
            "Shows items waiting to be processed and recently completed saves."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "recent_limit": {
                    "type": "integer",
                    "description": "Maximum number of recent completions to include (default 20).",
                    "default": 20,
                }
            },
        },
    ),
    Tool(
        name="pipeline_retry",
        description="Reset an errored pipeline item back to pending for reprocessing.",
        inputSchema={
            "type": "object",
            "properties": {
                "link_id": {
                    "type": "integer",
                    "description": "ID of the link to retry.",
                }
            },
            "required": ["link_id"],
        },
    ),
]


def create_mcp_server() -> Server:
    """Build and return the crows-nest MCP Server."""
    server = Server("crows-nest")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        result: object

        if name == "search_knowledge":
            result = _search_knowledge(
                query=arguments["query"],
                category=arguments.get("category"),
                max_results=arguments.get("max_results", 5),
                full_document=arguments.get("full_document", False),
            )

        elif name == "list_topics":
            result = _list_topics()

        elif name == "get_document":
            result = _get_document(arguments["path"])

        elif name == "get_server_info":
            result = _get_server_info()

        elif name == "list_recent_articles":
            result = _list_recent_articles(
                limit=arguments.get("limit", 8),
                max_age_hours=arguments.get("max_age_hours", 48),
            )

        elif name == "search_articles":
            result = _search_articles(
                query=arguments["query"],
                max_results=arguments.get("max_results", 10),
            )

        elif name == "mark_surfaced":
            result = _mark_surfaced(arguments["article_ids"])

        elif name == "manage_feeds":
            result = _manage_feeds(
                action=arguments["action"],
                url=arguments.get("url"),
                title=arguments.get("title"),
                tier=arguments.get("tier"),
            )

        elif name == "list_all_articles":
            result = _list_all_articles(
                feed_url=arguments.get("feed_url"),
                surfaced=arguments.get("surfaced"),
                limit=arguments.get("limit", 50),
                offset=arguments.get("offset", 0),
            )

        elif name == "pipeline_queue":
            result = _pipeline_queue(recent_limit=arguments.get("recent_limit", 20))

        elif name == "pipeline_retry":
            result = _pipeline_retry(arguments["link_id"])

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        if isinstance(result, str):
            return [TextContent(type="text", text=result)]
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server
