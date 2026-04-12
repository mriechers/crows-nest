# Crow's Nest

Two systems in one repo: an **MCP knowledge server** and a **URL-to-Obsidian content preservation pipeline**.

## Pipeline

4-stage automated pipeline that captures URLs from multiple sources, processes them, and creates structured Obsidian notes.

### Input Sources

| Source | Script | Mechanism |
|--------|--------|-----------|
| Cloudflare ingest queue | `pipeline/ingest_poller.py` | Drains D1 queue populated by Cloudflare Worker, marks items synced |
| Obsidian vault | `pipeline/obsidian_scanner.py` | Scans for notes tagged `pending-clippings`, extracts URLs |
| CLI | `pipeline/add_link.py` | Manually queue a URL |

### Stages

1. **Intake** — Input scripts save URLs to SQLite with `status='pending'`
2. **Processor** (`pipeline/processor.py`) — Routes by content type: yt-dlp download, ffmpeg audio extraction, Whisper transcription, image conversion/resize, thumbnail extraction. Args: `--limit N` (default 20), `--drain` (loop until empty), `--db PATH`
3. **Summarizer** (`pipeline/summarizer.py`) — Calls Claude Haiku via OpenRouter, writes Obsidian notes to `2 - AREAS/INTERNET CLIPPINGS/` with YAML frontmatter. Args: `--limit N` (default 5), `--drain`, `--db PATH`
4. **Archiver** (`pipeline/archiver.py`) — Uploads media to R2 (`crows-nest-media-archive` bucket), generates share URLs via `share.bymarkriechers.com`, updates DB and Obsidian note. Web pages go to Readwise Reader instead. Args: `--db PATH`

### Key Files

- `pipeline/config.py` — all paths derived from env vars (`CROWS_NEST_HOME`, `OBSIDIAN_VAULT`, `MEDIA_ROOT`, `CROWS_NEST_INGEST_API_URL`). Defaults to macOS dev paths; override for Linux.
- `pipeline/db.py` — SQLite schema (links + processing_log + feeds + articles) and CRUD helpers
- `pipeline/content_types.py` — URL classification logic
- `pipeline/status.py` — dashboard (`python status.py`) and health check (`python status.py --health`)
- `pipeline/add_link.py` — CLI to manually queue URLs
- `pipeline/keychain_secrets.py` — macOS Keychain with env var fallback for API keys
- `pipeline/cleanup_media.py` — removes local media for archived items older than N days; preserves DB, Obsidian notes, vault archive images. Args: `--days N`, `--dry-run`, `--db PATH`
- `pipeline/sync_clippings.py` — idempotent sync of Obsidian clippings with DB and current spec
- `pipeline/backfill_video.py` — re-download video for items that only have audio
- `pipeline/rss_listener.py` — polls RSS feeds, scores articles by tier/recency/keywords, stores ephemerally in SQLite for briefing use (not the full pipeline)
- `pipeline/utils.py` — shared helpers (`setup_logging`, `sanitize_title`, `extract_urls`, `media_dir_for`)

### Status Machine

```
pending -> downloading -> transcribed -> summarized -> archived
```

Each stage claims work atomically and updates status on completion. Failed items record errors and increment `retry_count`.

### Running Manually

```bash
cd ~/Developer/second-brain/crows-nest
source .venv/bin/activate

python pipeline/status.py                              # dashboard
python pipeline/add_link.py "https://..."              # queue a URL
python pipeline/processor.py --limit 5                 # process up to 5
python pipeline/processor.py --drain                   # process all pending
python pipeline/summarizer.py --limit 3                # summarize up to 3
python pipeline/archiver.py                            # archive summarized
python pipeline/cleanup_media.py --days 30 --dry-run   # preview cleanup
```

### Scheduling

- **macOS**: launchd plists in `config/launchd/` (install to `~/Library/LaunchAgents/`). Plists exist for: ingest-poller, processor, summarizer, archiver, rss-refresh, obsidian-scanner.
- **Linux**: systemd timer/service units in `config/systemd/` for processor, summarizer, and archiver.

### Platform Portability

The pipeline runs on macOS and Linux. Platform-specific behavior:
- Image processing: uses `sips` (macOS) or ImageMagick (Linux) — auto-detected in `config.py`
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
      thumbnail.jpg     # Extracted thumbnail (video frame, og_image, or first image)
```

---

## MCP Knowledge Server

Domain-specific knowledge server (`src/mcp_knowledge/`) with keyword search over curated docs and RSS feed tools.

### Knowledge Tools

- `search_knowledge(query, category?, max_results=5, full_document=False)` — keyword search with excerpts
- `list_topics()` — categories with document counts
- `get_document(path)` — fetch full document by path
- `get_server_info()` — server name, description, doc count, categories, last refreshed

### RSS Tools (requires `.[rss]` extras)

- `list_recent_articles(limit=8, max_age_hours=48)` — top-scored unsurfaced articles from the ephemeral cache
- `search_articles(query, max_results=10)` — keyword search across article titles and summaries
- `mark_surfaced(article_ids)` — mark articles seen, exclude from future queries
- `manage_feeds(action, url?, title?, tier?)` — list, add, or get stats for feeds

### Knowledge Structure

```
knowledge/
├── sources.json
├── category-name/
│   ├── document.md       # Searchable (indexed)
│   ├── document.html     # Raw source (not indexed)
│   └── document.json     # Metadata
```

To add knowledge: add to `sources.json` and run `python scripts/crawl_docs.py`, or create `.md` files directly.

---

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # base + tests
pip install -e ".[archive]"  # + boto3 for R2
pip install -e ".[rss]"      # + feedparser for RSS
pip install -e ".[all]"      # everything
pytest tests/
```

### Commits

Follow the-lodge commit conventions: `feat/fix/refactor/chore/test/docs` prefix, `Agent:` trailer, `Co-Authored-By:` footer. Reference GitHub Issues with `Fixes #N` or `Ref #N`.
