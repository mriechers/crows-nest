"""
Archiver for the Crow's Nest pipeline.

Stage 4: uploads individual media files to the crows-nest-media-archive R2 bucket
with proper Content-Type headers for inline browser playback, generates share URLs
via the share.bymarkriechers.com custom domain, and writes them back to the database
and Obsidian note. Web pages are saved to Readwise Reader instead.
"""

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone

import boto3
import requests
from botocore.config import Config as BotoConfig

from db import DB_PATH, claim_link, get_pending, log_processing, update_status
from keychain_secrets import get_secret
from utils import setup_logging

logger = setup_logging("crows-nest.archiver")

R2_BUCKET = "crows-nest-media-archive"
SHARE_DOMAIN = "https://share.bymarkriechers.com"

MIME_TYPES = {
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Extension priority for finding the primary shareable media file
_SHAREABLE_EXTENSIONS = (".mp4", ".webm", ".mkv", ".m4a", ".mp3",
                         ".jpg", ".jpeg", ".png", ".gif", ".webp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_sha256(filepath: str) -> str:
    """Return the hex SHA-256 digest of the file at filepath."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
        config=BotoConfig(retries={"max_attempts": 3, "mode": "adaptive"}),
    )


def upload_to_r2(local_path: str, r2_key: str) -> bool:
    """Upload a file to R2 with auto-detected Content-Type. Returns True on success."""
    try:
        client = get_r2_client()
        ext = os.path.splitext(local_path)[1].lower()
        extra_args = {}
        if ext in MIME_TYPES:
            extra_args["ContentType"] = MIME_TYPES[ext]
        client.upload_file(local_path, R2_BUCKET, r2_key, ExtraArgs=extra_args)
        logger.info("Uploaded %s -> r2://%s/%s", local_path, R2_BUCKET, r2_key)
        return True
    except Exception as e:
        logger.error("R2 upload failed for %s: %s", r2_key, e)
        return False


def save_to_readwise(url: str) -> str | None:
    """Save a web page URL to Readwise Reader's archive.

    Returns the Readwise Reader URL on success, None on failure.
    """
    token = get_secret("READWISE_TOKEN")
    if not token:
        logger.warning("READWISE_TOKEN not found, skipping Readwise save")
        return None

    try:
        resp = requests.post(
            "https://readwise.io/api/v3/save/",
            headers={"Authorization": f"Token {token}"},
            json={
                "url": url,
                "location": "archive",
                "tags": ["crows-nest"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        reader_url = data.get("url", "")
        logger.info("Saved to Readwise archive: %s -> %s", url, reader_url)
        return reader_url or None
    except Exception as e:
        logger.error("Readwise save failed for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Media file discovery
# ---------------------------------------------------------------------------


def find_shareable_media(media_dir: str, video_path: str | None = None) -> str | None:
    """Find the primary shareable media file in a directory.

    Prefers video_path if provided and valid, then scans by extension priority:
    .mp4 > .webm > .mkv > .m4a > .mp3
    """
    if video_path and os.path.isfile(video_path):
        return video_path

    if not os.path.isdir(media_dir):
        return None

    files_by_ext: dict[str, list[str]] = {}
    for name in os.listdir(media_dir):
        ext = os.path.splitext(name)[1].lower()
        if ext in _SHAREABLE_EXTENSIONS:
            files_by_ext.setdefault(ext, []).append(os.path.join(media_dir, name))

    for ext in _SHAREABLE_EXTENSIONS:
        if ext in files_by_ext:
            # Pick the largest file of this type (handles multiple downloads)
            return max(files_by_ext[ext], key=os.path.getsize)

    return None


# ---------------------------------------------------------------------------
# R2 key derivation
# ---------------------------------------------------------------------------


def slugify(text: str, max_length: int = 80) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_length].rstrip("-")


def make_r2_key(media_dir: str, media_file: str) -> str:
    """Generate a clean R2 key from the media directory and file.

    Pattern: {YYYY}/{MM}/{slug}.{ext}
    """
    parent = os.path.basename(os.path.dirname(media_dir.rstrip("/")))
    try:
        year, month = parent.split("-")
        int(year)
        int(month)
    except (ValueError, AttributeError):
        now = datetime.now(timezone.utc)
        year = now.strftime("%Y")
        month = now.strftime("%m")

    basename = os.path.splitext(os.path.basename(media_file))[0]
    ext = os.path.splitext(media_file)[1].lower()
    slug = slugify(basename)

    return f"{year}/{month}/{slug}{ext}"


# ---------------------------------------------------------------------------
# Obsidian note patching
# ---------------------------------------------------------------------------


def update_obsidian_note(note_path: str, share_url: str) -> bool:
    """Add share-url to an Obsidian note's frontmatter. Idempotent."""
    if not note_path or not os.path.isfile(note_path):
        return False

    with open(note_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "share-url:" in content:
        return False  # Already has it

    # Insert share-url before the closing --- of frontmatter
    # Find the second --- (closing frontmatter delimiter)
    if content.startswith("---"):
        close_idx = content.index("---", 3)
        # Ensure we're on a new line before inserting
        prefix = "" if content[close_idx - 1] == "\n" else "\n"
        content = (
            content[:close_idx]
            + f"{prefix}share-url: {share_url}\n"
            + content[close_idx:]
        )

    # Add to Source Details section if present
    source_marker = "- **Original URL**:"
    if source_marker in content:
        if "readwise.io" in share_url or "read.readwise.io" in share_url:
            insert_line = f"- **Readwise**: [Read in Reader]({share_url})\n"
        else:
            filename = os.path.basename(share_url)
            insert_line = f"- **Archived Media**: [{filename}]({share_url})\n"
        idx = content.index(source_marker)
        # Find end of that line
        eol = content.index("\n", idx)
        content = content[: eol + 1] + insert_line + content[eol + 1 :]

    with open(note_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("Updated Obsidian note with share URL: %s", note_path)
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(db_path: str) -> None:
    """Claim summarized links, archive media to R2, update status."""
    links = get_pending(status="summarized", limit=10, db_path=db_path)
    logger.info("found %d summarized link(s)", len(links))

    for link in links:
        link_id = link["id"]
        url = link["url"]
        media_dir = link.get("download_path")
        obsidian_note = link.get("obsidian_note_path") or ""
        content_type = link.get("content_type") or "unknown"
        captured_at = link.get("created_at") or ""
        video_path = link.get("video_path")

        claimed = claim_link(
            link_id, from_status="summarized", to_status="archiving", db_path=db_path
        )
        if not claimed:
            logger.info("link %d: already claimed, skipping", link_id)
            continue

        logger.info("link %d: archiving %s", link_id, url)

        # If download_path is a file, use its parent directory
        if media_dir and os.path.isfile(media_dir):
            media_dir = os.path.dirname(media_dir)

        # No media directory — save articles to Readwise, then mark archived
        if not media_dir or not os.path.isdir(media_dir):
            logger.info(
                "link %d: no media dir (%r), marking archived with path=none",
                link_id,
                media_dir,
            )

            # Save web pages to Readwise Reader archive
            share_url = None
            if content_type == "web_page":
                reader_url = save_to_readwise(url)
                if reader_url:
                    share_url = reader_url
                    update_obsidian_note(obsidian_note, reader_url)

            update_status(
                link_id=link_id,
                status="archived",
                archive_path="none",
                share_url=share_url,
                db_path=db_path,
            )
            log_processing(
                link_id, "archiver", "skipped", "no media dir", db_path
            )
            continue

        try:
            # Find the primary shareable media file
            media_file = find_shareable_media(media_dir, video_path)
            if not media_file:
                logger.warning("link %d: no shareable media found in %s", link_id, media_dir)
                update_status(
                    link_id=link_id,
                    status="archived",
                    archive_path="none",
                    db_path=db_path,
                )
                log_processing(link_id, "archiver", "skipped", "no shareable media", db_path)
                continue

            # Generate R2 key and upload
            r2_key = make_r2_key(media_dir, media_file)
            media_ok = upload_to_r2(media_file, r2_key)
            if not media_ok:
                raise RuntimeError(f"media upload failed for key: {r2_key}")

            # Upload manifest
            manifest_key = re.sub(r"\.[^.]+$", ".manifest.json", r2_key)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                manifest = {
                    "url": url,
                    "content_type": content_type,
                    "captured_at": captured_at,
                    "media_file": os.path.basename(media_file),
                    "media_size": os.path.getsize(media_file),
                    "media_sha256": compute_sha256(media_file),
                    "r2_key": r2_key,
                    "obsidian_note": obsidian_note,
                }
                json.dump(manifest, f, indent=2)
                manifest_path = f.name

            try:
                upload_to_r2(manifest_path, manifest_key)
            finally:
                os.unlink(manifest_path)

            # Generate share URL and update Obsidian note
            share_url = f"{SHARE_DOMAIN}/{r2_key}"
            update_obsidian_note(obsidian_note, share_url)

            # Mark archived with share URL
            r2_uri = f"r2://{R2_BUCKET}/{r2_key}"
            update_status(
                link_id=link_id,
                status="archived",
                archive_path=r2_uri,
                share_url=share_url,
                db_path=db_path,
            )
            log_processing(
                link_id, "archiver", "success", f"share_url: {share_url}", db_path
            )
            logger.info("link %d: archived -> %s", link_id, share_url)

        except Exception as exc:
            error_msg = str(exc)
            logger.error("link %d: error — %s", link_id, error_msg)
            update_status(
                link_id=link_id,
                status="summarized",
                error=error_msg,
                db_path=db_path,
            )
            log_processing(link_id, "archiver", "error", error_msg, db_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crow's Nest R2 archiver")
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help="Path to the SQLite database (default: %(default)s)",
    )
    args = parser.parse_args()
    run(args.db)
