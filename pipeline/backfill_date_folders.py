#!/usr/bin/env python3
"""Backfill flat clippings notes into YYYY/MM/DD date subfolders.

Reads each .md file in the clippings root (not already in a subfolder),
extracts the `created:` date from YAML frontmatter, moves the file into
the appropriate YYYY/MM/DD subfolder, and updates the DB's
obsidian_note_path if a matching record exists.

Usage:
    python pipeline/backfill_date_folders.py                # dry run (default)
    python pipeline/backfill_date_folders.py --apply         # actually move files
    python pipeline/backfill_date_folders.py --apply --db /path/to/db
"""

import argparse
import os
import re
import shutil
import sqlite3
import sys

try:
    from pipeline.config import DB_PATH, OBSIDIAN_CLIPPINGS, OBSIDIAN_VAULT
except ImportError:
    from config import DB_PATH, OBSIDIAN_CLIPPINGS, OBSIDIAN_VAULT


# Match `created: YYYY-MM-DD` in frontmatter
CREATED_RE = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def extract_created_date(file_path: str) -> str | None:
    """Return YYYY-MM-DD from frontmatter, or None."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            # Only read the first 2KB — frontmatter is always at the top
            head = f.read(2048)
    except OSError:
        return None

    # Ensure we're inside frontmatter (between --- delimiters)
    if not head.startswith("---"):
        return None

    end = head.find("---", 3)
    if end == -1:
        return None

    frontmatter = head[: end + 3]
    match = CREATED_RE.search(frontmatter)
    return match.group(1) if match else None


def target_path_for(file_path: str, date_str: str) -> str:
    """Build the YYYY/MM/DD target path for a note."""
    year, month, day = date_str.split("-")
    target_dir = os.path.join(OBSIDIAN_CLIPPINGS, year, month, day)
    basename = os.path.basename(file_path)
    target = os.path.join(target_dir, basename)

    # Handle collisions
    if os.path.exists(target) and os.path.abspath(target) != os.path.abspath(file_path):
        name, ext = os.path.splitext(basename)
        counter = 1
        while os.path.exists(target):
            target = os.path.join(target_dir, f"{name} ({counter}){ext}")
            counter += 1

    return target


def update_db_path(db_path: str, old_path: str, new_path: str) -> bool:
    """Update obsidian_note_path in DB. Returns True if a row was updated."""
    conn = sqlite3.connect(db_path)
    try:
        # Convert new_path to vault-relative using the module-level OBSIDIAN_VAULT
        # (which may be monkeypatched in tests)
        vault_prefix = OBSIDIAN_VAULT.rstrip(os.sep) + os.sep
        stored_path = new_path[len(vault_prefix):] if new_path.startswith(vault_prefix) else new_path

        # Try matching on the exact path or just the filename
        # (DB may store a different base dir than where we're reading from)
        basename = os.path.basename(old_path)
        cur = conn.execute(
            "UPDATE links SET obsidian_note_path = ?, updated_at = datetime('now') "
            "WHERE obsidian_note_path LIKE ?",
            (stored_path, f"%{basename}"),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill clippings into date folders")
    parser.add_argument("--apply", action="store_true", help="Actually move files (default: dry run)")
    parser.add_argument("--db", default=DB_PATH, help="Path to crows-nest DB")
    args = parser.parse_args()

    if not os.path.isdir(OBSIDIAN_CLIPPINGS):
        print(f"Clippings directory not found: {OBSIDIAN_CLIPPINGS}")
        sys.exit(1)

    # Collect only flat .md files (not in subdirectories)
    flat_notes = [
        f for f in os.listdir(OBSIDIAN_CLIPPINGS)
        if f.endswith(".md") and os.path.isfile(os.path.join(OBSIDIAN_CLIPPINGS, f))
    ]

    if not flat_notes:
        print("No flat .md files found — nothing to backfill.")
        return

    moved = 0
    skipped_no_date = 0
    db_updated = 0

    for filename in sorted(flat_notes):
        src = os.path.join(OBSIDIAN_CLIPPINGS, filename)
        date_str = extract_created_date(src)

        if not date_str:
            skipped_no_date += 1
            print(f"  SKIP (no date): {filename}")
            continue

        dest = target_path_for(src, date_str)
        dest_rel = os.path.relpath(dest, OBSIDIAN_CLIPPINGS)

        if args.apply:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            updated = update_db_path(args.db, src, dest)
            if updated:
                db_updated += 1
            print(f"  MOVED: {filename} -> {dest_rel}" + (" (DB updated)" if updated else ""))
        else:
            print(f"  WOULD MOVE: {filename} -> {dest_rel}")

        moved += 1

    print()
    mode = "Moved" if args.apply else "Would move"
    print(f"{mode}: {moved}")
    print(f"Skipped (no date): {skipped_no_date}")
    if args.apply:
        print(f"DB records updated: {db_updated}")
    print(f"Total flat notes scanned: {len(flat_notes)}")


if __name__ == "__main__":
    main()
