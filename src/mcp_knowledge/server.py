"""MCP server exposing tools and resources for a domain-specific knowledge base.

This file is intentionally thin — it wires FastMCP to the knowledge module.
When forking, you should rarely need to edit this file. Customization points:
  - config.py: server name, description, paths, search tuning
  - knowledge/: your domain documents (.md files)
  - knowledge/sources.json: manifest of your source documents
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from . import config, knowledge

mcp = FastMCP(config.SERVER_NAME)


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
    mcp.run()


if __name__ == "__main__":
    main()
