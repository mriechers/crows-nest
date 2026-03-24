"""CLI tool for manual URL submission to the Crow's Nest pipeline.

Usage:
    python3 add_link.py "https://example.com" --context "Speaker: Dr. Smith"
    python3 add_link.py "https://youtu.be/abc123"
"""

import argparse
import sqlite3
import sys
import os

# Ensure sibling modules (db, content_types) are importable regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import init_db, add_link, DB_PATH
from content_types import classify_url


def main():
    parser = argparse.ArgumentParser(
        description="Add a URL to the Crow's Nest processing pipeline."
    )
    parser.add_argument("url", help="URL to add")
    parser.add_argument("--context", default=None, help="Optional context note")
    parser.add_argument(
        "--db", default=DB_PATH, dest="db_path", help="Path to SQLite database"
    )
    args = parser.parse_args()

    init_db(args.db_path)
    content_type = classify_url(args.url)

    try:
        link_id = add_link(
            url=args.url,
            source_type="cli",
            sender=None,
            context=args.context,
            content_type=content_type,
            db_path=args.db_path,
        )
        print(f"Added [{content_type}]: {args.url} (id={link_id})")
    except sqlite3.IntegrityError:
        print(f"Already queued: {args.url}")


if __name__ == "__main__":
    main()
