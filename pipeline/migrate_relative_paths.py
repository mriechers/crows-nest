#!/usr/bin/env python3
"""One-time migration: convert absolute obsidian_note_path values to vault-relative.

Usage:
    python pipeline/migrate_relative_paths.py                # dry run
    python pipeline/migrate_relative_paths.py --apply        # apply changes
    python pipeline/migrate_relative_paths.py --apply --db /path/to/db
"""

import argparse
import sqlite3
import sys

try:
    from pipeline.config import DB_PATH, OBSIDIAN_VAULT, to_vault_relative
except ImportError:
    from config import DB_PATH, OBSIDIAN_VAULT, to_vault_relative


def main():
    parser = argparse.ArgumentParser(description="Migrate obsidian_note_path to vault-relative")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--db", default=DB_PATH, help="Path to crows-nest DB")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, obsidian_note_path FROM links WHERE obsidian_note_path IS NOT NULL AND obsidian_note_path != ''"
    ).fetchall()

    converted = 0
    already_relative = 0

    for row in rows:
        link_id = row["id"]
        old_path = row["obsidian_note_path"]
        new_path = to_vault_relative(old_path)

        if new_path == old_path:
            already_relative += 1
            continue

        if args.apply:
            conn.execute(
                "UPDATE links SET obsidian_note_path = ?, updated_at = datetime('now') WHERE id = ?",
                (new_path, link_id),
            )
            print(f"  CONVERTED id={link_id}: {old_path}")
            print(f"         -> {new_path}")
        else:
            print(f"  WOULD CONVERT id={link_id}: {old_path}")
            print(f"             -> {new_path}")

        converted += 1

    if args.apply:
        conn.commit()

    conn.close()

    print()
    mode = "Converted" if args.apply else "Would convert"
    print(f"{mode}: {converted}")
    print(f"Already relative: {already_relative}")
    print(f"Total rows checked: {len(rows)}")


if __name__ == "__main__":
    main()
