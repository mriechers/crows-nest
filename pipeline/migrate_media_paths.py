#!/usr/bin/env python3
"""
One-shot migration: update stored media paths in SQLite from the old
~/Media/crows-nest root to the new {CROWS_NEST_HOME}/media root.

Safe to run multiple times — rows whose paths do not start with OLD_ROOT
are left untouched (idempotent).

Usage:
    python pipeline/migrate_media_paths.py [--db <path>] [--dry-run]
"""

import argparse
import os
import sqlite3
import sys

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

OLD_ROOT = os.path.expanduser("~/Media/crows-nest")

CROWS_NEST_HOME = os.environ.get(
    "CROWS_NEST_HOME",
    os.path.expanduser("~/Developer/second-brain/crows-nest"),
)
NEW_ROOT = os.environ.get(
    "MEDIA_ROOT",
    os.path.join(CROWS_NEST_HOME, "media"),
)

# Fall back to config module's DB_PATH if available
try:
    sys.path.insert(0, os.path.dirname(__file__))
    from config import DB_PATH as _DEFAULT_DB_PATH
except ImportError:
    _DEFAULT_DB_PATH = os.path.join(CROWS_NEST_HOME, "data", "crows-nest.db")


# ---------------------------------------------------------------------------
# Migration logic
# ---------------------------------------------------------------------------

def _reroot(value: str | None, old: str, new: str) -> str | None:
    """Replace old root prefix with new root in a path string."""
    if value and value.startswith(old):
        return new + value[len(old):]
    return value


def migrate(db_path: str, dry_run: bool = False) -> int:
    """
    Update download_path and transcript_path in the links table.

    Returns the number of rows whose paths were updated.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, download_path, transcript_path FROM links"
        ).fetchall()

        updated = 0
        for row in rows:
            new_download = _reroot(row["download_path"], OLD_ROOT, NEW_ROOT)
            new_transcript = _reroot(row["transcript_path"], OLD_ROOT, NEW_ROOT)

            changed = (
                new_download != row["download_path"]
                or new_transcript != row["transcript_path"]
            )
            if not changed:
                continue

            updated += 1
            if dry_run:
                print(
                    f"[dry-run] row {row['id']}: "
                    f"download_path {row['download_path']!r} -> {new_download!r}  |  "
                    f"transcript_path {row['transcript_path']!r} -> {new_transcript!r}"
                )
            else:
                conn.execute(
                    "UPDATE links SET download_path = ?, transcript_path = ? WHERE id = ?",
                    (new_download, new_transcript, row["id"]),
                )

        if not dry_run:
            conn.commit()

    finally:
        conn.close()

    return updated


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB_PATH,
        help=f"Path to the SQLite database (default: {_DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying the database",
    )
    args = parser.parse_args()

    print(f"Old root: {OLD_ROOT}")
    print(f"New root: {NEW_ROOT}")
    print(f"Database: {args.db}")
    if args.dry_run:
        print("Mode: dry-run (no changes will be written)")
    print()

    try:
        count = migrate(args.db, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"\n{count} row(s) would be updated.")
    else:
        print(f"{count} row(s) updated.")


if __name__ == "__main__":
    main()
