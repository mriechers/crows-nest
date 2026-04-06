#!/usr/bin/env python3
"""
iMessage self-message listener for the Crow's Nest pipeline.

Polls the local iMessage database for messages sent by yourself (is_from_me=1)
in direct conversations (not groups) that contain URLs. Ingests new URLs
via add_link() and tracks the last-seen ROWID to avoid reprocessing.

Usage: text yourself a URL from any device — the pipeline picks it up.
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR
from content_types import classify_url
from db import DB_PATH, add_link, init_db
from utils import extract_urls, setup_logging

logger = setup_logging("crows-nest.imessage-listener")

IMESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")
STATE_FILE = os.path.join(DATA_DIR, "imessage_state.json")


def _load_state(state_file: str) -> int:
    """Load the last-seen ROWID from state file. Returns 0 if no state."""
    try:
        with open(state_file, "r") as f:
            return json.load(f).get("last_rowid", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def _save_state(state_file: str, last_rowid: int) -> None:
    """Save the last-seen ROWID to state file."""
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump({"last_rowid": last_rowid}, f)


def fetch_self_messages(imessage_db: str, since_rowid: int = 0) -> list[dict]:
    """Fetch self-sent direct messages with URLs since the given ROWID."""
    try:
        conn = sqlite3.connect(f"file:{imessage_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        logger.error("Cannot open iMessage DB: %s", exc)
        return []

    try:
        cursor = conn.execute(
            """
            SELECT ROWID, text
            FROM message
            WHERE is_from_me = 1
              AND cache_roomnames IS NULL
              AND text LIKE '%http%'
              AND ROWID > ?
            ORDER BY ROWID ASC
            LIMIT 100
            """,
            (since_rowid,),
        )
        return [{"rowid": row["ROWID"], "text": row["text"]} for row in cursor.fetchall()]
    except sqlite3.OperationalError as exc:
        logger.error("Failed to query iMessage DB: %s", exc)
        return []
    finally:
        conn.close()


def process_self_messages(
    imessage_db: str = IMESSAGE_DB,
    db_path: str = DB_PATH,
    state_file: str = STATE_FILE,
) -> int:
    """Poll iMessage DB for self-sent URLs and ingest them.
    Returns the number of new URLs added.
    """
    last_rowid = _load_state(state_file)
    messages = fetch_self_messages(imessage_db, since_rowid=last_rowid)

    if not messages:
        return 0

    init_db(db_path)
    added = 0
    max_rowid = last_rowid

    for msg in messages:
        urls = extract_urls(msg["text"])
        for url in urls:
            content_type = classify_url(url)
            try:
                add_link(
                    url=url,
                    source_type="imessage",
                    sender=None,
                    context=None,
                    content_type=content_type,
                    db_path=db_path,
                )
                added += 1
                logger.info("Queued [%s]: %s", content_type, url)
            except sqlite3.IntegrityError:
                logger.info("Skipped duplicate: %s", url)

        max_rowid = max(max_rowid, msg["rowid"])

    _save_state(state_file, max_rowid)
    return added


def main():
    parser = argparse.ArgumentParser(
        description="Poll iMessage for self-sent URLs and ingest into the pipeline."
    )
    parser.add_argument("--db", default=DB_PATH, help="Path to pipeline SQLite database")
    parser.add_argument("--imessage-db", default=IMESSAGE_DB, help="Path to iMessage chat.db")
    parser.add_argument("--state-file", default=STATE_FILE, help="Path to state file")
    args = parser.parse_args()

    count = process_self_messages(args.imessage_db, args.db, args.state_file)
    if count:
        logger.info("Ingested %d new URL(s) from iMessage", count)


if __name__ == "__main__":
    main()
