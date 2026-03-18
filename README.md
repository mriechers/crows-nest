# MCP Knowledge Server Template

A reusable template for building domain-specific [MCP](https://modelcontextprotocol.io/) knowledge servers. Fork this repo, add your content, and give any AI agent instant expertise in your domain.

## What You Get

- **4 MCP tools**: search, list topics, get document, server info
- **3 MCP resources**: sources manifest, document list, individual documents
- **Title-boosted search**: document titles receive significant ranking boosts over content-only matches
- **Excerpt extraction**: ~500 char context windows keep token costs low
- **Gap logging**: queries with no results are logged to `logs/missing_info.json` — a backlog of what your knowledge base is missing
- **Crawl4AI ingestion**: automated pipeline for scraping documentation into the three-artifact format (`.md`, `.html`, `.json`)
- **GitHub Actions**: weekly freshness checks + tiered monthly/quarterly content refresh with auto-PRs
- **Docker support**: for isolated deployment

## Quick Start

### 1. Fork and clone

```bash
# Use GitHub's "Use this template" button, or:
gh repo create my-knowledge-server --template mriechers/mcp-knowledge-server-template
cd my-knowledge-server
```

### 2. Customize `src/mcp_knowledge/config.py`

This is the only file you *must* edit:

```python
SERVER_NAME = "my-domain-server"
SERVER_DESCRIPTION = "Expert knowledge about my specific domain"
```

### 3. Add your knowledge

**Option A: Crawl from URLs**

Edit `knowledge/sources.json` to list your documentation sources, then:

```bash
pip install crawl4ai
python -m playwright install chromium
python scripts/crawl_docs.py
```

**Option B: Add manually**

Create `.md` files in `knowledge/your-category/`:

```
knowledge/
├── sources.json
├── your-category/
│   └── your-topic.md    # Start with a # Title heading
```

### 4. Install and run

```bash
# Option 1: uvx (recommended for MCP)
pip install -e .
mcp-knowledge-server

# Option 2: Docker
docker build -t my-knowledge-server .
docker run my-knowledge-server
```

### 5. Connect to your AI tool

Add to your MCP client configuration (e.g., `~/.claude.json`):

```json
{
  "mcpServers": {
    "my-knowledge-server": {
      "command": "uvx",
      "args": ["--from", "/path/to/your/server", "mcp-knowledge-server"]
    }
  }
}
```

## Customization Checklist

- [ ] Edit `config.py` — server name, description, search tuning
- [ ] Edit `AGENTS.md` — replace `[your domain]` with actual domain description
- [ ] Edit `knowledge/sources.json` — add your documentation sources
- [ ] Run `crawl_docs.py` or add `.md` files manually
- [ ] Remove `knowledge/example-topic/` once you have real content
- [ ] Update this `README.md` with your project details

## Search Tuning

All search parameters live in `config.py`:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `MAX_RESULTS_DEFAULT` | 5 | Results per search |
| `EXCERPT_CONTEXT_CHARS` | 500 | Characters in excerpt window |
| `MAX_DOCUMENT_CHARS` | 15,000 | Truncation limit for full documents |
| `TITLE_FULL_MATCH_BOOST` | 50 | Score boost for full query in title |
| `TITLE_TERM_BOOST` | 10 | Score boost per query term in title |

## Content Freshness

### Automated (GitHub Actions)

- **Weekly**: `check-knowledge-freshness.yml` scans for stale content (>90 days) and creates a GitHub Issue
- **Monthly** (Tier 1): `refresh-knowledge.yml` re-crawls frequently-changing sources
- **Quarterly** (Tier 2): Same workflow re-crawls stable reference material

### Manual

```bash
python scripts/crawl_docs.py                    # All sources
python scripts/crawl_docs.py --tier 1           # Tier 1 only
python scripts/crawl_docs.py --category guides  # One category
python scripts/crawl_docs.py --dry-run          # Preview without writing
```

## Deployment Options

| Method | Best For | Command |
|--------|----------|---------|
| **uvx** | Local MCP integration | `uvx --from . mcp-knowledge-server` |
| **pip install** | Development | `pip install -e . && mcp-knowledge-server` |
| **Docker** | Isolated/shared deployment | `docker build -t srv . && docker run srv` |

## Architecture

```
src/mcp_knowledge/
├── config.py      # All tunable constants (edit this)
├── knowledge.py   # Search engine, document loading, gap logging
└── server.py      # FastMCP wrapper (thin, rarely needs editing)
```

The knowledge module does the heavy lifting. The server is a thin wrapper that wires FastMCP tools and resources to the knowledge module's functions.

## License

MIT
