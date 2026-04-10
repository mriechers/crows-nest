"""
Cleanup script for the Crow's Nest pipeline.

Removes local media directories for archived items older than N days.
Database records, Obsidian notes, and vault archive images are preserved.

Usage:
    python cleanup_media.py [--db PATH] [--days N] [--dry-run]
"""

import argparse
import os
import shutil
from datetime import datetime, timedelta, timezone

from config import DB_PATH, MEDIA_ROOT, OBSIDIAN_ARCHIVE
from db import get_connection
from utils import setup_logging

logger = setup_logging("crows-nest.cleanup")


def _get_dir_size(path: str) -> int:
    """Return total size in bytes of all files under path."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                total += os.path.getsize(filepath)
            except OSError:
                pass
    return total


def _format_bytes(size: int) -> str:
    """Return a human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def get_archived_links(cutoff: datetime, db_path: str) -> list[dict]:
    """
    Return links where status='archived' and updated_at is older than cutoff.
    Only includes rows with a non-null, non-'none' download_path.
    """
    cutoff_str = cutoff.isoformat()
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT id, url, download_path, updated_at
            FROM links
            WHERE status = 'archived'
              AND updated_at < ?
              AND download_path IS NOT NULL
              AND download_path != 'none'
            ORDER BY updated_at ASC
            """,
            (cutoff_str,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def resolve_media_dir(download_path: str) -> str | None:
    """
    Resolve the media directory from download_path.

    download_path may be a file path or a directory path. Either way, we
    want the directory that lives under MEDIA_ROOT so we don't wander
    outside of it.
    """
    if not download_path:
        return None

    # If it points to a file, use the parent directory
    candidate = download_path
    if os.path.isfile(candidate):
        candidate = os.path.dirname(candidate)

    # Ensure the resolved path lives under MEDIA_ROOT
    real_media_root = os.path.realpath(MEDIA_ROOT)
    try:
        real_candidate = os.path.realpath(candidate)
    except (OSError, ValueError):
        return None

    if not real_candidate.startswith(real_media_root + os.sep) and real_candidate != real_media_root:
        logger.warning(
            "download_path %r is outside MEDIA_ROOT %r — skipping",
            download_path,
            MEDIA_ROOT,
        )
        return None

    return candidate if os.path.isdir(candidate) else None


def is_obsidian_archive_path(path: str) -> bool:
    """Return True if path is inside OBSIDIAN_ARCHIVE."""
    real_archive = os.path.realpath(OBSIDIAN_ARCHIVE)
    try:
        real_path = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    return real_path.startswith(real_archive + os.sep) or real_path == real_archive


def run(db_path: str, days: int, dry_run: bool) -> None:
    """Main cleanup loop."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    logger.info(
        "Scanning for archived items older than %d day(s) (cutoff: %s)%s",
        days,
        cutoff.strftime("%Y-%m-%d"),
        " [DRY RUN]" if dry_run else "",
    )

    links = get_archived_links(cutoff, db_path)
    logger.info("Found %d candidate link(s)", len(links))

    items_cleaned = 0
    bytes_reclaimed = 0
    items_skipped = 0

    for link in links:
        link_id = link["id"]
        download_path = link["download_path"]
        updated_at = link["updated_at"]

        media_dir = resolve_media_dir(download_path)
        if not media_dir:
            logger.debug(
                "link %d: no valid local media directory for path %r — skipping",
                link_id,
                download_path,
            )
            items_skipped += 1
            continue

        # Safety check: never delete anything inside OBSIDIAN_ARCHIVE
        if is_obsidian_archive_path(media_dir):
            logger.warning(
                "link %d: media_dir %r is inside OBSIDIAN_ARCHIVE — skipping",
                link_id,
                media_dir,
            )
            items_skipped += 1
            continue

        dir_size = _get_dir_size(media_dir)
        size_str = _format_bytes(dir_size)

        if dry_run:
            logger.info(
                "[DRY RUN] Would delete link %d: %s (%s, archived %s)",
                link_id,
                media_dir,
                size_str,
                updated_at[:10],
            )
        else:
            try:
                shutil.rmtree(media_dir)
                logger.info(
                    "Deleted link %d: %s (%s, archived %s)",
                    link_id,
                    media_dir,
                    size_str,
                    updated_at[:10],
                )
                items_cleaned += 1
                bytes_reclaimed += dir_size
            except OSError as exc:
                logger.error(
                    "link %d: failed to delete %r — %s",
                    link_id,
                    media_dir,
                    exc,
                )
                items_skipped += 1
                continue

        if dry_run:
            items_cleaned += 1
            bytes_reclaimed += dir_size

    # Summary
    action = "Would reclaim" if dry_run else "Reclaimed"
    prefix = "[DRY RUN] " if dry_run else ""
    print(
        f"\n{prefix}Cleanup complete: "
        f"{items_cleaned} item(s) {'would be ' if dry_run else ''}cleaned, "
        f"{_format_bytes(bytes_reclaimed)} {action.lower()}, "
        f"{items_skipped} skipped."
    )
    logger.info(
        "%s%d item(s) cleaned, %s %s, %d skipped",
        prefix,
        items_cleaned,
        action.lower(),
        _format_bytes(bytes_reclaimed),
        items_skipped,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Remove local media for archived Crow's Nest items."
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help="Path to the SQLite database (default: %(default)s)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Delete media for items archived more than N days ago (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without actually deleting anything",
    )
    args = parser.parse_args()
    run(db_path=args.db, days=args.days, dry_run=args.dry_run)
