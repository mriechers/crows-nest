"""Migrate clippings from old vault locations to 3 - RESOURCES/INTERNET CLIPPINGS.

Scans legacy directories for .md notes with the 'clippings' tag, extracts the
'created' date from frontmatter, and moves each note into a YYYY/MM/DD subfolder
under the new OBSIDIAN_CLIPPINGS destination. Updates obsidian_note_path in the
SQLite DB so the pipeline stays consistent.

Deduplicates by source URL — if two copies of a note exist, keeps the one in the
canonical location and skips the duplicate.

Usage:
    python migrate_to_resources.py                # dry-run (default)
    python migrate_to_resources.py --apply        # apply changes
    python migrate_to_resources.py --scan-dir /extra/path  # add a scan dir
"""

import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))

from config import OBSIDIAN_CLIPPINGS, OBSIDIAN_VAULT
from db import DB_PATH, get_connection, init_db
from utils import setup_logging

logger = setup_logging("crows-nest.migrate-to-resources")

# All legacy locations to scan
LEGACY_DIRS = [
    os.path.join(OBSIDIAN_VAULT, "2 - AREAS", "INTERNET CLIPPINGS"),
    os.path.join(OBSIDIAN_VAULT, "2 - AREAS", "CLIPPINGS - Need Sorting"),
]


# ---------------------------------------------------------------------------
# Frontmatter helpers (lightweight, mirrors sync_clippings.py)
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter. Returns (dict, body)."""
    if not content.startswith("---"):
        return {}, content
    close = content.find("---", 3)
    if close == -1:
        return {}, content
    fm_text = content[3:close].strip()
    body = content[close + 3:].lstrip("\n")

    fm = {}
    current_key = None
    list_values = []

    for line in fm_text.split("\n"):
        if line.strip().startswith("- ") and current_key:
            list_values.append(line.strip()[2:])
            continue
        if list_values and current_key:
            fm[current_key] = list_values
            list_values = []
        match = re.match(r"^(\S[\w-]*)\s*:\s*(.*)", line)
        if match:
            current_key = match.group(1)
            value = match.group(2).strip()
            if value:
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                fm[current_key] = value
            else:
                fm[current_key] = value

    if list_values and current_key:
        fm[current_key] = list_values

    return fm, body


def date_subfolder(created: str) -> str | None:
    """Extract YYYY/MM/DD subfolder from a 'created' date string."""
    if not created:
        return None
    try:
        date_str = str(created)[:10]
        year, month, day = date_str.split("-")
        # Basic validation
        if len(year) == 4 and len(month) == 2 and len(day) == 2:
            return os.path.join(year, month, day)
    except (ValueError, IndexError):
        pass
    return None


# ---------------------------------------------------------------------------
# Migration logic
# ---------------------------------------------------------------------------


def find_notes(scan_dirs: list[str]) -> list[str]:
    """Walk scan_dirs recursively and return paths to .md clipping notes."""
    notes = []
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        for root, _dirs, files in os.walk(scan_dir):
            for name in files:
                if not name.endswith(".md"):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        head = f.read(2000)
                    fm, _ = parse_frontmatter(head)
                    tags = fm.get("tags", [])
                    if isinstance(tags, list) and "clippings" in tags:
                        notes.append(path)
                except (OSError, ValueError):
                    continue
    return sorted(notes)


def compute_target(note_path: str, fm: dict) -> str:
    """Compute destination path under OBSIDIAN_CLIPPINGS with date subfolder."""
    created = fm.get("created") or fm.get("date") or ""
    subfolder = date_subfolder(created)
    if subfolder:
        target_dir = os.path.join(OBSIDIAN_CLIPPINGS, subfolder)
    else:
        target_dir = OBSIDIAN_CLIPPINGS
    return os.path.join(target_dir, os.path.basename(note_path))


def resolve_collision(target_path: str) -> str:
    """Add (1), (2) suffixes if target already exists."""
    if not os.path.exists(target_path):
        return target_path
    base, ext = os.path.splitext(target_path)
    counter = 1
    while os.path.exists(target_path):
        target_path = f"{base} ({counter}){ext}"
        counter += 1
    return target_path


def update_db_note_path(db_path: str, source_url: str, new_path: str) -> bool:
    """Update obsidian_note_path in the DB for a given URL. Returns True if updated."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "UPDATE links SET obsidian_note_path = ? WHERE url = ?",
            (new_path, source_url),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def migrate(scan_dirs: list[str], db_path: str, apply: bool) -> dict:
    """Run the migration. Returns summary stats."""
    init_db(db_path)

    notes = find_notes(scan_dirs)
    seen_urls: set[str] = set()

    stats = {"scanned": len(notes), "moved": 0, "skipped": 0, "dupes": 0, "errors": 0}

    for note_path in notes:
        try:
            with open(note_path, "r", encoding="utf-8") as f:
                content = f.read()
            fm, body = parse_frontmatter(content)
        except OSError as e:
            logger.error("cannot read %s: %s", note_path, e)
            stats["errors"] += 1
            continue

        source_url = fm.get("source", "")

        # Deduplicate by URL
        if source_url and source_url in seen_urls:
            prefix = "REMOVE" if apply else "WOULD REMOVE"
            print(f"  [{prefix}] duplicate: {os.path.basename(note_path)}")
            if apply:
                os.remove(note_path)
            stats["dupes"] += 1
            continue
        if source_url:
            seen_urls.add(source_url)

        target_path = compute_target(note_path, fm)

        # Already in the right place?
        if os.path.abspath(note_path) == os.path.abspath(target_path):
            stats["skipped"] += 1
            continue

        target_path = resolve_collision(target_path)
        target_dir = os.path.dirname(target_path)

        prefix = "MOVE" if apply else "WOULD MOVE"
        rel_src = os.path.relpath(note_path, OBSIDIAN_VAULT)
        rel_dst = os.path.relpath(target_path, OBSIDIAN_VAULT)
        print(f"  [{prefix}] {rel_src} -> {rel_dst}")

        if apply:
            os.makedirs(target_dir, exist_ok=True)
            shutil.move(note_path, target_path)
            if source_url:
                update_db_note_path(db_path, source_url, target_path)
            stats["moved"] += 1
        else:
            stats["moved"] += 1  # count as "would move" for summary

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate clippings to 3 - RESOURCES with date subfolders."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply changes (default is dry-run).",
    )
    parser.add_argument(
        "--scan-dir", action="append", dest="scan_dirs", metavar="PATH",
        help="Additional directory to scan.",
    )
    parser.add_argument(
        "--db", default=DB_PATH, metavar="PATH",
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    scan_dirs = list(LEGACY_DIRS)
    if args.scan_dirs:
        scan_dirs.extend(args.scan_dirs)
    # Deduplicate, keep order
    scan_dirs = list(dict.fromkeys(scan_dirs))

    mode = "DRY RUN" if not args.apply else "APPLYING"
    print(f"=== Migrate clippings to RESOURCES ({mode}) ===")
    print(f"Destination: {OBSIDIAN_CLIPPINGS}")
    print(f"Scanning {len(scan_dirs)} directory(ies):")
    for d in scan_dirs:
        exists = "OK" if os.path.isdir(d) else "not found"
        print(f"  {d} [{exists}]")
    print()

    stats = migrate(scan_dirs, args.db, args.apply)

    print()
    print(f"Summary:")
    print(f"  Notes scanned:  {stats['scanned']}")
    print(f"  Moved:          {stats['moved']}")
    print(f"  Already OK:     {stats['skipped']}")
    print(f"  Duplicates:     {stats['dupes']}")
    print(f"  Errors:         {stats['errors']}")

    if not args.apply and stats["moved"]:
        print()
        print("Re-run with --apply to execute the migration.")


if __name__ == "__main__":
    main()
