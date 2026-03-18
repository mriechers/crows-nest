# Example Knowledge Document

This is a placeholder document showing the expected format for knowledge base content.

## Overview

Replace this file with real documentation for your domain. Each document should:

- Focus on a single topic or concept
- Use clear headings (the `# ` title is used for search ranking)
- Be written in Markdown format

## How Search Works

The knowledge server extracts the first `# ` heading as the document title.
Title matches receive a significant score boost over content-only matches,
so choose descriptive, keyword-rich titles.

## Three-Artifact Pattern

When crawled via `scripts/crawl_docs.py`, each source produces three files:
- `example.md` — human-readable Markdown (this file, used for search)
- `example.html` — raw HTML source (preserved for reference)
- `example.json` — machine metadata (URL, timestamp, content length)

Only `.md` files are indexed by the search engine.
