"""
Archiver for the Crow's Nest pipeline.

Stage 4: creates tar.gz archives of captured media directories, generates
SHA-256 manifests, and uploads both to the crows-nest-archive R2 bucket via
boto3 (S3-compatible). Runs daily; processes all links with status="summarized".
"""

import argparse
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone

import boto3
from botocore.config import Config as BotoConfig

from db import DB_PATH, claim_link, get_pending, log_processing, update_status
from keychain_secrets import get_secret
from utils import setup_logging

logger = setup_logging("crows-nest.archiver")

R2_BUCKET = "crows-nest-archive"


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


def create_archive(media_dir: str, output_path: str) -> list[dict]:
    """Create a tar.gz archive of media_dir at output_path.

    Returns an inventory list of dicts with keys: name, size, sha256.
    """
    inventory = []

    with tarfile.open(output_path, "w:gz") as tar:
        for dirpath, _dirnames, filenames in os.walk(media_dir):
            for filename in sorted(filenames):
                full_path = os.path.join(dirpath, filename)
                arcname = os.path.relpath(full_path, start=os.path.dirname(media_dir))
                tar.add(full_path, arcname=arcname)
                inventory.append(
                    {
                        "name": arcname,
                        "size": os.path.getsize(full_path),
                        "sha256": compute_sha256(full_path),
                    }
                )

    logger.info(
        "created archive %s (%d files)", output_path, len(inventory)
    )
    return inventory


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
    """Upload a file to R2. Returns True on success."""
    try:
        client = get_r2_client()
        client.upload_file(local_path, R2_BUCKET, r2_key)
        logger.info("Uploaded %s -> r2://%s/%s", local_path, R2_BUCKET, r2_key)
        return True
    except Exception as e:
        logger.error("R2 upload failed for %s: %s", r2_key, e)
        return False


# ---------------------------------------------------------------------------
# R2 key derivation
# ---------------------------------------------------------------------------


def _r2_key_from_media_path(media_path: str, title: str) -> str:
    """Derive an R2 key from the media directory path.

    Pattern: {YYYY}/{MM}/{title}.tar.gz
    Falls back to today's date if the path doesn't contain a YYYY-MM segment.
    """
    # media_path is typically ~/Media/crows-nest/YYYY-MM/sanitized-title
    basename = os.path.basename(media_path.rstrip("/"))
    parent = os.path.basename(os.path.dirname(media_path.rstrip("/")))

    # parent should look like "2025-06"
    try:
        year, month = parent.split("-")
        int(year)
        int(month)
    except (ValueError, AttributeError):
        now = datetime.now(timezone.utc)
        year = now.strftime("%Y")
        month = now.strftime("%m")

    safe_title = basename or title
    return f"{year}/{month}/{safe_title}.tar.gz"


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

        claimed = claim_link(
            link_id, from_status="summarized", to_status="archiving", db_path=db_path
        )
        if not claimed:
            logger.info("link %d: already claimed, skipping", link_id)
            continue

        logger.info("link %d: archiving %s", link_id, url)

        # No media directory — mark archived and skip upload
        if not media_dir or not os.path.isdir(media_dir):
            logger.info(
                "link %d: no media dir (%r), marking archived with path=none",
                link_id,
                media_dir,
            )
            update_status(
                link_id=link_id,
                status="archived",
                archive_path="none",
                db_path=db_path,
            )
            log_processing(
                link_id, "archiver", "skipped", "no media dir", db_path
            )
            continue

        try:
            title = os.path.basename(media_dir.rstrip("/"))
            r2_key = _r2_key_from_media_path(media_dir, title)
            manifest_key = r2_key.replace(".tar.gz", ".manifest.json")

            with tempfile.TemporaryDirectory() as tmpdir:
                archive_path = os.path.join(tmpdir, f"{title}.tar.gz")
                manifest_path = os.path.join(tmpdir, f"{title}.manifest.json")

                # Copy Obsidian note into media dir so it's included in the archive
                if obsidian_note and os.path.isfile(obsidian_note):
                    note_dest = os.path.join(media_dir, "obsidian-note.md")
                    shutil.copy2(obsidian_note, note_dest)
                    logger.info("link %d: included obsidian note in archive", link_id)

                # Build archive and inventory
                inventory = create_archive(media_dir, archive_path)

                # Build manifest
                manifest = {
                    "url": url,
                    "content_type": content_type,
                    "captured_at": captured_at,
                    "obsidian_note": obsidian_note,
                    "r2_key": r2_key,
                    "files": inventory,
                }
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)

                # Upload archive
                archive_ok = upload_to_r2(archive_path, r2_key)
                if not archive_ok:
                    raise RuntimeError(f"archive upload failed for key: {r2_key}")

                # Upload manifest
                manifest_ok = upload_to_r2(manifest_path, manifest_key)
                if not manifest_ok:
                    raise RuntimeError(
                        f"manifest upload failed for key: {manifest_key}"
                    )

            # Mark archived
            r2_uri = f"r2://{R2_BUCKET}/{r2_key}"
            update_status(
                link_id=link_id,
                status="archived",
                archive_path=r2_uri,
                db_path=db_path,
            )
            log_processing(
                link_id, "archiver", "success", f"r2_key: {r2_key}", db_path
            )
            logger.info("link %d: archived -> %s", link_id, r2_uri)

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
