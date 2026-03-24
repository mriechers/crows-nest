"""Knowledge base loader and search for domain-specific documentation.

This module is the workhorse of the template. It handles:
- Loading .md files from the knowledge/ directory tree
- Keyword search with title-boosted scoring
- Excerpt extraction around matched terms
- Gap logging for queries that return no results (feeds content backlog)

Most of the search behavior can be tuned via config.py without touching
this file. The public API (load_all_documents, search_knowledge, etc.) is
consumed by server.py and should stay stable when forking.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    EXCERPT_CONTEXT_CHARS,
    KNOWLEDGE_DIR,
    LOG_DIR,
    MAX_DOCUMENT_CHARS,
    MAX_RESULTS_DEFAULT,
    TITLE_FULL_MATCH_BOOST,
    TITLE_TERM_BOOST,
)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_knowledge_dir() -> Path:
    """Return the knowledge directory, falling back to a cwd-based path.

    The config value is resolved relative to this file's location, which
    works correctly when the server is installed via uvx. The cwd fallback
    helps during local development when running from the project root.
    """
    if KNOWLEDGE_DIR.is_dir():
        return KNOWLEDGE_DIR
    cwd_knowledge = Path.cwd() / "knowledge"
    if cwd_knowledge.is_dir():
        return cwd_knowledge
    return KNOWLEDGE_DIR


def _resolve_log_dir() -> Path:
    """Return the log directory, mirroring _resolve_knowledge_dir logic."""
    if LOG_DIR.is_dir():
        return LOG_DIR
    cwd_logs = Path.cwd() / "logs"
    if cwd_logs.is_dir():
        return cwd_logs
    return LOG_DIR


# ---------------------------------------------------------------------------
# Document loading
# ---------------------------------------------------------------------------


def load_all_documents() -> dict[str, str]:
    """Load all .md files from the knowledge directory tree.

    Returns a dict mapping relative paths (e.g. "category/topic/doc.md")
    to their UTF-8 text content. Files that cannot be read are silently
    skipped so a single bad file doesn't break the whole server.
    """
    knowledge_dir = _resolve_knowledge_dir()
    docs: dict[str, str] = {}
    if not knowledge_dir.is_dir():
        return docs
    for md_file in sorted(knowledge_dir.rglob("*.md")):
        rel = md_file.relative_to(knowledge_dir).as_posix()
        try:
            docs[rel] = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
    return docs


def get_document(path: str) -> str | None:
    """Load a single document by its relative path inside knowledge/.

    Returns None if the file does not exist or cannot be read.
    If MAX_DOCUMENT_CHARS is set (>0), the returned text is truncated.
    """
    knowledge_dir = _resolve_knowledge_dir()
    target = knowledge_dir / path
    if not target.is_file():
        return None
    try:
        content = target.read_text(encoding="utf-8")
    except OSError:
        return None
    if MAX_DOCUMENT_CHARS and len(content) > MAX_DOCUMENT_CHARS:
        content = content[:MAX_DOCUMENT_CHARS] + "\n\n[truncated]"
    return content


def list_documents(category: str | None = None) -> list[str]:
    """List available document paths, optionally filtered by category prefix.

    Category is a top-level directory name (e.g. "policies", "guides").
    Returns paths in sorted order.
    """
    knowledge_dir = _resolve_knowledge_dir()
    if not knowledge_dir.is_dir():
        return []
    paths: list[str] = []
    for md_file in sorted(knowledge_dir.rglob("*.md")):
        rel = md_file.relative_to(knowledge_dir).as_posix()
        if category and not rel.startswith(category):
            continue
        paths.append(rel)
    return paths


def list_categories() -> list[str]:
    """List top-level knowledge base categories (top-level subdirectory names).

    Files sitting directly in knowledge/ (with no subdirectory) are excluded
    because they don't belong to a named category.
    """
    knowledge_dir = _resolve_knowledge_dir()
    if not knowledge_dir.is_dir():
        return []
    categories: set[str] = set()
    for md_file in knowledge_dir.rglob("*.md"):
        rel = md_file.relative_to(knowledge_dir).as_posix()
        top = rel.split("/")[0]
        if top != rel:  # skip files at the knowledge/ root
            categories.add(top)
    return sorted(categories)


def load_sources() -> dict:
    """Load the sources.json manifest from the knowledge directory.

    Returns an empty manifest dict if the file is absent, so callers don't
    need to guard for None.
    """
    knowledge_dir = _resolve_knowledge_dir()
    sources_path = knowledge_dir / "sources.json"
    if not sources_path.is_file():
        return {"sources": []}
    return json.loads(sources_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _extract_title(content: str, path: str) -> str:
    """Extract a human-readable title from a document.

    Checks the first ``# `` heading in the first 20 lines. Falls back to
    deriving a title from the filename stem.
    """
    for line in content.splitlines()[:20]:
        if line.startswith("# "):
            return line[2:].strip()
    # Derive from filename: strip extension, replace hyphens/underscores with spaces.
    stem = Path(path).stem.replace("-", " ").replace("_", " ")
    return stem.title()


def search_knowledge(
    query: str,
    category: str | None = None,
    max_results: int = MAX_RESULTS_DEFAULT,
    full_document: bool = False,
) -> list[dict]:
    """Search knowledge documents by keyword matching with title boosting.

    Scoring algorithm:
    - Full query string match in title: +TITLE_FULL_MATCH_BOOST
    - Each individual query term in title: +TITLE_TERM_BOOST
    - Each occurrence of each query term in content body: +1

    Results are sorted by descending score. If no results are found the query
    is logged via ``_log_gap`` to surface content gaps.

    Args:
        query: Free-text search string.
        category: Optional top-level category prefix to restrict results.
        max_results: Maximum number of results to return.
        full_document: When True, return full document text (up to
            MAX_DOCUMENT_CHARS) instead of a short excerpt.

    Returns:
        List of dicts with keys: ``path``, ``score``, and either ``excerpt``
        or ``content`` depending on ``full_document``.
    """
    docs = load_all_documents()
    query_lower = query.lower()
    query_terms = query_lower.split()
    if not query_terms:
        return []

    scored: list[tuple[str, str, int]] = []
    for path, content in docs.items():
        if category and not path.startswith(category):
            continue

        title = _extract_title(content, path)
        title_lower = title.lower()
        content_lower = content.lower()

        score = 0

        # Title boosts (high signal)
        if query_lower in title_lower:
            score += TITLE_FULL_MATCH_BOOST
        for term in query_terms:
            if term in title_lower:
                score += TITLE_TERM_BOOST

        # Content term frequency
        score += sum(content_lower.count(term) for term in query_terms)

        if score > 0:
            scored.append((path, content, score))

    scored.sort(key=lambda x: x[2], reverse=True)

    if not scored:
        _log_gap(query)
        return []

    results: list[dict] = []
    for path, content, score in scored[:max_results]:
        if full_document:
            text = content
            if MAX_DOCUMENT_CHARS and len(text) > MAX_DOCUMENT_CHARS:
                text = text[:MAX_DOCUMENT_CHARS] + "\n\n[truncated]"
            results.append({"source": "crows-nest", "path": path, "content": text, "score": score})
        else:
            excerpt = _extract_excerpt(content, query_terms)
            results.append({"source": "crows-nest", "path": path, "excerpt": excerpt, "score": score})
    return results


def _extract_excerpt(
    content: str,
    query_terms: list[str],
    context_chars: int = EXCERPT_CONTEXT_CHARS,
) -> str:
    """Extract the most relevant excerpt from content around the first term match.

    Finds the earliest position of any query term and returns a window of
    ``context_chars`` characters centred on that position. Adds ellipsis
    markers when the excerpt is not at the start or end of the document.
    """
    content_lower = content.lower()
    best_pos = -1
    for term in query_terms:
        pos = content_lower.find(term)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos

    if best_pos == -1:
        return content[:context_chars] + ("..." if len(content) > context_chars else "")

    start = max(0, best_pos - context_chars // 2)
    end = min(len(content), best_pos + context_chars // 2)
    excerpt = content[start:end]
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(content):
        excerpt = excerpt + "..."
    return excerpt


# ---------------------------------------------------------------------------
# Gap logging
# ---------------------------------------------------------------------------


def _log_gap(query: str) -> None:
    """Append a no-results query to logs/missing_info.json.

    This creates a backlog of topics not yet covered by the knowledge base.
    Failures are silent — logging should never crash the server.
    """
    log_dir = _resolve_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    log_file = log_dir / "missing_info.json"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "status": "no_results",
    }

    try:
        data: list[dict] = []
        if log_file.is_file():
            text = log_file.read_text(encoding="utf-8").strip()
            if text:
                data = json.loads(text)
        data.append(entry)
        log_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass  # Fail silently — logging must not crash the server
