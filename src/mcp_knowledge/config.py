"""Configuration constants — edit these when forking for your domain."""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Server identity
# ---------------------------------------------------------------------------

# FORK: Change these to describe your specific knowledge domain.
SERVER_NAME = "my-knowledge-server"
SERVER_DESCRIPTION = "Expert knowledge about [your domain]"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# These resolve relative to this file's location (src/mcp_knowledge/), so they
# always point to the project root regardless of where the server is invoked.
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge"
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

# ---------------------------------------------------------------------------
# Search tuning
# ---------------------------------------------------------------------------

# Maximum results returned when max_results is not specified by the caller.
MAX_RESULTS_DEFAULT = 5

# Characters of surrounding context to include in each excerpt.
EXCERPT_CONTEXT_CHARS = 500

# Truncate full-document returns at this many characters to keep token budgets
# manageable. Set to 0 to disable truncation.
MAX_DOCUMENT_CHARS = 15_000

# Score boost when the full query string matches a document's title.
TITLE_FULL_MATCH_BOOST = 50

# Score boost per individual query term that appears in a document's title.
TITLE_TERM_BOOST = 10

# ---------------------------------------------------------------------------
# Media archive
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Path to the media archive directory (pipeline output)
MEDIA_ROOT = os.environ.get(
    "MEDIA_ROOT",
    str(_PROJECT_ROOT / "media"),
)

# ---------------------------------------------------------------------------
# MCP Transport
# ---------------------------------------------------------------------------

# Transport mode: "stdio" (default, backward-compat) or "sse" (HTTP daemon)
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")

# SSE transport settings (only used when MCP_TRANSPORT=sse)
MCP_SSE_HOST = os.environ.get("MCP_SSE_HOST", "127.0.0.1")
MCP_SSE_PORT = int(os.environ.get("MCP_SSE_PORT", "27185"))

