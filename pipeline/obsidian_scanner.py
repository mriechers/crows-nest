#!/usr/bin/env python3
"""
Obsidian vault scanner for the Crow's Nest pipeline.

Scans the Obsidian vault for notes tagged with 'pending-clippings',
extracts URLs from the note body, ingests them via add_link(), and
archives the note to 4 - ARCHIVE/ once all URLs are processed.

This provides an offline-first fallback: just write URLs into a note
and the pipeline picks them up on the next scan cycle.
"""

import argparse
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shutil

from config import OBSIDIAN_VAULT
from content_types import classify_url
from db import DB_PATH, add_link, init_db
from utils import extract_urls, setup_logging

logger = setup_logging("crows-nest.obsidian-scanner")

_TAG_PATTERN = re.compile(r"pending-clippings")
_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _archive_note(note_path: str, vault_path: str) -> None:
    """Move a processed note to the vault's archive directory."""
    archive_dir = os.path.join(vault_path, "4 - ARCHIVE", "processed-clippings")
    os.makedirs(archive_dir, exist_ok=True)
    dest = os.path.join(archive_dir, os.path.basename(note_path))
    shutil.move(note_path, dest)
    logger.info("Archived note to %s", dest)


def find_pending_notes(vault_path: str) -> list[str]:
    """Find all .md files in the vault with 'pending-clippings' in their frontmatter tags."""
    results = []
    for root, _dirs, files in os.walk(vault_path):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            filepath = os.path.join(root, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read(2048)
            except OSError:
                continue

            fm_match = _FRONTMATTER_PATTERN.match(content)
            if not fm_match:
                continue

            frontmatter = fm_match.group(1)
            if _TAG_PATTERN.search(frontmatter):
                results.append(filepath)

    return results


def extract_urls_from_note(content: str) -> list[str]:
    """Extract URLs from note body, skipping the frontmatter block."""
    fm_match = _FRONTMATTER_PATTERN.match(content)
    if fm_match:
        body = content[fm_match.end():]
    else:
        body = content
    return extract_urls(body)


def scan_and_ingest(vault_path: str, db_path: str = DB_PATH) -> int:
    """Scan vault for pending-clippings notes, ingest URLs, delete notes.
    Returns the number of new URLs added (excludes duplicates).
    """
    notes = find_pending_notes(vault_path)
    if not notes:
        return 0

    init_db(db_path)
    total_added = 0

    for note_path in notes:
        try:
            with open(note_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            logger.error("Could not read %s: %s", note_path, exc)
            continue

        urls = extract_urls_from_note(content)
        if not urls:
            logger.info("No URLs in %s, archiving empty note", os.path.basename(note_path))
            _archive_note(note_path, vault_path)
            continue

        added = 0
        for url in urls:
            content_type = classify_url(url)
            try:
                add_link(
                    url=url,
                    source_type="obsidian",
                    sender=None,
                    context=os.path.basename(note_path),
                    content_type=content_type,
                    db_path=db_path,
                )
                added += 1
                logger.info("Queued [%s]: %s", content_type, url)
            except sqlite3.IntegrityError:
                logger.info("Skipped duplicate: %s", url)

        _archive_note(note_path, vault_path)
        logger.info("Processed note: %s (%d new, %d total URLs)", os.path.basename(note_path), added, len(urls))
        total_added += added

    return total_added


def main():
    parser = argparse.ArgumentParser(
        description="Scan Obsidian vault for pending-clippings notes and ingest URLs."
    )
    parser.add_argument("--vault", default=OBSIDIAN_VAULT, help="Path to Obsidian vault")
    parser.add_argument("--db", default=DB_PATH, help="Path to local SQLite database")
    args = parser.parse_args()

    count = scan_and_ingest(args.vault, db_path=args.db)
    if count:
        logger.info("Ingested %d new URL(s) from vault notes", count)


if __name__ == "__main__":
    main()
