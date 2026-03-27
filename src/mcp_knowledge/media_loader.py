"""Media archive document loader.

Walks the media archive directory structure and loads documents for indexing.

Media archive layout::

    media_root/
        YYYY-MM/
            item-title/
                metadata.json      -- rich metadata (title, creator, platform, url, ...)
                item-title.txt     -- Whisper transcript
                page.txt           -- web page content (web_page content type)
                article.md         -- scraped article markdown
                *.m4a, *.mp4       -- media files (not loaded)

Each loaded document is a dict with keys: title, text, path, metadata.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Month directory pattern: YYYY-MM
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# Text files that indicate web page / article content (checked first, in order)
_WEB_PAGE_NAMES = ("page.txt", "article.md")


def _read_text(path: str) -> str:
    """Read a text file, returning its contents as a string."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _load_metadata(item_dir: str) -> dict[str, Any]:
    """Load metadata.json from item_dir, returning {} if absent or malformed."""
    meta_path = os.path.join(item_dir, "metadata.json")
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load metadata at %s: %s", meta_path, exc)
        return {}


def _find_text_content(item_dir: str) -> str | None:
    """Return the text content for this item directory, or None if not found.

    Priority order:
    1. page.txt (web page content)
    2. article.md (scraped article)
    3. Any .txt file that is not metadata.json (Whisper transcript)
    """
    # 1 & 2: preferred web page / article files
    for name in _WEB_PAGE_NAMES:
        candidate = os.path.join(item_dir, name)
        if os.path.isfile(candidate):
            return _read_text(candidate)

    # 3: fall back to any .txt file (transcript)
    try:
        entries = os.listdir(item_dir)
    except OSError:
        return None

    for name in sorted(entries):
        if name.endswith(".txt") and name != "metadata.json":
            return _read_text(os.path.join(item_dir, name))

    return None


def load_media_documents(media_root: str) -> list[dict[str, Any]]:
    """Walk media_root and return a list of indexable document dicts.

    Each dict has the shape::

        {
            "title": str,      # From metadata.json "title", fallback to dirname
            "text": str,       # Transcript or page content
            "path": str,       # Absolute path to item directory
            "metadata": dict,  # Full metadata.json contents (or {})
        }

    Items without any text content are skipped.
    A nonexistent media_root returns an empty list without raising.
    """
    if not os.path.isdir(media_root):
        logger.debug("media_root does not exist or is not a directory: %s", media_root)
        return []

    documents: list[dict[str, Any]] = []

    try:
        month_entries = sorted(os.listdir(media_root))
    except OSError as exc:
        logger.error("Cannot list media_root %s: %s", media_root, exc)
        return []

    for month_name in month_entries:
        if not _MONTH_RE.match(month_name):
            continue

        month_dir = os.path.join(media_root, month_name)
        if not os.path.isdir(month_dir):
            continue

        try:
            item_entries = sorted(os.listdir(month_dir))
        except OSError as exc:
            logger.warning("Cannot list month dir %s: %s", month_dir, exc)
            continue

        for item_name in item_entries:
            item_dir = os.path.join(month_dir, item_name)
            if not os.path.isdir(item_dir):
                continue

            text = _find_text_content(item_dir)
            if text is None:
                logger.debug("No text content in %s — skipping", item_dir)
                continue

            metadata = _load_metadata(item_dir)
            title = metadata.get("title") or item_name

            documents.append(
                {
                    "title": title,
                    "text": text,
                    "path": item_dir,
                    "metadata": metadata,
                }
            )

    logger.info(
        "Loaded %d document(s) from media archive at %s",
        len(documents),
        media_root,
    )
    return documents
