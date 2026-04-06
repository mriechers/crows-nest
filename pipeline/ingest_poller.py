#!/usr/bin/env python3
"""
Ingest poller for the Crow's Nest pipeline.

Drains the D1 ingest queue (populated by the Cloudflare Worker) into the
local pipeline database. Designed to run on a launchd timer every 5 minutes.

Each cycle:
  1. GET /api/pending — fetch unsynced items from D1
  2. For each item, call add_link() to insert into local SQLite
  3. POST /api/mark-synced — mark successfully processed items in D1

Duplicates (URLs already in local DB) are skipped and marked synced so
they don't reappear on the next poll.
"""

import argparse
import sqlite3
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import INGEST_API_URL
from content_types import classify_url
from db import DB_PATH, add_link, init_db
from keychain_secrets import get_secret
from utils import setup_logging

logger = setup_logging("crows-nest.ingest-poller")


def fetch_pending(api_url: str, token: str, limit: int = 50) -> list[dict]:
    """Fetch unsynced items from the D1 ingest queue."""
    try:
        resp = requests.get(
            f"{api_url}/pending",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("items", [])
    except Exception as exc:
        logger.error("Failed to fetch pending items: %s", exc)
        return []


def mark_synced(api_url: str, token: str, ids: list[int]) -> bool:
    """Mark items as synced in the D1 ingest queue."""
    try:
        resp = requests.post(
            f"{api_url}/mark-synced",
            headers={"Authorization": f"Bearer {token}"},
            json={"ids": ids},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Failed to mark synced: %s", exc)
        return False


def poll_and_drain(api_url: str, token: str, db_path: str = DB_PATH) -> int:
    """Poll D1 queue and drain items into local pipeline DB.
    Returns the number of new items added (excludes duplicates).
    """
    items = fetch_pending(api_url, token)
    if not items:
        return 0

    init_db(db_path)
    added = 0
    processed_ids = []

    for item in items:
        url = item["url"]
        content_type = classify_url(url)
        try:
            add_link(
                url=url,
                source_type="ingest-api",
                sender=item.get("source", "shortcut"),
                context=item.get("context"),
                content_type=content_type,
                db_path=db_path,
            )
            added += 1
            logger.info("Queued [%s]: %s", content_type, url)
        except sqlite3.IntegrityError:
            logger.info("Skipped duplicate: %s", url)

        processed_ids.append(item["id"])

    if processed_ids:
        mark_synced(api_url, token, processed_ids)

    return added


def main():
    parser = argparse.ArgumentParser(
        description="Poll the D1 ingest queue and drain into the local pipeline."
    )
    parser.add_argument("--db", default=DB_PATH, help="Path to local SQLite database")
    parser.add_argument("--limit", type=int, default=50, help="Max items per poll")
    args = parser.parse_args()

    token = get_secret("CROWS_NEST_INGEST_TOKEN")
    if not token:
        logger.error("CROWS_NEST_INGEST_TOKEN not found in Keychain or env")
        sys.exit(1)

    count = poll_and_drain(INGEST_API_URL, token, db_path=args.db)
    if count:
        logger.info("Drained %d new item(s) from ingest queue", count)


if __name__ == "__main__":
    main()
