# Knowledge Server Agent Instructions

## What This Server Knows

This is a domain-specific knowledge server providing curated, searchable documentation about **[your domain]**. It contains pre-indexed documents organized by category, with keyword search, excerpt extraction, and full document retrieval.

## Available Tools

### `search_knowledge(query, category?, max_results=5, full_document=False)`
**Your primary tool.** Search the knowledge base using natural language keywords.
- Returns ranked results with relevance scores
- By default returns short excerpts (~500 chars) to minimize token usage
- Set `full_document=True` only when you need the complete text
- Use `category` to narrow results to a specific topic area

### `list_topics()`
**Start here for discovery.** Returns all categories with document counts (~100 tokens).
Call this first to understand what knowledge is available before searching.

### `get_document(path)`
**Direct retrieval.** Fetch a specific document by its path when you already know what you need.
Use paths from search results or the `knowledge://documents` resource.

### `get_server_info()`
**Metadata.** Returns server identity, document count, categories, and last refresh timestamp.
Useful for understanding the scope and freshness of the knowledge base.

## Resources (Read-Only)

- `knowledge://sources` — Source manifest showing where content was crawled from
- `knowledge://documents` — Complete list of all document paths
- `knowledge://document/{path}` — Read a specific document without a tool call

## Usage Patterns

### Quick lookup
1. `search_knowledge("your question")` — get excerpts
2. If an excerpt looks relevant, `get_document(path)` for full text

### Broad exploration
1. `list_topics()` — see what categories exist
2. `search_knowledge(query, category="specific-area")` — narrow search

### When results are insufficient
- Try different keyword combinations
- Search without a category filter
- Use `full_document=True` if excerpts don't provide enough context
- If no results found, the query is logged for future knowledge expansion

## Knowledge Structure

Documents are organized in `knowledge/` by category subdirectories:
```
knowledge/
├── sources.json          # Where content came from
├── category-name/
│   ├── document.md       # Searchable content (indexed)
│   ├── document.html     # Raw source (not indexed)
│   └── document.json     # Metadata (timestamps, URLs)
```

Only `.md` files are searchable. The first `# ` heading in each file is used as the document title and receives a significant score boost in search results.

## Extending This Server

To add new knowledge:
1. Add a source entry to `knowledge/sources.json`
2. Run `python scripts/crawl_docs.py` to fetch and convert
3. Or manually create `.md` files in the appropriate category directory
