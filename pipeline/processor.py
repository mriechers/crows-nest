"""
Content processor for the Crow's Nest pipeline.

Stage 2: picks up pending links, routes by content type, processes them,
and updates the database status machine.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone, timedelta

from config import WHISPER_SCRIPT, OBSIDIAN_ARCHIVE, convert_heic_to_jpeg, resize_image
from db import init_db, get_connection, get_pending, claim_link, update_status, log_processing
from content_types import classify_url
from utils import media_dir_for, sanitize_title, setup_logging

MAX_RETRIES = 3

logger = setup_logging("crows-nest.processor")

SIGNAL_ATTACHMENTS = os.path.expanduser("~/.local/share/signal-cli/attachments")
SUPPORTED_IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_transcript(media_dir: str) -> str | None:
    """Walk media_dir tree and return the first .txt file found."""
    for dirpath, _dirnames, filenames in os.walk(media_dir):
        for name in filenames:
            if name.endswith(".txt"):
                return os.path.join(dirpath, name)
    return None


# ---------------------------------------------------------------------------
# Image handler
# ---------------------------------------------------------------------------

def _convert_heic_to_jpeg(src: str, dst: str) -> None:
    """Convert HEIC to JPEG using the best available tool (sips or ImageMagick)."""
    convert_heic_to_jpeg(src, dst)


def _resize_image(path: str, max_dim: int = 1568) -> None:
    """Resize image so longest edge is max_dim pixels. Modifies in place."""
    resize_image(path, max_dim)


def process_image(
    link_id: int,
    media_dir: str,
    metadata: dict,
    context: str,
    timestamp_slug: str,
    db_path: str,
) -> None:
    """Copy images to vault and media archive, save metadata."""
    import shutil

    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(OBSIDIAN_ARCHIVE, exist_ok=True)

    attachment_paths = metadata.get("attachment_paths", [])
    vault_filenames = []

    for i, src_path in enumerate(attachment_paths, 1):
        if not os.path.exists(src_path):
            logger.warning("link %d: attachment not found: %s", link_id, src_path)
            continue

        ext = os.path.splitext(src_path)[1].lower()
        temp_file = None

        if ext == ".heic":
            # Convert to JPEG
            temp_file = os.path.join(media_dir, f"_temp_{i}.jpg")
            _convert_heic_to_jpeg(src_path, temp_file)
            src_path = temp_file
            ext = ".jpg"
        elif ext not in SUPPORTED_IMAGE_TYPES:
            logger.warning("link %d: unsupported image format: %s", link_id, ext)
            continue

        filename = f"{timestamp_slug}-{i}{ext}"
        vault_filenames.append(filename)

        # Copy full-res to media archive
        media_dest = os.path.join(media_dir, filename)
        shutil.copy2(src_path, media_dest)

        # Copy to vault archive (resized for display)
        vault_dest = os.path.join(OBSIDIAN_ARCHIVE, filename)
        shutil.copy2(src_path, vault_dest)
        _resize_image(vault_dest)

        # Clean up HEIC temp file
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)

    # Save metadata with vault filenames
    metadata["vault_filenames"] = vault_filenames
    metadata["context"] = context
    metadata["content_type"] = "image"
    metadata["platform"] = "Signal"
    metadata["processed_at"] = datetime.now(timezone.utc).isoformat()

    metadata_path = os.path.join(media_dir, "metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    update_status(
        link_id=link_id,
        status="transcribed",
        download_path=media_dir,
        transcript_path=metadata_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_image", "success",
                   f"{len(vault_filenames)} images processed", db_path)
    logger.info("link %d: %d images copied to vault and media", link_id, len(vault_filenames))


# ---------------------------------------------------------------------------
# Web page handler
# ---------------------------------------------------------------------------

def fetch_web_content(url: str) -> tuple[str, str]:
    """Fetch a URL with curl and return (title, plain_text).

    Strips <script> and <style> blocks, then all remaining HTML tags.
    Falls back gracefully on curl failure.
    """
    result = subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-A",
         "Mozilla/5.0 (compatible; crows-nest/1.0)", url],
        capture_output=True,
        text=True,
    )
    html = result.stdout

    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else url

    # Strip script / style blocks
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html,
                     flags=re.IGNORECASE | re.DOTALL)
    # Strip all tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return title, cleaned


def process_web_page(
    link_id: int,
    url: str,
    content: str,
    title: str,
    media_dir: str,
    db_path: str,
) -> None:
    """Save fetched web content to disk and update DB to transcribed."""
    article_path = os.path.join(media_dir, "article.md")
    metadata_path = os.path.join(media_dir, "metadata.json")

    # Write article markdown
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\nSource: {url}\n\n{content}\n")

    # Write metadata JSON
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""
    metadata = {
        "url": url,
        "title": title,
        "content_type": "web_page",
        "platform": domain,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    update_status(
        link_id=link_id,
        status="transcribed",
        download_path=article_path,
        transcript_path=article_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_web_page", "success",
                   f"saved article.md: {article_path}", db_path)
    logger.info("link %d: web page saved to %s", link_id, article_path)


# ---------------------------------------------------------------------------
# Video handler
# ---------------------------------------------------------------------------

def process_video(
    link_id: int,
    url: str,
    content_type: str,
    media_dir: str,
    context: str,
    db_path: str,
) -> None:
    """Download video with yt-dlp, transcribe with Whisper."""
    logger.info("link %d: downloading video from %s", link_id, url)

    # Step 1: Fetch rich metadata before downloading
    yt_metadata = {}
    try:
        meta_result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=60,
        )
        if meta_result.returncode == 0:
            yt_metadata = json.loads(meta_result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        logger.warning("link %d: metadata fetch failed (non-fatal): %s", link_id, exc)

    # Step 2: Download audio track only — faster + whisper only needs audio
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
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")

    # Find the downloaded audio file
    audio_file = None
    for name in os.listdir(media_dir):
        if name.endswith((".m4a", ".mp3", ".wav", ".opus", ".webm")):
            audio_file = os.path.join(media_dir, name)
            break

    if not audio_file:
        raise RuntimeError("yt-dlp succeeded but no audio file found in media_dir")

    # Derive video title from the downloaded filename (yt-dlp names it after the title)
    video_title = os.path.splitext(os.path.basename(audio_file))[0]

    # Detect platform from URL domain
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""
    platform = "unknown"
    for name, domains in [
        ("TikTok", ("tiktok.com",)),
        ("YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
        ("Instagram", ("instagram.com",)),
        ("X/Twitter", ("x.com", "twitter.com")),
        ("Vimeo", ("vimeo.com",)),
        ("Facebook", ("facebook.com",)),
    ]:
        if any(domain.endswith(d) for d in domains):
            platform = name
            break

    # Build rich metadata from yt-dlp output
    metadata = {
        "url": url,
        "title": yt_metadata.get("title") or video_title,
        "content_type": content_type,
        "platform": platform,
        "creator": yt_metadata.get("uploader") or yt_metadata.get("channel") or yt_metadata.get("creator") or "",
        "creator_url": yt_metadata.get("uploader_url") or yt_metadata.get("channel_url") or "",
        "description": yt_metadata.get("description") or "",
        "upload_date": yt_metadata.get("upload_date") or "",  # YYYYMMDD format
        "duration": yt_metadata.get("duration") or 0,  # seconds
        "duration_string": yt_metadata.get("duration_string") or "",
        "view_count": yt_metadata.get("view_count") or 0,
        "like_count": yt_metadata.get("like_count") or 0,
        "comment_count": yt_metadata.get("comment_count") or 0,
        "thumbnail": yt_metadata.get("thumbnail") or "",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path = os.path.join(media_dir, "metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    update_status(link_id=link_id, status="downloading",
                  download_path=audio_file, db_path=db_path)

    # Transcribe
    prompt_arg = context if context else ""
    whisper_cmd = [WHISPER_SCRIPT, audio_file]
    if prompt_arg:
        whisper_cmd = [WHISPER_SCRIPT, "--prompt", prompt_arg, audio_file]

    result = subprocess.run(whisper_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"whisper-transcribe failed: {result.stderr[:500]}")

    transcript_path = _find_transcript(media_dir)
    if not transcript_path:
        raise RuntimeError("Whisper ran but no .txt transcript found in media_dir tree")

    update_status(
        link_id=link_id,
        status="transcribed",
        transcript_path=transcript_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_video", "success",
                   f"transcript: {transcript_path}", db_path)
    logger.info("link %d: video transcribed to %s", link_id, transcript_path)


# ---------------------------------------------------------------------------
# Audio handler
# ---------------------------------------------------------------------------

def process_audio(
    link_id: int,
    url: str,
    media_dir: str,
    context: str,
    db_path: str,
) -> None:
    """Download audio with curl, transcribe with Whisper."""
    logger.info("link %d: downloading audio from %s", link_id, url)

    filename = sanitize_title(url.split("/")[-1].split("?")[0]) or "audio.mp3"
    audio_path = os.path.join(media_dir, filename)

    result = subprocess.run(
        ["curl", "-sL", "--max-time", "300", "-o", audio_path, url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl audio download failed: {result.stderr[:500]}")

    update_status(link_id=link_id, status="downloading",
                  download_path=audio_path, db_path=db_path)

    # Transcribe
    prompt_arg = context if context else ""
    whisper_cmd = [WHISPER_SCRIPT, audio_path]
    if prompt_arg:
        whisper_cmd = [WHISPER_SCRIPT, "--prompt", prompt_arg, audio_path]

    result = subprocess.run(whisper_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"whisper-transcribe failed: {result.stderr[:500]}")

    transcript_path = _find_transcript(media_dir)
    if not transcript_path:
        raise RuntimeError("Whisper ran but no .txt transcript found in media_dir tree")

    update_status(
        link_id=link_id,
        status="transcribed",
        transcript_path=transcript_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_audio", "success",
                   f"transcript: {transcript_path}", db_path)
    logger.info("link %d: audio transcribed to %s", link_id, transcript_path)


# ---------------------------------------------------------------------------
# Stale claim recovery
# ---------------------------------------------------------------------------

def recover_stale_claims(db_path: str, stale_minutes: int = 30) -> None:
    """Reset links stuck mid-processing back to pending."""
    stale_statuses = ("downloading", "summarizing", "archiving")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    cutoff_str = cutoff.isoformat()

    conn = get_connection(db_path)
    try:
        placeholders = ",".join("?" for _ in stale_statuses)
        cursor = conn.execute(
            f"SELECT id, status, updated_at FROM links "
            f"WHERE status IN ({placeholders}) AND updated_at < ?",
            (*stale_statuses, cutoff_str),
        )
        stale_rows = cursor.fetchall()
        for row in stale_rows:
            conn.execute(
                "UPDATE links SET status = 'pending', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )
            logger.warning(
                "link %d: reset from stale status '%s' (last updated %s)",
                row["id"], row["status"], row["updated_at"],
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(db_path: str) -> None:
    """Claim and process pending links, routing by content type."""
    init_db(db_path)
    recover_stale_claims(db_path)

    pending = get_pending(status="pending", limit=20, db_path=db_path)
    logger.info("found %d pending link(s)", len(pending))

    for link in pending:
        link_id = link["id"]
        url = link["url"]
        content_type = link.get("content_type") or classify_url(url)
        context = link.get("context") or ""

        claimed = claim_link(link_id, from_status="pending",
                             to_status="downloading", db_path=db_path)
        if not claimed:
            logger.info("link %d: already claimed by another worker, skipping", link_id)
            continue

        logger.info("link %d: processing %s (%s)", link_id, url, content_type)

        try:
            if content_type == "web_page":
                title, content = fetch_web_content(url)
                mdir = media_dir_for(title)
                process_web_page(link_id, url, content, title, mdir, db_path)

            elif content_type in ("youtube", "social_video"):
                mdir = media_dir_for(sanitize_title(url))
                process_video(link_id, url, content_type, mdir, context, db_path)

            elif content_type in ("audio", "podcast"):
                mdir = media_dir_for(sanitize_title(url))
                process_audio(link_id, url, mdir, context, db_path)

            elif content_type == "image":
                link_meta = json.loads(link.get("metadata") or "{}")
                ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                mdir = media_dir_for(ts)
                process_image(link_id, mdir, link_meta, context, ts, db_path)

            else:
                # Unknown — treat as web page
                logger.warning("link %d: unknown content_type '%s', treating as web_page",
                               link_id, content_type)
                title, content = fetch_web_content(url)
                mdir = media_dir_for(title)
                process_web_page(link_id, url, content, title, mdir, db_path)

        except Exception as exc:
            error_msg = str(exc)
            logger.error("link %d: error — %s", link_id, error_msg)

            # Re-read current retry_count from DB
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT retry_count FROM links WHERE id = ?", (link_id,)
                ).fetchone()
                current_retries = row["retry_count"] if row else 0
            finally:
                conn.close()

            new_retries = current_retries + 1
            if new_retries < MAX_RETRIES:
                update_status(
                    link_id=link_id,
                    status="pending",
                    retry_count=new_retries,
                    error=error_msg,
                    db_path=db_path,
                )
                logger.warning("link %d: retry %d/%d", link_id, new_retries, MAX_RETRIES)
            else:
                update_status(
                    link_id=link_id,
                    status="failed",
                    retry_count=new_retries,
                    error=error_msg,
                    db_path=db_path,
                )
                logger.error("link %d: max retries reached, marked failed", link_id)

            log_processing(link_id, "processor", "error", error_msg, db_path)


if __name__ == "__main__":
    from db import DB_PATH
    run(DB_PATH)
