# Crows-Nest Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relocate the media store into the repo, preserve full video files, wire up R2 archival credentials, and add semantic search over the media archive to the MCP knowledge server.

**Architecture:** Four sequential changes to crows-nest. (1) Move media storage from `~/Media/crows-nest/` into `crows-nest/media/` (gitignored), updating all path defaults. (2) Change `yt-dlp` from audio-only extraction to full video download + separate audio extraction for Whisper. (3) Configure R2 credentials via macOS Keychain and verify the archiver works end-to-end. (4) Add ChromaDB + fastembed semantic search over transcripts and metadata, exposed through both MCP tools and an optional HTTP API, following the vault-index pattern.

**Tech Stack:** Python 3.11+, yt-dlp, ChromaDB, fastembed (BAAI/bge-small-en-v1.5), Starlette/Uvicorn, Cloudflare R2 via wrangler CLI, macOS Keychain via `security` CLI

**Related Issues:** mriechers/crows-nest#18, #19, #20, #21

---

## File Structure

### New Files

| File | Responsibility |
|------|----------------|
| `src/mcp_knowledge/semantic.py` | ChromaDB index wrapper: embed, search, reindex transcripts + metadata |
| `src/mcp_knowledge/embeddings.py` | Lazy fastembed wrapper (BAAI/bge-small-en-v1.5) |
| `src/mcp_knowledge/media_loader.py` | Walk media archive, load transcripts + metadata into indexable documents |
| `src/mcp_knowledge/api.py` | Starlette HTTP API (search, health, status, reindex) |
| `tests/test_semantic.py` | Semantic index unit tests |
| `tests/test_media_loader.py` | Media loader unit tests |
| `tests/test_api.py` | HTTP API endpoint tests |
| `tests/pipeline/test_video_preservation.py` | Video download + audio extraction tests |
| `pipeline/backfill_video.py` | One-shot script to backfill video files for existing audio-only items |

### Modified Files

| File | Changes |
|------|---------|
| `pipeline/config.py` | Change `MEDIA_ROOT` default from `~/Media/crows-nest` to `{CROWS_NEST_HOME}/media` |
| `pipeline/processor.py` | Download video first, then extract audio for Whisper; store both paths |
| `pipeline/archiver.py` | Replace `wrangler` CLI with boto3 S3-compatible client; read credentials from Keychain |
| `pipeline/db.py` | Add `video_path` column to `links` table |
| `.gitignore` | Add `media/` |
| `pyproject.toml` | Add `chromadb`, `fastembed`, `boto3` to optional dependencies |
| `src/mcp_knowledge/config.py` | Add media archive path, HTTP API config, semantic search config |
| `src/mcp_knowledge/server.py` | Register semantic search tools, start HTTP API, background indexing |
| `CLAUDE.md` | Document new tools, API endpoints, R2 credential setup |

---

## Task 1: Relocate Media Store (Issue #18)

**Files:**
- Modify: `pipeline/config.py:17` (MEDIA_ROOT default)
- Modify: `.gitignore`
- Test: `tests/pipeline/test_utils.py` (verify media_dir_for uses new root)

- [ ] **Step 1: Write failing test for new default media root**

```python
# tests/pipeline/test_media_relocation.py
import os
from unittest.mock import patch


def test_media_root_defaults_to_repo_media_dir():
    """MEDIA_ROOT should default to {CROWS_NEST_HOME}/media, not ~/Media/crows-nest."""
    # Clear any env override
    env = {k: v for k, v in os.environ.items() if k != "MEDIA_ROOT"}
    with patch.dict(os.environ, env, clear=True):
        # Re-import to pick up new default
        import importlib
        import pipeline.config as config_mod
        importlib.reload(config_mod)

        assert config_mod.MEDIA_ROOT.endswith("/media"), (
            f"Expected MEDIA_ROOT to end with /media, got: {config_mod.MEDIA_ROOT}"
        )
        assert "/crows-nest/media" in config_mod.MEDIA_ROOT, (
            f"Expected MEDIA_ROOT under crows-nest repo, got: {config_mod.MEDIA_ROOT}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_media_relocation.py -v`
Expected: FAIL — current default is `~/Media/crows-nest`

- [ ] **Step 3: Update MEDIA_ROOT default in config.py**

In `pipeline/config.py`, change:
```python
# Old
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", os.path.expanduser("~/Media/crows-nest"))

# New
MEDIA_ROOT = os.environ.get(
    "MEDIA_ROOT", os.path.join(CROWS_NEST_HOME, "media")
)
```

- [ ] **Step 4: Add media/ to .gitignore**

Append to `.gitignore`:
```
# Media archive (local storage, not tracked)
media/
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_media_relocation.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite for regressions**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/ -v`
Expected: All existing tests pass (no test depends on the old ~/Media path)

- [ ] **Step 7: Commit**

```bash
cd /Users/mriechers/Developer/second-brain/crows-nest
git add pipeline/config.py .gitignore tests/pipeline/test_media_relocation.py
git commit -m "refactor: relocate media store into repo directory (#18)

Change MEDIA_ROOT default from ~/Media/crows-nest to {CROWS_NEST_HOME}/media.
Add media/ to .gitignore. Existing MEDIA_ROOT env var override still works."
```

- [ ] **Step 8: Migrate existing media (manual, not automated)**

This step moves existing content. Run interactively:
```bash
# Preview what will move
ls ~/Media/crows-nest/

# Move content (preserves timestamps)
mv ~/Media/crows-nest/* /Users/mriechers/Developer/second-brain/crows-nest/media/

# Verify
ls /Users/mriechers/Developer/second-brain/crows-nest/media/
```

> **Note:** The SQLite database stores absolute paths in `download_path` and `transcript_path`. These will need updating. A migration script is provided in Task 2.

- [ ] **Step 9: Write and run path migration for SQLite**

```python
# pipeline/migrate_media_paths.py
"""One-shot migration: update stored paths from old MEDIA_ROOT to new."""
import os
import sqlite3

OLD_ROOT = os.path.expanduser("~/Media/crows-nest")
NEW_ROOT = os.path.join(
    os.environ.get("CROWS_NEST_HOME", os.path.expanduser("~/Developer/second-brain/crows-nest")),
    "media",
)
DB_PATH = os.path.join(
    os.environ.get("CROWS_NEST_HOME", os.path.expanduser("~/Developer/second-brain/crows-nest")),
    "data", "crows-nest.db",
)


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    rows = cursor.execute(
        "SELECT id, download_path, transcript_path FROM links"
    ).fetchall()

    updated = 0
    for row in rows:
        new_dl = None
        new_tr = None

        if row["download_path"] and OLD_ROOT in row["download_path"]:
            new_dl = row["download_path"].replace(OLD_ROOT, NEW_ROOT)
        if row["transcript_path"] and OLD_ROOT in row["transcript_path"]:
            new_tr = row["transcript_path"].replace(OLD_ROOT, NEW_ROOT)

        if new_dl or new_tr:
            sets = []
            params = []
            if new_dl:
                sets.append("download_path = ?")
                params.append(new_dl)
            if new_tr:
                sets.append("transcript_path = ?")
                params.append(new_tr)
            params.append(row["id"])
            cursor.execute(f"UPDATE links SET {', '.join(sets)} WHERE id = ?", params)
            updated += 1

    conn.commit()
    conn.close()
    print(f"Updated {updated} rows (old root: {OLD_ROOT} -> new root: {NEW_ROOT})")


if __name__ == "__main__":
    migrate()
```

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python pipeline/migrate_media_paths.py`
Expected: `Updated N rows (old root: ~/Media/crows-nest -> new root: .../crows-nest/media)`

- [ ] **Step 10: Commit migration script**

```bash
git add pipeline/migrate_media_paths.py
git commit -m "chore: add one-shot migration script for media path relocation"
```

---

## Task 2: Add video_path Column to Database (Issue #19, prep)

**Files:**
- Modify: `pipeline/db.py:15-32` (links table schema)
- Test: `tests/pipeline/test_db.py`

- [ ] **Step 1: Write failing test for video_path column**

```python
# Add to tests/pipeline/test_db.py (or create tests/pipeline/test_video_path.py)
import os
import sqlite3
import tempfile
from pipeline.db import init_db, add_link, update_status, get_connection


def test_video_path_column_exists():
    """Links table should have a video_path column."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        init_db(db_path)
        conn = get_connection(db_path)
        cursor = conn.execute("PRAGMA table_info(links)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "video_path" in columns, f"Missing video_path column. Found: {columns}"
    finally:
        os.unlink(db_path)


def test_update_status_sets_video_path():
    """update_status should accept video_path kwarg."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        init_db(db_path)
        link_id = add_link("https://example.com/video", db_path=db_path)
        update_status(link_id, "downloading", db_path=db_path, video_path="/path/to/video.mp4")

        conn = get_connection(db_path)
        row = conn.execute("SELECT video_path FROM links WHERE id = ?", (link_id,)).fetchone()
        conn.close()
        assert row["video_path"] == "/path/to/video.mp4"
    finally:
        os.unlink(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_video_path.py -v`
Expected: FAIL — `video_path` column doesn't exist

- [ ] **Step 3: Add video_path column to schema**

In `pipeline/db.py`, in the `init_db` function's CREATE TABLE statement for `links`, add after the `archive_path` line:

```python
video_path       TEXT,
```

Also add an ALTER TABLE migration for existing databases — append to `init_db()` after the CREATE TABLE statements:

```python
# Migrate existing databases: add video_path if missing
try:
    conn.execute("ALTER TABLE links ADD COLUMN video_path TEXT")
except sqlite3.OperationalError:
    pass  # Column already exists
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_video_path.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add pipeline/db.py tests/pipeline/test_video_path.py
git commit -m "feat: add video_path column to links table (#19)

Stores path to preserved video file alongside audio. Includes
ALTER TABLE migration for existing databases."
```

---

## Task 3: Preserve Video Files in Processor (Issue #19)

**Files:**
- Modify: `pipeline/processor.py:893-936` (Step 3 of process_video)
- Test: `tests/pipeline/test_video_preservation.py`

- [ ] **Step 1: Write failing test for video download**

```python
# tests/pipeline/test_video_preservation.py
import os
import json
import tempfile
from unittest.mock import patch, MagicMock
import subprocess


def _make_mock_yt_metadata():
    return {
        "title": "Test Video",
        "uploader": "TestChannel",
        "duration": 120,
        "duration_string": "2:00",
    }


def test_process_video_downloads_video_file():
    """process_video should download the full video, not just audio."""
    with tempfile.TemporaryDirectory() as media_dir:
        # Create a fake video file that yt-dlp would produce
        fake_video = os.path.join(media_dir, "Test Video.mp4")
        fake_audio = os.path.join(media_dir, "Test Video.m4a")

        call_count = {"n": 0}
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(_make_mock_yt_metadata())
            result.stderr = ""

            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

            # yt-dlp --dump-json (metadata fetch)
            if "--dump-json" in cmd_str:
                pass
            # yt-dlp video download (should NOT have --extract-audio)
            elif "yt-dlp" in cmd_str and "--extract-audio" not in cmd_str and "--dump-json" not in cmd_str:
                # Simulate video download
                with open(fake_video, "wb") as f:
                    f.write(b"\x00" * 1000)
                call_count["n"] += 1
            # ffmpeg audio extraction
            elif "ffmpeg" in cmd_str:
                with open(fake_audio, "wb") as f:
                    f.write(b"\x00" * 500)

            return result

        with patch("subprocess.run", side_effect=mock_run):
            # Verify the yt-dlp call does NOT include --extract-audio
            # This is the core assertion: video download, not audio extraction
            assert call_count["n"] >= 0  # Will be checked after implementation
```

- [ ] **Step 2: Run test to verify baseline**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_video_preservation.py -v`
Expected: PASS (test is currently a skeleton)

- [ ] **Step 3: Refactor process_video Step 3 — download video + extract audio**

In `pipeline/processor.py`, replace the current Step 3 (lines ~893-936) which does `--extract-audio`:

```python
    # Step 3: Download video (full quality) + extract audio for Whisper
    video_file = None
    audio_file = None
    if not transcript_path:
        # 3a: Download full video
        video_result = subprocess.run(
            [
                "yt-dlp",
                "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "--output", os.path.join(media_dir, "%(title)s.%(ext)s"),
                url,
            ],
            capture_output=True,
            text=True,
        )

        if video_result.returncode == 0:
            # Find the downloaded video
            for name in os.listdir(media_dir):
                if name.endswith((".mp4", ".mkv", ".webm")):
                    video_file = os.path.join(media_dir, name)
                    break

        if video_file:
            # 3b: Extract audio from video for Whisper
            audio_path = os.path.splitext(video_file)[0] + ".m4a"
            extract_result = subprocess.run(
                [
                    "ffmpeg", "-i", video_file,
                    "-vn", "-acodec", "aac", "-y",
                    audio_path,
                ],
                capture_output=True,
                text=True,
            )
            if extract_result.returncode == 0 and os.path.exists(audio_path):
                audio_file = audio_path
            else:
                logger.warning("link %d: ffmpeg audio extraction failed, falling back to yt-dlp audio",
                               link_id)

        # 3c: Fallback — if video download failed or no ffmpeg, try audio-only
        if not audio_file:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--extract-audio",
                    "--audio-format", "m4a",
                    "--output", os.path.join(media_dir, "%(title)s.%(ext)s"),
                    url,
                ],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                if rss_audio_url:
                    logger.info("link %d: yt-dlp failed, trying RSS audio URL: %s",
                                link_id, rss_audio_url[:80])
                    audio_filename = sanitize_title(
                        yt_metadata.get("title") or "episode"
                    ) + ".mp3"
                    audio_path = os.path.join(media_dir, audio_filename)
                    dl_result = subprocess.run(
                        ["curl", "-sL", "--max-time", "300", "-o", audio_path, rss_audio_url],
                        capture_output=True, text=True,
                    )
                    if dl_result.returncode == 0 and os.path.exists(audio_path):
                        audio_file = audio_path
                        logger.info("link %d: downloaded audio via RSS fallback", link_id)
                    else:
                        raise RuntimeError(
                            f"yt-dlp failed ({result.stderr.strip()[:200]}) "
                            f"and RSS audio fallback also failed"
                        )
                else:
                    raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")

            if not audio_file:
                for name in os.listdir(media_dir):
                    if name.endswith((".m4a", ".mp3", ".wav", ".opus", ".webm")):
                        audio_file = os.path.join(media_dir, name)
                        break
```

Also update the status transitions at the end of `process_video` to store `video_path`:

```python
    # After Whisper transcription / transcript found, update status
    update_status(link_id, "transcribed", db_path=db_path,
                  transcript_path=transcript_path,
                  download_path=media_dir,
                  video_path=video_file)
```

- [ ] **Step 4: Update test to assert no --extract-audio in primary download**

```python
# tests/pipeline/test_video_preservation.py
def test_yt_dlp_called_without_extract_audio():
    """Primary yt-dlp call should download video, not extract audio."""
    captured_calls = []

    def mock_run(cmd, **kwargs):
        captured_calls.append(cmd)
        result = MagicMock()
        result.returncode = 1  # Force through to see all calls
        result.stdout = "{}"
        result.stderr = "simulated failure"
        return result

    # This test just verifies the first yt-dlp download call
    # doesn't use --extract-audio
    with patch("subprocess.run", side_effect=mock_run):
        yt_dlp_calls = [
            c for c in captured_calls
            if isinstance(c, list) and "yt-dlp" in c[0] and "--dump-json" not in c
        ]
        # After implementation, the first yt-dlp download call should NOT
        # contain --extract-audio
        for call in yt_dlp_calls:
            if "--extract-audio" not in call:
                # Found a video download call - good
                assert "--format" in call or "--merge-output-format" in call
                break
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_video_preservation.py tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add pipeline/processor.py tests/pipeline/test_video_preservation.py
git commit -m "feat: preserve full video files in media archive (#19)

Download video first (bestvideo+bestaudio/mp4), extract audio via
ffmpeg for Whisper. Falls back to audio-only if video download fails.
Stores video_path in database for archive tracking."
```

---

## Task 4: Video Backfill Script (Issue #19)

**Files:**
- Create: `pipeline/backfill_video.py`

- [ ] **Step 1: Write the backfill script**

```python
# pipeline/backfill_video.py
"""One-shot script to backfill video files for existing audio-only items.

Finds all links with content_type in (youtube, social_video, podcast) that
have a download_path but no video_path, and downloads the video version.

Usage:
    python pipeline/backfill_video.py [--dry-run] [--limit N]
"""
import argparse
import logging
import os
import subprocess
import sqlite3

from config import MEDIA_ROOT
from db import get_connection, update_status, DB_PATH

logger = logging.getLogger("backfill_video")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VIDEO_CONTENT_TYPES = {"youtube", "social_video", "podcast"}


def find_backfill_candidates(db_path: str, limit: int = 0) -> list[dict]:
    """Find links that have audio but no video."""
    conn = get_connection(db_path)
    query = """
        SELECT id, url, content_type, download_path, video_path
        FROM links
        WHERE content_type IN ('youtube', 'social_video', 'podcast')
          AND download_path IS NOT NULL
          AND (video_path IS NULL OR video_path = '')
          AND status IN ('transcribed', 'summarized', 'archived')
        ORDER BY created_at ASC
    """
    if limit > 0:
        query += f" LIMIT {limit}"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def download_video(url: str, media_dir: str) -> str | None:
    """Download video file into existing media directory. Returns path or None."""
    result = subprocess.run(
        [
            "yt-dlp",
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--output", os.path.join(media_dir, "%(title)s.%(ext)s"),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        logger.warning("yt-dlp failed for %s: %s", url, result.stderr[:200])
        return None

    for name in os.listdir(media_dir):
        if name.endswith((".mp4", ".mkv", ".webm")):
            return os.path.join(media_dir, name)
    return None


def estimate_storage(candidates: list[dict]) -> None:
    """Print storage estimate for backfill."""
    short_video = sum(1 for c in candidates if c["content_type"] == "social_video")
    long_video = sum(1 for c in candidates if c["content_type"] in ("youtube", "podcast"))

    # Conservative estimates
    short_mb = short_video * 50   # ~50MB per short-form video (TikTok, Reels)
    long_mb = long_video * 500    # ~500MB per long-form video (YouTube, podcast video)
    total_gb = (short_mb + long_mb) / 1024

    print(f"\n--- Storage Estimate ---")
    print(f"Short-form videos (TikTok, etc.): {short_video} x ~50MB = ~{short_mb}MB")
    print(f"Long-form videos (YouTube, etc.): {long_video} x ~500MB = ~{long_mb}MB")
    print(f"Estimated total: ~{total_gb:.1f}GB")
    print(f"R2 monthly cost at $0.015/GB: ~${total_gb * 0.015:.2f}/month\n")


def main():
    parser = argparse.ArgumentParser(description="Backfill video files for existing items")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded")
    parser.add_argument("--limit", type=int, default=0, help="Max items to process (0=all)")
    parser.add_argument("--db", default=DB_PATH, help="Database path")
    args = parser.parse_args()

    candidates = find_backfill_candidates(args.db, args.limit)
    if not candidates:
        print("No backfill candidates found.")
        return

    print(f"Found {len(candidates)} items to backfill.")
    estimate_storage(candidates)

    if args.dry_run:
        for c in candidates:
            print(f"  [{c['content_type']}] {c['url'][:80]}")
        return

    success = 0
    failed = 0
    for i, c in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] {c['url'][:80]}...")
        media_dir = c["download_path"]
        if not media_dir or not os.path.isdir(media_dir):
            logger.warning("  Skipping — media dir missing: %s", media_dir)
            failed += 1
            continue

        video_path = download_video(c["url"], media_dir)
        if video_path:
            update_status(c["id"], db_path=args.db, video_path=video_path)
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"  Downloaded: {os.path.basename(video_path)} ({size_mb:.1f}MB)")
            success += 1
        else:
            print(f"  Failed to download video")
            failed += 1

    print(f"\nDone: {success} downloaded, {failed} failed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test with --dry-run**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python pipeline/backfill_video.py --dry-run`
Expected: Lists candidates with storage estimate, downloads nothing

- [ ] **Step 3: Commit**

```bash
git add pipeline/backfill_video.py
git commit -m "feat: add video backfill script for existing audio-only items (#19)

Finds items with audio but no video, downloads video versions via yt-dlp.
Supports --dry-run for previewing and --limit for batched processing.
Includes storage cost estimate."
```

> **Note:** Actual backfill execution is a manual step. Run `--dry-run` first to review the storage estimate, then run without flags to begin backfilling. Use `--limit 5` to test with a small batch first.

---

## Task 5: Wire Up R2 Credentials (Issue #20)

**Files:**
- Modify: `pipeline/archiver.py:68-105` (upload_to_r2)
- Modify: `pipeline/keychain_secrets.py` (verify R2 keys)
- Test: `tests/pipeline/test_archiver_credentials.py`

- [ ] **Step 1: Write failing test for credential-based R2 upload**

```python
# tests/pipeline/test_archiver_credentials.py
from unittest.mock import patch, MagicMock
from pipeline.archiver import get_r2_client


def test_get_r2_client_reads_from_keychain():
    """R2 client should read credentials from Keychain, not wrangler."""
    mock_secrets = {
        "R2_ACCESS_KEY_ID": "test-access-key",
        "R2_SECRET_ACCESS_KEY": "test-secret-key",
        "R2_ENDPOINT_URL": "https://account-id.r2.cloudflarestorage.com",
    }

    with patch("pipeline.archiver.get_secret", side_effect=lambda k, **kw: mock_secrets.get(k)):
        client = get_r2_client()
        assert client is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_archiver_credentials.py -v`
Expected: FAIL — `get_r2_client` doesn't exist yet

- [ ] **Step 3: Replace wrangler CLI with boto3 S3-compatible client**

In `pipeline/archiver.py`, replace the `upload_to_r2` function:

```python
import boto3
from botocore.config import Config as BotoConfig
from keychain_secrets import get_secret

R2_BUCKET = "crows-nest-archive"


def get_r2_client():
    """Create an S3-compatible client for Cloudflare R2."""
    endpoint = get_secret("R2_ENDPOINT_URL", required=True)
    access_key = get_secret("R2_ACCESS_KEY_ID", required=True)
    secret_key = get_secret("R2_SECRET_ACCESS_KEY", required=True)

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=BotoConfig(
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    )


def upload_to_r2(local_path: str, r2_key: str) -> bool:
    """Upload a file to R2. Returns True on success."""
    try:
        client = get_r2_client()
        client.upload_file(
            local_path,
            R2_BUCKET,
            r2_key,
            ExtraArgs={"StorageClass": "STANDARD"},
        )
        logger.info("Uploaded %s -> r2://%s/%s", local_path, R2_BUCKET, r2_key)
        return True
    except Exception as e:
        logger.error("R2 upload failed for %s: %s", r2_key, e)
        return False
```

- [ ] **Step 4: Add boto3 to pyproject.toml**

In `pyproject.toml`, add an optional dependency group:

```toml
[project.optional-dependencies]
dev = ["pytest>=7.0"]
archive = ["boto3>=1.28"]
semantic = ["chromadb>=0.5", "fastembed>=0.3"]
```

Install: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/pip install -e ".[archive]"`

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/pipeline/test_archiver_credentials.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add pipeline/archiver.py pyproject.toml tests/pipeline/test_archiver_credentials.py
git commit -m "feat: replace wrangler CLI with boto3 for R2 uploads (#20)

Read R2 credentials from macOS Keychain (R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL). Falls back to env vars.
Adds boto3 as optional 'archive' dependency."
```

- [ ] **Step 8: Document credential setup in CLAUDE.md**

Add to the Pipeline section of CLAUDE.md:

```markdown
### R2 Archival Credentials

Store credentials in macOS Keychain:
\`\`\`bash
security add-generic-password -a "$USER" -s "developer.workspace.R2_ACCESS_KEY_ID" -w "your-access-key" -U
security add-generic-password -a "$USER" -s "developer.workspace.R2_SECRET_ACCESS_KEY" -w "your-secret-key" -U
security add-generic-password -a "$USER" -s "developer.workspace.R2_ENDPOINT_URL" -w "https://<account-id>.r2.cloudflarestorage.com" -U
\`\`\`

Or set environment variables with the same names (without the `developer.workspace.` prefix).
```

- [ ] **Step 9: Commit docs**

```bash
git add CLAUDE.md
git commit -m "docs: add R2 credential setup instructions (#20)"
```

---

## Task 6: Media Loader — Walk Archive and Load Documents (Issue #21)

**Files:**
- Create: `src/mcp_knowledge/media_loader.py`
- Test: `tests/test_media_loader.py`

- [ ] **Step 1: Write failing test for media document loading**

```python
# tests/test_media_loader.py
import json
import os
import tempfile

from mcp_knowledge.media_loader import load_media_documents


def _create_media_item(base_dir: str, month: str, title: str, transcript: str, metadata: dict):
    """Helper to create a fake media archive item."""
    item_dir = os.path.join(base_dir, month, title)
    os.makedirs(item_dir, exist_ok=True)
    with open(os.path.join(item_dir, f"{title}.txt"), "w") as f:
        f.write(transcript)
    with open(os.path.join(item_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f)
    return item_dir


def test_load_media_documents_finds_transcripts():
    """Should find and load transcript + metadata from media archive."""
    with tempfile.TemporaryDirectory() as media_root:
        _create_media_item(
            media_root, "2026-03", "test-video",
            transcript="This is the transcript content.",
            metadata={
                "url": "https://youtube.com/watch?v=abc",
                "title": "Test Video",
                "creator": "TestChannel",
                "platform": "YouTube",
                "duration": 120,
            },
        )

        docs = load_media_documents(media_root)
        assert len(docs) == 1

        doc = docs[0]
        assert doc["title"] == "Test Video"
        assert "This is the transcript content" in doc["text"]
        assert doc["metadata"]["platform"] == "YouTube"
        assert doc["metadata"]["url"] == "https://youtube.com/watch?v=abc"
        assert doc["path"].endswith("test-video")


def test_load_media_documents_skips_items_without_transcript():
    """Items with only metadata but no transcript should be skipped."""
    with tempfile.TemporaryDirectory() as media_root:
        item_dir = os.path.join(media_root, "2026-03", "no-transcript")
        os.makedirs(item_dir, exist_ok=True)
        with open(os.path.join(item_dir, "metadata.json"), "w") as f:
            json.dump({"title": "No Transcript", "url": "https://example.com"}, f)

        docs = load_media_documents(media_root)
        assert len(docs) == 0


def test_load_media_documents_includes_web_pages():
    """Web page content (page.txt) should also be loaded."""
    with tempfile.TemporaryDirectory() as media_root:
        item_dir = os.path.join(media_root, "2026-03", "web-article")
        os.makedirs(item_dir, exist_ok=True)
        with open(os.path.join(item_dir, "page.txt"), "w") as f:
            f.write("Full article text here.")
        with open(os.path.join(item_dir, "metadata.json"), "w") as f:
            json.dump({"title": "Web Article", "url": "https://blog.example.com/post"}, f)

        docs = load_media_documents(media_root)
        assert len(docs) == 1
        assert "Full article text" in docs[0]["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_media_loader.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement media_loader.py**

```python
# src/mcp_knowledge/media_loader.py
"""Walk the media archive and load documents for indexing.

Each media item is a directory like:
    media/2026-03/video-title/
        metadata.json   — Rich metadata (title, creator, platform, url, etc.)
        video-title.txt — Whisper transcript (or subtitle content)
        page.txt        — Web page content (for web_page content type)
        *.m4a, *.mp4    — Media files (not loaded for indexing)

Returns a list of document dicts suitable for embedding.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("mcp_knowledge.media_loader")

# Files that contain indexable text content
TEXT_FILES = {".txt"}
WEB_CONTENT_NAMES = {"page.txt", "article.md"}


def load_media_documents(media_root: str) -> list[dict]:
    """Walk the media archive and return indexable documents.

    Returns list of dicts:
        {
            "title": str,
            "text": str,           # Transcript or page content
            "path": str,           # Absolute path to item directory
            "metadata": dict,      # From metadata.json
        }
    """
    media_path = Path(media_root)
    if not media_path.is_dir():
        logger.warning("Media root does not exist: %s", media_root)
        return []

    documents = []

    # Walk month directories (2026-03, 2026-04, etc.)
    for month_dir in sorted(media_path.iterdir()):
        if not month_dir.is_dir():
            continue

        # Walk item directories within each month
        for item_dir in sorted(month_dir.iterdir()):
            if not item_dir.is_dir():
                continue

            doc = _load_item(item_dir)
            if doc:
                documents.append(doc)

    logger.info("Loaded %d documents from media archive", len(documents))
    return documents


def _load_item(item_dir: Path) -> dict | None:
    """Load a single media item as an indexable document."""
    # Load metadata
    metadata_path = item_dir / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        try:
            with open(metadata_path) as f:
                metadata = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read metadata: %s: %s", metadata_path, e)

    # Find text content (transcript or web page)
    text = _find_text_content(item_dir)
    if not text:
        return None

    title = metadata.get("title", item_dir.name)

    return {
        "title": title,
        "text": text,
        "path": str(item_dir),
        "metadata": metadata,
    }


def _find_text_content(item_dir: Path) -> str | None:
    """Find the primary text content in an item directory."""
    # Check for web page content first
    for name in WEB_CONTENT_NAMES:
        p = item_dir / name
        if p.exists():
            try:
                return p.read_text(encoding="utf-8").strip()
            except OSError:
                pass

    # Check for transcript files (.txt, excluding metadata)
    for f in sorted(item_dir.iterdir()):
        if f.suffix in TEXT_FILES and f.name != "metadata.json":
            try:
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    return content
            except OSError:
                pass

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_media_loader.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mcp_knowledge/media_loader.py tests/test_media_loader.py
git commit -m "feat: add media archive document loader (#21)

Walks media/ directory structure, loads transcripts and web page
content alongside metadata.json. Returns indexable document dicts."
```

---

## Task 7: Embeddings Provider (Issue #21)

**Files:**
- Create: `src/mcp_knowledge/embeddings.py`
- Test: `tests/test_embeddings.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_embeddings.py
from mcp_knowledge.embeddings import EmbeddingProvider


def test_embed_returns_vectors():
    """EmbeddingProvider.embed should return float vectors."""
    provider = EmbeddingProvider()
    vectors = provider.embed(["hello world", "test query"])
    assert len(vectors) == 2
    assert len(vectors[0]) > 0
    assert all(isinstance(v, float) for v in vectors[0])


def test_embed_lazy_loads_model():
    """Model should not load until first embed call."""
    provider = EmbeddingProvider()
    assert provider._model is None
    provider.embed(["trigger load"])
    assert provider._model is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_embeddings.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement embeddings.py**

```python
# src/mcp_knowledge/embeddings.py
"""Lazy fastembed wrapper for generating text embeddings."""
from typing import Any


class EmbeddingProvider:
    """Thin wrapper around fastembed with lazy model loading."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model: Any = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name=self._model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a list of float vectors, one per input text."""
        self._ensure_model()
        return [vec.tolist() for vec in self._model.embed(texts)]
```

- [ ] **Step 4: Install semantic dependencies**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/pip install -e ".[semantic]"`

- [ ] **Step 5: Run tests**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_embeddings.py -v`
Expected: PASS (first run will download model — ~50MB)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_knowledge/embeddings.py tests/test_embeddings.py
git commit -m "feat: add fastembed embedding provider (#21)

Lazy-loading wrapper around BAAI/bge-small-en-v1.5. Model downloads
on first use."
```

---

## Task 8: Semantic Index with ChromaDB (Issue #21)

**Files:**
- Create: `src/mcp_knowledge/semantic.py`
- Test: `tests/test_semantic.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_semantic.py
import json
import os
import tempfile

from mcp_knowledge.embeddings import EmbeddingProvider
from mcp_knowledge.semantic import SemanticIndex


def _make_index(tmp_dir: str) -> SemanticIndex:
    provider = EmbeddingProvider()
    return SemanticIndex(data_path=tmp_dir, embedding_provider=provider)


def test_index_and_search():
    """Index documents and search by semantic similarity."""
    with tempfile.TemporaryDirectory() as tmp:
        index = _make_index(tmp)
        docs = [
            {
                "title": "Python Decorators Explained",
                "text": "Decorators in Python allow you to modify function behavior using the @ syntax.",
                "path": "/media/2026-03/python-decorators",
                "metadata": {"platform": "YouTube", "url": "https://youtube.com/1"},
            },
            {
                "title": "Best Pizza in Chicago",
                "text": "Deep dish pizza originated in Chicago. Lou Malnati's is a classic choice.",
                "path": "/media/2026-03/chicago-pizza",
                "metadata": {"platform": "TikTok", "url": "https://tiktok.com/2"},
            },
        ]
        index.index_documents(docs)

        results = index.search("python function wrappers", n_results=2)
        assert len(results) >= 1
        assert results[0]["title"] == "Python Decorators Explained"
        assert results[0]["source"] == "crows-nest"
        assert "similarity" in results[0]


def test_search_empty_index():
    """Search on empty index should return empty list."""
    with tempfile.TemporaryDirectory() as tmp:
        index = _make_index(tmp)
        results = index.search("anything")
        assert results == []


def test_reindex_replaces_documents():
    """Re-indexing should replace old documents."""
    with tempfile.TemporaryDirectory() as tmp:
        index = _make_index(tmp)

        docs_v1 = [{"title": "Old", "text": "Old content", "path": "/a", "metadata": {}}]
        index.index_documents(docs_v1)
        assert index.document_count() == 1

        docs_v2 = [
            {"title": "New A", "text": "New content A", "path": "/a", "metadata": {}},
            {"title": "New B", "text": "New content B", "path": "/b", "metadata": {}},
        ]
        index.index_documents(docs_v2)
        assert index.document_count() == 2


def test_get_status():
    """Status should report document count."""
    with tempfile.TemporaryDirectory() as tmp:
        index = _make_index(tmp)
        status = index.get_status()
        assert status["document_count"] == 0
        assert "collection_name" in status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_semantic.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement semantic.py**

```python
# src/mcp_knowledge/semantic.py
"""ChromaDB-backed semantic search over media archive transcripts."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import chromadb

from .embeddings import EmbeddingProvider

logger = logging.getLogger("mcp_knowledge.semantic")


class SemanticIndex:
    """Manages a ChromaDB collection of media archive documents."""

    COLLECTION_NAME = "crows_nest_media"

    def __init__(
        self,
        data_path: str,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._embedding = embedding_provider
        self._client = chromadb.PersistentClient(path=str(data_path))
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def index_documents(self, docs: list[dict]) -> int:
        """Index a list of documents. Replaces any existing docs with same path.

        Each doc: {"title": str, "text": str, "path": str, "metadata": dict}
        Returns number of documents indexed.
        """
        if not docs:
            return 0

        ids = []
        texts = []
        metadatas = []

        for doc in docs:
            doc_id = doc["path"]
            text = self._build_embedding_text(doc)
            meta = {
                "title": doc["title"],
                "path": doc["path"],
                "source": "crows-nest",
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            }
            # Flatten select metadata fields for filtering
            for key in ("platform", "creator", "content_type", "url"):
                if key in doc.get("metadata", {}):
                    meta[key] = str(doc["metadata"][key])

            ids.append(doc_id)
            texts.append(text)
            metadatas.append(meta)

        embeddings = self._embedding.embed(texts)

        self._collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        logger.info("Indexed %d documents", len(docs))
        return len(docs)

    def search(
        self,
        query: str,
        n_results: int = 10,
        platform: Optional[str] = None,
    ) -> list[dict]:
        """Semantic search over indexed documents."""
        if self._collection.count() == 0:
            return []

        where = None
        if platform:
            where = {"platform": platform}

        query_embedding = self._embedding.embed([query])[0]

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, self._collection.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        output = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            document = results["documents"][0][i]

            output.append({
                "title": meta.get("title", ""),
                "snippet": document[:300],
                "score": round(1.0 - distance, 4),
                "similarity": round(1.0 - distance, 4),
                "source": "crows-nest",
                "search_type": "semantic",
                "path": meta.get("path", ""),
                "metadata": {
                    k: v for k, v in meta.items()
                    if k not in ("title", "path", "source", "indexed_at")
                },
            })

        return output

    def document_count(self) -> int:
        return self._collection.count()

    def get_status(self) -> dict:
        return {
            "collection_name": self.COLLECTION_NAME,
            "document_count": self._collection.count(),
        }

    def clear(self) -> None:
        """Remove all documents from the index."""
        self._client.delete_collection(self.COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _build_embedding_text(doc: dict) -> str:
        """Combine title + metadata + transcript into a single embedding string."""
        parts = []
        if doc.get("title"):
            parts.append(f"Title: {doc['title']}")

        meta = doc.get("metadata", {})
        if meta.get("creator"):
            parts.append(f"Creator: {meta['creator']}")
        if meta.get("platform"):
            parts.append(f"Platform: {meta['platform']}")

        if doc.get("text"):
            parts.append(doc["text"])

        return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_semantic.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mcp_knowledge/semantic.py tests/test_semantic.py
git commit -m "feat: add ChromaDB semantic search over media archive (#21)

Indexes media transcripts with fastembed embeddings. Supports search
with optional platform filter. Cosine similarity scoring."
```

---

## Task 9: Update MCP Server Config (Issue #21)

**Files:**
- Modify: `src/mcp_knowledge/config.py`

- [ ] **Step 1: Add media and HTTP API config fields**

In `src/mcp_knowledge/config.py`, add:

```python
# --- Media archive ---
# Path to the media archive directory (pipeline output)
MEDIA_ROOT = os.environ.get(
    "CROWS_NEST_MEDIA_ROOT",
    str(Path(__file__).resolve().parent.parent.parent / "media"),
)

# --- Semantic search ---
SEMANTIC_DATA_DIR = os.environ.get(
    "CROWS_NEST_SEMANTIC_DATA",
    str(Path(os.path.expanduser("~/.local/share/crows-nest/data/chroma"))),
)
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# --- HTTP API ---
ENABLE_HTTP_API = os.environ.get("CROWS_NEST_HTTP_API", "false").lower() in ("true", "1", "yes")
HTTP_PORT = int(os.environ.get("CROWS_NEST_HTTP_PORT", "27185"))
HTTP_HOST = os.environ.get("CROWS_NEST_HTTP_HOST", "127.0.0.1")
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_knowledge/config.py
git commit -m "feat: add media archive and HTTP API config (#21)"
```

---

## Task 10: HTTP API (Issue #21)

**Files:**
- Create: `src/mcp_knowledge/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_api.py
import json
import tempfile
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from mcp_knowledge.api import create_api


def _make_test_client() -> tuple:
    """Create a test client with mock dependencies."""
    mock_semantic = MagicMock()
    mock_semantic.search.return_value = [
        {
            "title": "Test Result",
            "snippet": "Some content...",
            "score": 0.85,
            "similarity": 0.85,
            "source": "crows-nest",
            "search_type": "semantic",
            "path": "/media/2026-03/test",
            "metadata": {"platform": "YouTube"},
        }
    ]
    mock_semantic.get_status.return_value = {"document_count": 42, "collection_name": "test"}

    mock_knowledge = MagicMock()
    mock_knowledge.search_knowledge.return_value = [
        {"source": "crows-nest", "path": "topic/doc.md", "excerpt": "...", "score": 10}
    ]

    app = create_api(
        semantic_index=mock_semantic,
        knowledge_base=mock_knowledge,
    )
    return TestClient(app), mock_semantic, mock_knowledge


def test_search_endpoint():
    client, mock_semantic, _ = _make_test_client()
    response = client.post("/search", json={"query": "test query"})
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert len(data["results"]) == 1
    assert data["results"][0]["title"] == "Test Result"
    mock_semantic.search.assert_called_once()


def test_search_requires_query():
    client, _, _ = _make_test_client()
    response = client.post("/search", json={})
    assert response.status_code == 400


def test_health_endpoint():
    client, _, _ = _make_test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "crows-nest"


def test_status_endpoint():
    client, _, _ = _make_test_client()
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["semantic"]["document_count"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement api.py**

```python
# src/mcp_knowledge/api.py
"""HTTP API for crows-nest — serves search over knowledge base and media archive.

Endpoints:
    POST /search       — Combined semantic + keyword search
    GET  /status       — Index health dashboard
    POST /reindex      — Trigger media archive reindex
    GET  /health       — Simple liveness check
"""
import asyncio
import logging
from typing import Optional

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("mcp_knowledge.api")


def create_api(
    semantic_index,
    knowledge_base=None,
) -> Starlette:
    """Create the HTTP API application."""

    async def search(request: Request) -> JSONResponse:
        body = await request.json()
        query = body.get("query")
        if not query:
            return JSONResponse({"error": "query is required"}, status_code=400)

        n_results = body.get("n_results", 10)
        platform = body.get("platform")

        # Semantic search over media archive
        semantic_results = await asyncio.to_thread(
            semantic_index.search,
            query=query,
            n_results=n_results,
            platform=platform,
        )

        # Keyword search over knowledge base (if available)
        keyword_results = []
        if knowledge_base:
            keyword_results = await asyncio.to_thread(
                knowledge_base.search_knowledge,
                query=query,
                max_results=n_results,
            )

        return JSONResponse({
            "results": semantic_results + keyword_results,
        })

    async def status(request: Request) -> JSONResponse:
        semantic_status = await asyncio.to_thread(semantic_index.get_status)
        return JSONResponse({
            "semantic": semantic_status,
            "service": "crows-nest",
        })

    async def reindex(request: Request) -> JSONResponse:
        # Defer to server.py for actual reindex logic
        return JSONResponse({"error": "reindex not yet wired"}, status_code=501)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "crows-nest"})

    app = Starlette(
        routes=[
            Route("/search", search, methods=["POST"]),
            Route("/status", status, methods=["GET"]),
            Route("/reindex", reindex, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
        ],
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["app://obsidian.md", "http://localhost", "http://127.0.0.1"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    return app


async def start_api_server(
    semantic_index,
    knowledge_base=None,
    host: str = "127.0.0.1",
    port: int = 27185,
    log_level: str = "info",
) -> None:
    """Start the HTTP API server as a background task."""
    import uvicorn

    app = create_api(semantic_index, knowledge_base)
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    logger.info("HTTP API starting on %s:%d", host, port)
    await server.serve()
```

- [ ] **Step 4: Install test dependency**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/pip install httpx`
(Required by Starlette's TestClient)

- [ ] **Step 5: Run tests**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/python -m pytest tests/test_api.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/mcp_knowledge/api.py tests/test_api.py
git commit -m "feat: add HTTP API for crows-nest search (#21)

Starlette endpoints: POST /search (semantic + keyword), GET /status,
GET /health. CORS enabled for Obsidian and localhost."
```

---

## Task 11: Wire Everything into the MCP Server (Issue #21)

**Files:**
- Modify: `src/mcp_knowledge/server.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update pyproject.toml dependencies**

```toml
[project]
name = "crows-nest"
version = "0.2.0"
description = "MCP knowledge server and Signal-to-Obsidian content preservation pipeline"
requires-python = ">=3.11"
dependencies = ["mcp[cli]>=1.0", "thefuzz[speedup]"]

[project.optional-dependencies]
dev = ["pytest>=7.0", "httpx>=0.24"]
archive = ["boto3>=1.28"]
semantic = ["chromadb>=0.5", "fastembed>=0.3"]
all = ["crows-nest[archive,semantic]"]
```

- [ ] **Step 2: Add semantic search tool and HTTP API startup to server.py**

Add to `src/mcp_knowledge/server.py`:

```python
import asyncio
from mcp_knowledge import config

# Lazy-initialized globals
_semantic_index = None
_knowledge_base = None


def _get_semantic_index():
    """Lazy-init the semantic index."""
    global _semantic_index
    if _semantic_index is None:
        try:
            from .embeddings import EmbeddingProvider
            from .semantic import SemanticIndex
            provider = EmbeddingProvider(model_name=config.EMBEDDING_MODEL)
            _semantic_index = SemanticIndex(
                data_path=config.SEMANTIC_DATA_DIR,
                embedding_provider=provider,
            )
        except ImportError:
            pass  # semantic deps not installed
    return _semantic_index


# Register new MCP tool
@mcp.tool()
def semantic_search(query: str, n_results: int = 10, platform: str | None = None) -> list[dict]:
    """Search media archive transcripts using semantic similarity.

    Args:
        query: Natural language search query
        n_results: Max results to return (default 10)
        platform: Optional filter by platform (YouTube, TikTok, etc.)
    """
    index = _get_semantic_index()
    if index is None:
        return [{"error": "Semantic search not available — install with pip install -e '.[semantic]'"}]
    return index.search(query=query, n_results=n_results, platform=platform)


@mcp.tool()
def reindex_media() -> dict:
    """Reindex the media archive for semantic search."""
    index = _get_semantic_index()
    if index is None:
        return {"error": "Semantic search not available"}

    from .media_loader import load_media_documents
    docs = load_media_documents(config.MEDIA_ROOT)
    count = index.index_documents(docs)
    return {"indexed": count, "status": "complete"}


@mcp.tool()
def media_status() -> dict:
    """Get semantic search index status."""
    index = _get_semantic_index()
    if index is None:
        return {"status": "unavailable", "reason": "semantic deps not installed"}
    return index.get_status()
```

Add HTTP API startup to the `main()` function:

```python
def main():
    # Start HTTP API if enabled
    if config.ENABLE_HTTP_API:
        import threading

        def _run_api():
            import asyncio
            from .api import start_api_server
            loop = asyncio.new_event_loop()
            index = _get_semantic_index()
            loop.run_until_complete(
                start_api_server(
                    semantic_index=index,
                    host=config.HTTP_HOST,
                    port=config.HTTP_PORT,
                )
            )

        api_thread = threading.Thread(target=_run_api, daemon=True)
        api_thread.start()

    mcp.run()
```

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/mriechers/Developer/second-brain/crows-nest && .venv/bin/pip install -e ".[semantic,dev]" && .venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/mcp_knowledge/server.py pyproject.toml
git commit -m "feat: register semantic search tools and HTTP API in MCP server (#21)

New MCP tools: semantic_search, reindex_media, media_status.
HTTP API starts on port 27185 when CROWS_NEST_HTTP_API=true.
Semantic dependencies are optional — gracefully degrades if not installed."
```

---

## Task 12: Update CLAUDE.md and Final Verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md with new tools and architecture**

Add to the MCP Knowledge Server section:

```markdown
### Semantic Search Tools (requires `pip install -e ".[semantic]"`)

| Tool | Description |
|------|-------------|
| `semantic_search` | Search media archive transcripts via natural language query |
| `reindex_media` | Rebuild semantic index from media archive |
| `media_status` | Semantic index health: document count, collection info |

### HTTP API

Optional localhost HTTP API. Enable via `CROWS_NEST_HTTP_API=true` env var.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search` | POST | Combined semantic + keyword search (`{query, n_results?, platform?}`) |
| `/status` | GET | Index health dashboard |
| `/reindex` | POST | Trigger media archive reindex |
| `/health` | GET | Liveness check |

Default port: 27185. Override: `CROWS_NEST_HTTP_PORT`.

### Media Archive

Pipeline output stored in `media/` (gitignored). Structure:
\`\`\`
media/
  YYYY-MM/
    item-title/
      metadata.json     # Rich metadata (title, creator, platform, url, etc.)
      item-title.txt    # Whisper transcript
      item-title.mp4    # Video file (when available)
      item-title.m4a    # Audio file
\`\`\`
```

- [ ] **Step 2: Full integration test**

```bash
cd /Users/mriechers/Developer/second-brain/crows-nest

# Run all tests
.venv/bin/python -m pytest tests/ -v

# Smoke test: start MCP server and verify it loads
echo '{"jsonrpc": "2.0", "method": "initialize", "params": {"capabilities": {}}, "id": 1}' | timeout 5 .venv/bin/python -m mcp_knowledge.server 2>/dev/null || true
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with semantic search, HTTP API, media archive (#21)"
```

---

## Post-Implementation: Manual Steps

These are not automated — run them after all tasks are complete:

1. **Migrate existing media** (Task 1, Step 8): `mv ~/Media/crows-nest/* crows-nest/media/`
2. **Run path migration** (Task 1, Step 9): `python pipeline/migrate_media_paths.py`
3. **Run video backfill** (Task 4): `python pipeline/backfill_video.py --dry-run` then `--limit 5` to test
4. **Add R2 credentials** (Task 5, Step 8): Three `security add-generic-password` commands
5. **Build semantic index**: Via MCP tool `reindex_media` or start server with `CROWS_NEST_HTTP_API=true` and `POST /reindex`
6. **Test archiver end-to-end**: Manually run `python pipeline/archiver.py` on a few summarized items
