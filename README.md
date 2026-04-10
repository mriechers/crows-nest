# Crow's Nest

Two systems in one repo: a **Signal-to-Obsidian content preservation pipeline** and an **MCP knowledge server**.

The pipeline captures URLs shared via Signal, iMessage, or a Cloudflare ingest queue, processes them (media download, Whisper transcription, Claude summarization), and writes structured Obsidian notes. The MCP server exposes a keyword-searchable knowledge base and RSS feed tools to any MCP-compatible AI client.

---

## Content Preservation Pipeline

### How It Works

URLs enter the pipeline from multiple sources and move through a SQLite state machine:

```
pending -> downloading -> transcribed -> summarized -> archived
```

Each stage is an independent script that claims work atomically and updates the state machine on completion.

### Input Sources

| Source | Script | How It Works |
|--------|--------|-------------|
| Signal messages | `pipeline/signal_listener.py` | Polls signal-cli every 5 min, extracts URLs |
| iMessage self-messages | `pipeline/imessage_listener.py` | Polls local iMessage DB for messages you sent to yourself |
| Cloudflare ingest queue | `pipeline/ingest_poller.py` | Drains a D1 queue populated by a Cloudflare Worker |
| Obsidian notes | `pipeline/obsidian_scanner.py` | Scans vault for notes tagged `pending-clippings` |
| CLI | `pipeline/add_link.py` | Manually queue a URL from the command line |

### Pipeline Stages

**Stage 1 — Listener** (`signal_listener.py`, `imessage_listener.py`, `ingest_poller.py`)

URLs are extracted and saved to SQLite with status `pending`. Signal sends a confirmation reply. The ingest poller additionally marks items synced in the remote D1 queue.

**Stage 2 — Processor** (`processor.py`)

Routes each item by content type and processes it:
- **YouTube / social video**: yt-dlp download, audio extraction via ffmpeg, Whisper transcription, thumbnail extraction
- **Podcast / audio**: yt-dlp download, Whisper transcription
- **Web pages**: requests-based scraping, og_image thumbnail download
- **Images**: HEIC-to-JPEG conversion (sips or ImageMagick), resize to 1568px longest edge

```bash
python pipeline/processor.py [--limit N] [--drain] [--db PATH]
```

`--limit` caps items per run (default 20). `--drain` loops until the queue is empty.

**Stage 3 — Summarizer** (`summarizer.py`)

Picks up `transcribed` items, calls Claude Haiku via OpenRouter for structured analysis, and writes Obsidian notes to `2 - AREAS/INTERNET CLIPPINGS/` with vault-convention YAML frontmatter and content-type tags.

```bash
python pipeline/summarizer.py [--limit N] [--drain] [--db PATH]
```

`--limit` caps items per run (default 5). `--drain` loops until done.

**Stage 4 — Archiver** (`archiver.py`)

Uploads media files to the `crows-nest-media-archive` R2 bucket with correct `Content-Type` headers for inline browser playback. Generates share URLs via `share.bymarkriechers.com` and writes them back to the database and the Obsidian note. Web pages are saved to Readwise Reader instead of R2.

```bash
python pipeline/archiver.py [--db PATH]
```

### Maintenance Scripts

| Script | Purpose |
|--------|---------|
| `cleanup_media.py` | Delete local media directories for archived items older than N days. Preserves DB records, Obsidian notes, and vault archive images. |
| `sync_clippings.py` | Idempotent sync of Obsidian clippings with DB and current spec. |
| `backfill_video.py` | Re-download video for items that only have audio. |
| `status.py` | Dashboard and health check. |

```bash
python pipeline/cleanup_media.py [--days N] [--dry-run] [--db PATH]
python pipeline/status.py           # pipeline dashboard
python pipeline/status.py --health  # health check (exit 0/1)
```

### Database

SQLite at `{CROWS_NEST_HOME}/data/crows-nest.db`. Two schemas:

- **Pipeline schema**: `links` (status machine), `processing_log`, `signal_messages`
- **RSS schema**: `feeds`, `articles` (ephemeral cache with TTL expiry)

---

## MCP Knowledge Server

A domain-specific knowledge server with keyword search over curated markdown documents and RSS feed tools for morning briefing pipelines.

### Tools

**Knowledge tools**

| Tool | Description |
|------|-------------|
| `search_knowledge(query, category?, max_results, full_document)` | Keyword search with title boosting and excerpt extraction |
| `list_topics()` | Categories with document counts |
| `get_document(path)` | Fetch full document by path |
| `get_server_info()` | Server name, description, doc count, categories, last refreshed |

**RSS tools** (requires `pip install -e ".[rss]"`)

| Tool | Description |
|------|-------------|
| `list_recent_articles(limit, max_age_hours)` | Top-scored unsurfaced articles from the ephemeral cache |
| `search_articles(query, max_results)` | Keyword search across article titles and summaries |
| `mark_surfaced(article_ids)` | Mark articles as seen so they are excluded from future queries |
| `manage_feeds(action, url?, title?, tier?)` | List, add, or get stats for RSS feed subscriptions |

### Knowledge Structure

```
knowledge/
├── sources.json           # Source manifest
└── category-name/
    ├── document.md        # Searchable (indexed)
    ├── document.html      # Raw source (not indexed)
    └── document.json      # Metadata
```

Add documents by editing `knowledge/sources.json` and running `python scripts/crawl_docs.py`, or by creating `.md` files directly.

---

## Setup

### Prerequisites

- Python 3.11+
- For media processing: `yt-dlp`, `ffmpeg`, Whisper (via `whisper-transcribe.sh`)
- For image processing: `sips` (macOS built-in) or ImageMagick
- For R2 archival: AWS-compatible credentials (see below)

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"      # base + tests
pip install -e ".[archive]"  # + boto3 for R2 archival
pip install -e ".[rss]"      # + feedparser for RSS
pip install -e ".[all]"      # everything
```

### Environment Variables

All paths are configurable. Defaults work for a standard macOS dev setup.

| Variable | Default | Purpose |
|----------|---------|---------|
| `CROWS_NEST_HOME` | `~/Developer/second-brain/crows-nest` | Repo root |
| `OBSIDIAN_VAULT` | `~/Developer/second-brain/obsidian/MarkBrain` | Vault root |
| `MEDIA_ROOT` | `{CROWS_NEST_HOME}/media` | Media storage |
| `CROWS_NEST_INGEST_API_URL` | `https://share.bymarkriechers.com/api` | Cloudflare Worker endpoint |

### Secrets

Stored in macOS Keychain (env var fallback for Linux):

```bash
# Signal
security add-generic-password -a "$USER" -s "developer.workspace.SIGNAL_USER" -w "+1..." -U

# OpenRouter (for Claude Haiku summarization)
security add-generic-password -a "$USER" -s "developer.workspace.OPENROUTER_API_KEY" -w "sk-..." -U

# R2 archival
security add-generic-password -a "$USER" -s "developer.workspace.R2_ACCESS_KEY_ID" -w "your-key" -U
security add-generic-password -a "$USER" -s "developer.workspace.R2_SECRET_ACCESS_KEY" -w "your-secret" -U
security add-generic-password -a "$USER" -s "developer.workspace.R2_ENDPOINT_URL" -w "https://<account>.r2.cloudflarestorage.com" -U

# Ingest API token
security add-generic-password -a "$USER" -s "developer.workspace.CROWS_NEST_INGEST_API_TOKEN" -w "your-token" -U
```

On Linux, set the same names as environment variables.

### MCP Client Configuration

Add to your MCP client config (e.g., `~/.claude.json`):

```json
{
  "mcpServers": {
    "crows-nest": {
      "command": "uvx",
      "args": ["--from", "/path/to/crows-nest", "mcp-knowledge-server"]
    }
  }
}
```

Or with `pip install -e .`:

```bash
mcp-knowledge-server
```

---

## Scheduling

### macOS (launchd)

Plists in `config/launchd/`, installed to `~/Library/LaunchAgents/`:

| Plist | Runs |
|-------|------|
| `com.crows-nest.listener.plist` | Signal listener (every 5 min) |
| `com.crows-nest.imessage-listener.plist` | iMessage listener |
| `com.crows-nest.ingest-poller.plist` | Cloudflare queue poller |
| `com.crows-nest.processor.plist` | Content processor |
| `com.crows-nest.summarizer.plist` | Summarizer |
| `com.crows-nest.archiver.plist` | R2 archiver |
| `com.crows-nest.rss-refresh.plist` | RSS feed refresh |
| `com.crows-nest.obsidian-scanner.plist` | Obsidian vault scanner |

```bash
cp config/launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.crows-nest.listener.plist
```

### Linux (systemd)

Service/timer pairs in `config/systemd/` for listener, processor, summarizer, and archiver.

```bash
sudo cp config/systemd/* /etc/systemd/system/
sudo systemctl enable --now crows-nest-listener.timer
```

---

## Architecture

```
crows-nest/
├── pipeline/
│   ├── config.py            # All paths (env-var configurable)
│   ├── db.py                # SQLite schema + CRUD
│   ├── content_types.py     # URL classification
│   ├── signal_listener.py   # Stage 1a: Signal input
│   ├── imessage_listener.py # Stage 1b: iMessage input
│   ├── ingest_poller.py     # Stage 1c: Cloudflare D1 queue input
│   ├── obsidian_scanner.py  # Stage 1d: Obsidian vault input
│   ├── processor.py         # Stage 2: download + transcribe
│   ├── summarizer.py        # Stage 3: LLM summarization + Obsidian notes
│   ├── archiver.py          # Stage 4: R2 upload + share URLs
│   ├── cleanup_media.py     # Maintenance: purge local media after archival
│   ├── status.py            # Dashboard and health check
│   └── add_link.py          # CLI: manually queue a URL
├── src/mcp_knowledge/
│   ├── config.py            # Server name, paths, search tuning
│   ├── knowledge.py         # Search engine, document loading
│   └── server.py            # FastMCP tool/resource definitions
├── knowledge/               # Curated markdown documents
├── config/
│   ├── launchd/             # macOS LaunchAgent plists
│   └── systemd/             # Linux systemd units
└── tests/
```

### Platform Notes

| Feature | macOS | Linux |
|---------|-------|-------|
| Image conversion | `sips` (built-in) | ImageMagick (`apt install imagemagick`) |
| Secrets | macOS Keychain | Environment variables |
| Scheduling | launchd | systemd |

---

## Running Manually

```bash
cd ~/Developer/second-brain/crows-nest
source .venv/bin/activate

python pipeline/status.py                         # dashboard
python pipeline/add_link.py "https://..."         # queue a URL
python pipeline/processor.py --limit 5           # process up to 5 items
python pipeline/processor.py --drain             # process all pending
python pipeline/summarizer.py --limit 3          # summarize up to 3
python pipeline/archiver.py                      # archive summarized items
python pipeline/cleanup_media.py --days 30 --dry-run  # preview cleanup
```

---

## Tests

```bash
pytest tests/
```
