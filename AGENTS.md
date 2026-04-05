# Crow's Nest

Two systems in one repo: an **MCP knowledge server** and a **Signal-to-Obsidian content preservation pipeline**.

## Pipeline

4-stage automated pipeline that captures URLs shared via Signal, processes them, and creates structured Obsidian notes.

### Stages

1. **Listener** (`pipeline/signal_listener.py`) — polls signal-cli every 5 min, extracts URLs and image batches, saves to SQLite
2. **Processor** (`pipeline/processor.py`) — downloads media, transcribes audio/video with Whisper, scrapes web pages
3. **Summarizer** (`pipeline/summarizer.py`) — calls Claude Haiku via OpenRouter for structured analysis, writes Obsidian notes to `2 - AREAS/INTERNET CLIPPINGS/`
4. **Archiver** (`pipeline/archiver.py`) — uploads individual media files to R2 with Content-Type headers for inline playback, generates share URLs via `share.bymarkriechers.com`, writes them to DB and Obsidian note. Web pages are saved to Readwise Reader instead.

### Key files

- `pipeline/config.py` — all paths derived from env vars (`CROWS_NEST_HOME`, `OBSIDIAN_VAULT`, `MEDIA_ROOT`). Defaults to macOS dev paths; override for Proxmox/Linux.
- `pipeline/db.py` — SQLite status machine (pending → downloading → transcribed → summarized → archived)
- `pipeline/status.py` — dashboard (`python status.py`) and health check (`python status.py --health`)
- `pipeline/add_link.py` — CLI to manually queue URLs
- `pipeline/keychain_secrets.py` — macOS Keychain with env var fallback for API keys
- `pipeline/sync_clippings.py` — reusable tool to sync Obsidian clippings with DB and current spec (idempotent, rule-based normalization)
- `pipeline/backfill_video.py` — download video for items that only have audio

### Running manually

```bash
cd ~/Developer/second-brain/crows-nest
.venv/bin/python pipeline/status.py           # dashboard
.venv/bin/python pipeline/add_link.py "URL"   # queue a URL
.venv/bin/python pipeline/processor.py        # process pending
.venv/bin/python pipeline/summarizer.py       # summarize transcribed
```

### Scheduling

- **macOS**: launchd plists in `config/launchd/` (installed to `~/Library/LaunchAgents/`)
- **Linux**: systemd timer/service units in `config/systemd/` (install to `/etc/systemd/system/`)

### Platform portability

The pipeline runs on macOS and Linux. Platform-specific behavior:
- Image processing: uses `sips` (macOS) or ImageMagick (Linux) — auto-detected
- Secrets: macOS Keychain with env var fallback — on Linux, just set env vars
- All paths configurable via env vars in `config.py`

### R2 Archival Credentials

Store in macOS Keychain:
```bash
security add-generic-password -a "$USER" -s "developer.workspace.R2_ACCESS_KEY_ID" -w "your-key" -U
security add-generic-password -a "$USER" -s "developer.workspace.R2_SECRET_ACCESS_KEY" -w "your-secret" -U
security add-generic-password -a "$USER" -s "developer.workspace.R2_ENDPOINT_URL" -w "https://<account-id>.r2.cloudflarestorage.com" -U
```

Or set environment variables with the same names.

## MCP Knowledge Server

Domain-specific knowledge server (`src/mcp_knowledge/`) with keyword search over curated docs and semantic search over media archive transcripts.

### Keyword Search Tools

- `search_knowledge(query, category?, max_results=5, full_document=False)` — keyword search with excerpts
- `list_topics()` — categories with document counts
- `get_document(path)` — fetch full document by path
- `get_server_info()` — server metadata

### Semantic Search Tools (requires `pip install -e ".[semantic]"`)

- `semantic_search(query, n_results=10, platform?)` — search media archive transcripts via natural language
- `reindex_media()` — rebuild semantic index from media archive
- `media_status()` — semantic index health: document count, collection info

### Knowledge Structure

```
knowledge/
├── sources.json
├── category-name/
│   ├── document.md       # Searchable (indexed)
│   ├── document.html     # Raw source (not indexed)
│   └── document.json     # Metadata
```

To add knowledge: add to `sources.json` and run `python scripts/crawl_docs.py`, or manually create `.md` files.

### Media Archive

Pipeline output stored in `media/` (gitignored). Structure:
```
media/
  YYYY-MM/
    item-title/
      metadata.json     # Rich metadata (title, creator, platform, url, etc.)
      item-title.txt    # Whisper transcript
      item-title.mp4    # Video file (when available)
      item-title.m4a    # Audio file
```

### HTTP API

Optional localhost HTTP API. Enable via `CROWS_NEST_HTTP_API=true` env var.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search` | POST | Combined semantic + keyword search (`{query, n_results?, platform?}`) |
| `/status` | GET | Index health dashboard |
| `/reindex` | POST | Trigger media archive reindex |
| `/health` | GET | Liveness check |

Default port: 27185. Override: `CROWS_NEST_HTTP_PORT`.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"              # base + tests
pip install -e ".[semantic]"         # + ChromaDB, fastembed
pip install -e ".[archive]"          # + boto3 for R2
pip install -e ".[all]"              # everything
pytest tests/
```
