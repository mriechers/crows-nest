# Crow's Nest

Two systems in one repo: an **MCP knowledge server** and a **Signal-to-Obsidian content preservation pipeline**.

## Pipeline

4-stage automated pipeline that captures URLs shared via Signal, processes them, and creates structured Obsidian notes.

### Stages

1. **Listener** (`pipeline/signal_listener.py`) — polls signal-cli every 5 min, extracts URLs and image batches, saves to SQLite
2. **Processor** (`pipeline/processor.py`) — downloads media, transcribes audio/video with Whisper, scrapes web pages
3. **Summarizer** (`pipeline/summarizer.py`) — calls Claude Haiku via OpenRouter for structured analysis, writes Obsidian notes to `0 - INBOX/Clippings/`
4. **Archiver** (`pipeline/archiver.py`) — tar.gz + SHA-256 manifest → Cloudflare R2 (`crows-nest-media-archive` bucket)

### Key files

- `pipeline/config.py` — all paths derived from env vars (`CROWS_NEST_HOME`, `OBSIDIAN_VAULT`, `MEDIA_ROOT`). Defaults to macOS dev paths; override for Proxmox/Linux.
- `pipeline/db.py` — SQLite status machine (pending → downloading → transcribed → summarized → archived)
- `pipeline/status.py` — dashboard (`python status.py`) and health check (`python status.py --health`)
- `pipeline/add_link.py` — CLI to manually queue URLs
- `pipeline/signal_doctor.py` — diagnostic CLI for signal-cli auth/setup issues (`python signal_doctor.py`)
- `pipeline/keychain_secrets.py` — macOS Keychain with env var fallback for API keys
- `pipeline/fix_obsidian_names.py` — fixes Obsidian filenames with banned characters and updates weekly log wikilinks to match (dry run by default, pass `--apply` to write changes)

### Running manually

```bash
cd ~/Developer/second-brain/crows-nest
.venv/bin/python pipeline/status.py           # dashboard
.venv/bin/python pipeline/add_link.py "URL"   # queue a URL
.venv/bin/python pipeline/processor.py        # process pending
.venv/bin/python pipeline/summarizer.py       # summarize transcribed
.venv/bin/python pipeline/fix_obsidian_names.py          # dry run: check for bad filenames/wikilinks
.venv/bin/python pipeline/fix_obsidian_names.py --apply  # fix bad filenames/wikilinks
```

### Scheduling

- **macOS**: launchd plists in `config/launchd/` (installed to `~/Library/LaunchAgents/`)
- **Linux**: systemd timer/service units in `config/systemd/` (install to `/etc/systemd/system/`)

### Signal listener health & recovery

The listener writes a structured health file at `logs/signal-health.json`
after every poll. `pipeline/status.py --health` surfaces three states:

- **ok** — last poll reached signal-cli successfully
- **error** — single transient failure (timeout, subprocess error, ...)
- **degraded** — 3+ consecutive failures; needs user intervention

The health file also tracks `last_success_at` and `consecutive_failures`
so `status.py --health` can report "last healthy Nh Mm ago" even while
the listener is in a failure state.

When Signal ingestion stops working:

1. **Diagnose**: `python pipeline/signal_doctor.py`
   Runs four checks — SIGNAL_USER configured, signal-cli binary on PATH,
   signal-cli data directory exists, and a live `receive --timeout 1` —
   and prints exact recovery commands for each failing check.
2. **Re-link** (most common fix): when signal-cli reports "not registered",
   your linked device has expired. Re-link with:
   ```bash
   signal-cli -a "+16085551234" link -n "crows-nest"
   ```
   then scan the QR code from the Signal mobile app under
   *Settings → Linked Devices → Link New Device*.
3. **Fallback capture** while Signal is broken: use the HTTP
   `/add-link` endpoint (see below) or `python pipeline/add_link.py URL`.

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
| `/add-link` | POST | Queue a URL for the pipeline (token-authed) |

Default port: 27185. Override: `CROWS_NEST_HTTP_PORT`.

#### /add-link — Signal-independent URL ingestion

`POST /add-link` queues a URL into the same pending pipeline as the Signal
listener. It is the foundation for iOS/macOS Shortcuts, bookmarklets,
browser extensions, and any other tool that can make an HTTP call — useful
whenever the Signal listener is down for auth reasons.

**Enable it** by setting a shared-secret token (required):

```bash
export CROWS_NEST_HTTP_API=true
export CROWS_NEST_API_TOKEN="some-long-random-string"
```

Without `CROWS_NEST_API_TOKEN` the endpoint returns 503. With it, the
endpoint requires that exact token in the `X-Crows-Nest-Token` header.

**Request body** (JSON):

```json
{
  "url": "https://example.com/article",
  "context": "optional note about why this was saved",
  "source_type": "http"
}
```

`source_type` is optional and defaults to `"http"` — override it to tag
where the request came from (e.g. `"shortcut"`, `"bookmarklet"`).

**Responses:**
- `201 {id, status:"queued", content_type, source_type}` — queued successfully
- `400` — missing or non-string `url`, or invalid JSON body
- `401` — missing or wrong token
- `409` — URL already queued (same deduplication as Signal listener)
- `503` — endpoint disabled (token not set)

**Example** — queue a URL from the command line:

```bash
curl -X POST http://127.0.0.1:27185/add-link \
  -H "X-Crows-Nest-Token: $CROWS_NEST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","context":"manual curl"}'
```

For remote access (pipeline on Proxmox), front it with Tailscale and
point the client at the Tailscale IP; the endpoint binds to
`CROWS_NEST_HTTP_HOST` (default `127.0.0.1`).

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
