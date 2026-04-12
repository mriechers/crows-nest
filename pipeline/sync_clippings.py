"""
Sync Obsidian clippings with the Crow's Nest database and current pipeline spec.

Scans vault directories for notes with the 'clippings' tag, normalizes their
metadata to match the current spec, registers orphaned notes in the database,
and moves notes to the canonical output directory.

Designed to be re-run whenever the pipeline spec changes — add new normalization
rules and re-run. Already-compliant notes are skipped.

Usage:
    python sync_clippings.py                    # dry-run (default)
    python sync_clippings.py --apply            # apply fixes
    python sync_clippings.py --scan-dir PATH    # scan a specific directory
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys

from config import OBSIDIAN_CLIPPINGS, OBSIDIAN_VAULT
from content_types import classify_url
from db import DB_PATH, add_link, get_connection, init_db, update_status
from utils import setup_logging

logger = setup_logging("crows-nest.sync-clippings")

# Previous output directories (scanned for stragglers during sync)
LEGACY_CLIPPINGS_DIRS = [
    os.path.join(OBSIDIAN_VAULT, "2 - AREAS", "CLIPPINGS - Need Sorting"),
    os.path.join(OBSIDIAN_VAULT, "2 - AREAS", "INTERNET CLIPPINGS"),
]

# Content type to tag mapping (mirrors summarizer.py)
CONTENT_TYPE_TAG_MAP = {
    "youtube": "video-clip",
    "podcast": "audio-clip",
    "social_video": "video-clip",
    "audio": "audio-clip",
    "web_page": "web-clip",
    "image": "image-clip",
}


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body) where body starts after the closing ---.
    """
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
        # List item
        if line.strip().startswith("- ") and current_key:
            list_values.append(line.strip()[2:])
            continue

        # If we were collecting a list, save it
        if list_values and current_key:
            fm[current_key] = list_values
            list_values = []

        # Key-value pair
        match = re.match(r"^(\S[\w-]*)\s*:\s*(.*)", line)
        if match:
            current_key = match.group(1)
            value = match.group(2).strip()
            if value:
                # Remove surrounding quotes
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                fm[current_key] = value
            else:
                # Could be start of a list
                fm[current_key] = value

    # Final list
    if list_values and current_key:
        fm[current_key] = list_values

    return fm, body


def serialize_frontmatter(fm: dict) -> str:
    """Serialize a frontmatter dict back to YAML string."""
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, str) and (":" in value or '"' in value or value != value.strip()):
            escaped = value.replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Normalization rules
# ---------------------------------------------------------------------------
# Each rule returns True if it made a change, False if the note was already OK.


def rule_remove_legacy_tag(fm: dict, body: str) -> bool:
    """Remove the 'clippings---need-sorting' tag."""
    tags = fm.get("tags", [])
    if not isinstance(tags, list):
        return False
    legacy = "clippings---need-sorting"
    if legacy in tags:
        tags.remove(legacy)
        fm["tags"] = tags
        return True
    return False


def rule_ensure_para(fm: dict, body: str) -> bool:
    """Set para to 'areas'."""
    if fm.get("para") != "areas":
        fm["para"] = "areas"
        return True
    return False


def rule_ensure_base_tags(fm: dict, body: str) -> bool:
    """Ensure required base tags are present."""
    tags = fm.get("tags", [])
    if not isinstance(tags, list):
        tags = []
        fm["tags"] = tags

    content_type = fm.get("content-type", "web_page")
    type_tag = CONTENT_TYPE_TAG_MAP.get(content_type, "web-clip")
    required = ["all", "clippings", type_tag, "inbox-capture"]

    changed = False
    for tag in required:
        if tag not in tags:
            # Insert base tags at the front, before topic tags
            insert_idx = min(len(tags), len(required))
            tags.insert(insert_idx, tag)
            changed = True

    return changed


def rule_add_share_url(fm: dict, body: str, db_share_url: str | None = None) -> bool:
    """Add share-url from DB if note doesn't have it."""
    if "share-url" in fm:
        return False
    if db_share_url:
        fm["share-url"] = db_share_url
        return True
    return False


# All rules in execution order
NORMALIZATION_RULES = [
    ("remove_legacy_tag", rule_remove_legacy_tag),
    ("ensure_para", rule_ensure_para),
    ("ensure_base_tags", rule_ensure_base_tags),
]


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------


def find_clipping_notes(scan_dirs: list[str]) -> list[str]:
    """Find all .md files with the 'clippings' tag in the given directories."""
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


def get_db_urls(db_path: str) -> dict[str, dict]:
    """Return a map of url -> row dict for all links in the DB."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, url, status, obsidian_note_path, share_url, download_path "
            "FROM links"
        ).fetchall()
        return {row["url"]: dict(row) for row in rows}
    finally:
        conn.close()


def sync_note(
    note_path: str,
    db_urls: dict[str, dict],
    db_path: str,
    apply: bool = False,
) -> dict:
    """Process a single note. Returns a report dict."""
    report = {
        "path": note_path,
        "filename": os.path.basename(note_path),
        "fixes": [],
        "registered": False,
        "moved": False,
        "error": None,
    }

    try:
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()

        fm, body = parse_frontmatter(content)
        source_url = fm.get("source", "")

        if not source_url:
            report["error"] = "no source URL in frontmatter"
            return report

        # Look up in DB
        db_row = db_urls.get(source_url)
        db_share_url = db_row["share_url"] if db_row else None

        # Run normalization rules
        for rule_name, rule_fn in NORMALIZATION_RULES:
            if rule_fn(fm, body):
                report["fixes"].append(rule_name)

        # share-url rule needs DB context
        if rule_add_share_url(fm, body, db_share_url):
            report["fixes"].append("add_share_url")

        # Determine target path — use date subfolders when a date is available
        target_dir = OBSIDIAN_CLIPPINGS
        created_at = fm.get("created") or fm.get("date") or ""
        if created_at:
            try:
                date_str = str(created_at)[:10]
                year, month, day = date_str.split("-")
                target_dir = os.path.join(OBSIDIAN_CLIPPINGS, year, month, day)
            except (ValueError, IndexError):
                pass
        target_path = os.path.join(target_dir, os.path.basename(note_path))
        needs_move = os.path.abspath(note_path) != os.path.abspath(target_path)

        if needs_move:
            report["moved"] = True

        # Register in DB if orphaned
        if not db_row:
            report["registered"] = True

        if not apply:
            return report

        # Apply: write normalized content
        if report["fixes"]:
            new_content = serialize_frontmatter(fm) + "\n" + body
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(new_content)

        # Apply: move to canonical directory
        if needs_move:
            os.makedirs(target_dir, exist_ok=True)
            # Handle filename collision
            if os.path.exists(target_path) and os.path.abspath(note_path) != os.path.abspath(target_path):
                base, ext = os.path.splitext(os.path.basename(note_path))
                counter = 1
                while os.path.exists(target_path):
                    target_path = os.path.join(target_dir, f"{base} ({counter}){ext}")
                    counter += 1
            shutil.move(note_path, target_path)
            note_path = target_path

        # Apply: register in DB
        if report["registered"]:
            content_type = fm.get("content-type")
            if not content_type and source_url:
                content_type = classify_url(source_url)
            sender = fm.get("sender")
            metadata_dict = {}
            if fm.get("creator"):
                metadata_dict["creator"] = fm["creator"]
            if fm.get("platform"):
                metadata_dict["platform"] = fm["platform"]
            if fm.get("published"):
                metadata_dict["upload_date"] = fm["published"].replace("-", "")

            try:
                link_id = add_link(
                    url=source_url,
                    source_type="imported",
                    sender=sender,
                    content_type=content_type,
                    metadata=json.dumps(metadata_dict) if metadata_dict else None,
                    db_path=db_path,
                )
                # Set status to summarized (note already exists) and record note path
                update_status(
                    link_id=link_id,
                    status="summarized",
                    obsidian_note_path=note_path,
                    db_path=db_path,
                )
                logger.info("Registered in DB: %s (id=%d)", source_url, link_id)
            except sqlite3.IntegrityError:
                logger.info("URL already in DB: %s", source_url)

        # Apply: update obsidian_note_path in DB if it changed
        elif db_row and needs_move:
            update_status(
                link_id=db_row["id"],
                status=db_row["status"],
                obsidian_note_path=note_path,
                db_path=db_path,
            )

    except Exception as e:
        report["error"] = str(e)

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Obsidian clippings with Crow's Nest database and spec."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply changes (default is dry-run).",
    )
    parser.add_argument(
        "--scan-dir", action="append", dest="scan_dirs", metavar="PATH",
        help="Directory to scan (can be specified multiple times). "
             "Defaults to legacy + current clippings dirs.",
    )
    parser.add_argument(
        "--db", default=DB_PATH, metavar="PATH",
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    # Default scan dirs: both old and new locations
    scan_dirs = args.scan_dirs or LEGACY_CLIPPINGS_DIRS + [OBSIDIAN_CLIPPINGS]
    # Deduplicate and filter to existing dirs
    scan_dirs = list(dict.fromkeys(d for d in scan_dirs if os.path.isdir(d)))

    if not scan_dirs:
        print("No valid scan directories found.")
        return

    init_db(args.db)
    db_urls = get_db_urls(args.db)

    print(f"Scanning {len(scan_dirs)} directory(ies)...")
    for d in scan_dirs:
        print(f"  {d}")
    print()

    notes = find_clipping_notes(scan_dirs)
    print(f"Found {len(notes)} clipping note(s)")
    print()

    if not notes:
        return

    total_fixes = 0
    total_registered = 0
    total_moved = 0
    total_errors = 0

    for note_path in notes:
        report = sync_note(note_path, db_urls, args.db, apply=args.apply)

        # Print status
        actions = []
        if report["fixes"]:
            actions.append(f"fix: {', '.join(report['fixes'])}")
        if report["registered"]:
            actions.append("register in DB")
        if report["moved"]:
            actions.append("move")
        if report["error"]:
            actions.append(f"ERROR: {report['error']}")

        if actions:
            prefix = "APPLY" if args.apply else "WOULD"
            print(f"  [{prefix}] {report['filename']}")
            for a in actions:
                print(f"    -> {a}")

        total_fixes += len(report["fixes"])
        total_registered += 1 if report["registered"] else 0
        total_moved += 1 if report["moved"] else 0
        total_errors += 1 if report["error"] else 0

    print()
    mode = "Applied" if args.apply else "Dry run"
    print(f"{mode} summary:")
    print(f"  Notes scanned:  {len(notes)}")
    print(f"  Fixes needed:   {total_fixes}")
    print(f"  DB registrations: {total_registered}")
    print(f"  Notes to move:  {total_moved}")
    print(f"  Errors:         {total_errors}")

    if not args.apply and (total_fixes or total_registered or total_moved):
        print()
        print("Re-run with --apply to execute changes.")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(__file__))
    main()
