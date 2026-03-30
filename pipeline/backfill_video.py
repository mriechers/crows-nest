"""
One-shot backfill script: download video for items that have audio but no video.

Targets links where:
  - content_type in ('youtube', 'social_video', 'podcast')
  - download_path is not NULL
  - video_path IS NULL or empty string
  - status in ('transcribed', 'summarized', 'archived')

Usage:
    python backfill_video.py [--dry-run] [--limit N] [--db PATH]
"""

import argparse
import os
import subprocess
import sys

from db import DB_PATH, get_connection, update_status

# Storage estimates per content type
_SIZE_ESTIMATES_MB = {
    "social_video": 50,
    "youtube": 500,
    "podcast": 500,
}

R2_COST_PER_GB_MONTH = 0.015


def query_candidates(db_path: str, limit: int | None = None) -> list[dict]:
    """Return links that have audio but no video."""
    sql = """
        SELECT id, url, content_type, download_path, status
        FROM links
        WHERE content_type IN ('youtube', 'social_video', 'podcast')
          AND download_path IS NOT NULL
          AND download_path != ''
          AND (video_path IS NULL OR video_path = '')
          AND status IN ('transcribed', 'summarized', 'archived')
        ORDER BY created_at ASC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    conn = get_connection(db_path)
    try:
        cursor = conn.execute(sql)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def storage_estimate(candidates: list[dict]) -> tuple[float, float]:
    """Return (total_mb, r2_monthly_cost)."""
    total_mb = sum(
        _SIZE_ESTIMATES_MB.get(row["content_type"], 500) for row in candidates
    )
    total_gb = total_mb / 1024
    cost = total_gb * R2_COST_PER_GB_MONTH
    return total_mb, cost


def find_new_video(media_dir: str, before: set[str]) -> str | None:
    """Return the first .mp4/.mkv/.webm file in media_dir that was not in before."""
    for name in os.listdir(media_dir):
        if name.endswith((".mp4", ".mkv", ".webm")):
            full = os.path.join(media_dir, name)
            if full not in before:
                return full
    return None


def existing_videos(media_dir: str) -> set[str]:
    """Return the set of video file paths currently in media_dir."""
    result = set()
    if not os.path.isdir(media_dir):
        return result
    for name in os.listdir(media_dir):
        if name.endswith((".mp4", ".mkv", ".webm")):
            result.add(os.path.join(media_dir, name))
    return result


def download_video(url: str, media_dir: str) -> subprocess.CompletedProcess:
    return subprocess.run(
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill video downloads for items that only have audio."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show candidates and storage estimate without downloading.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N items.",
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        metavar="PATH",
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    candidates = query_candidates(args.db, args.limit)

    if not candidates:
        print("No candidates found. Nothing to do.")
        return

    total_mb, r2_cost = storage_estimate(candidates)

    print(f"Found {len(candidates)} candidate(s) with audio but no video.")
    print(f"Storage estimate: ~{total_mb:.0f} MB (~{total_mb / 1024:.2f} GB)")
    print(f"R2 cost estimate: ~${r2_cost:.4f}/month")
    print()

    if args.dry_run:
        print("Candidates (dry run — no downloads):")
        for i, row in enumerate(candidates, 1):
            est = _SIZE_ESTIMATES_MB.get(row["content_type"], 500)
            print(
                f"  [{i}/{len(candidates)}] [{row['content_type']}] "
                f"~{est} MB  {row['url']}"
            )
        return

    downloaded = 0
    failed = 0

    for i, row in enumerate(candidates, 1):
        link_id = row["id"]
        url = row["url"]
        media_dir = row["download_path"]

        print(f"[{i}/{len(candidates)}] {url}... ", end="", flush=True)

        if not os.path.isdir(media_dir):
            print(f"Failed to download video (media_dir not found: {media_dir})")
            failed += 1
            continue

        before = existing_videos(media_dir)

        try:
            result = download_video(url, media_dir)
        except subprocess.TimeoutExpired:
            print("Failed to download video (timeout after 600s)")
            failed += 1
            continue

        if result.returncode != 0:
            reason = result.stderr.strip()[:200] if result.stderr else "unknown error"
            print(f"Failed to download video: {reason}")
            failed += 1
            continue

        video_file = find_new_video(media_dir, before)
        if not video_file:
            print("Failed to download video (file not found after yt-dlp succeeded)")
            failed += 1
            continue

        size_mb = os.path.getsize(video_file) / (1024 * 1024)
        print(f"Downloaded: {os.path.basename(video_file)} ({size_mb:.1f} MB)")

        update_status(
            link_id,
            row["status"],  # preserve existing status
            db_path=args.db,
            video_path=video_file,
        )
        downloaded += 1

    print()
    print(f"Done: {downloaded} downloaded, {failed} failed")


if __name__ == "__main__":
    # Ensure pipeline/ is on the path when run directly
    sys.path.insert(0, os.path.dirname(__file__))
    main()
