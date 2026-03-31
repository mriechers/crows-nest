"""
RSS feed listener for the Crow's Nest pipeline.

Polls RSS feeds on a schedule, scores articles by tier/recency/keywords,
and stores them ephemerally in SQLite with TTL-based expiry. Articles are
NOT processed through the full pipeline — this is a rolling cache for the
briefing Pulse section.
"""

import argparse
import calendar
import html
import logging
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import feedparser

try:
    from pipeline.config import DB_PATH
except ImportError:
    from config import DB_PATH

try:
    from pipeline.db import (
        add_article,
        add_feed,
        expire_old_articles,
        get_connection,
        get_top_articles,
        init_db,
        list_feeds,
        mark_articles_surfaced,
    )
except ImportError:
    from db import (
        add_article,
        add_feed,
        expire_old_articles,
        get_connection,
        get_top_articles,
        init_db,
        list_feeds,
        mark_articles_surfaced,
    )

try:
    from pipeline.utils import setup_logging
except ImportError:
    try:
        from utils import setup_logging
    except ImportError:
        def setup_logging(name: str) -> logging.Logger:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
            )
            return logging.getLogger(name)

logger = setup_logging("rss_listener")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIER_WEIGHTS: dict[int, float] = {1: 3.0, 2: 2.0, 3: 1.0}

BOOST_KEYWORDS: list[str] = [
    "wisconsin",
    "madison",
    "milwaukee",
    "pbs",
    "public media",
    "public broadcasting",
    "wpr",
    "mpr",
    "minnesota",
]

# URL patterns that determine tier classification
_TIER1_PATTERNS = [
    "pbswisconsin",
    "wpr.org",
    "mprnews",
    "mpr.org",
    "minnesotareformer",
    "minnpost",
    "racketmn",
]

_TIER2_PATTERNS = [
    "theverge",
    "techmeme",
    "platformer",
    "404media",
    "nytimes",
    "bloomberg",
    "daringfireball",
    "art19.com/the-daily",
]

_TIER3_PATTERNS = [
    "polygon",
    "bloody-disgusting",
    "forbes",
]

# Default OPML path relative to this file
_DEFAULT_OPML = os.path.join(os.path.dirname(__file__), "feeds.opml")


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML-to-plaintext stripper."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(text: str, max_chars: int = 300) -> str:
    """Strip HTML tags from text, unescape entities, and truncate."""
    if not text:
        return ""
    stripper = _HTMLStripper()
    try:
        stripper.feed(text)
        result = stripper.get_text()
    except Exception:
        # Fallback: crude tag removal
        result = re.sub(r"<[^>]+>", " ", text)
    result = html.unescape(result)
    result = re.sub(r"\s+", " ", result).strip()
    return result[:max_chars]


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

def _classify_tier(url: str) -> int:
    """Return the tier (1/2/3) for a feed URL based on domain patterns."""
    url_lower = url.lower()
    for pattern in _TIER1_PATTERNS:
        if pattern in url_lower:
            return 1
    for pattern in _TIER3_PATTERNS:
        if pattern in url_lower:
            return 3
    # Default tier 2 for anything else (includes explicit tier2 patterns)
    return 2


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_article(
    tier: int,
    title: str,
    summary: str,
    age_hours: float,
) -> float:
    """
    Compute a relevance score for an article.

    Score = base tier weight
          + recency bonus (<6h: +2.0, <12h: +1.0, <24h: +0.5)
          + keyword bonus (+1.0 if title or summary mentions a boost keyword)
    """
    score = TIER_WEIGHTS.get(tier, 1.0)

    # Recency bonus
    if age_hours < 6:
        score += 2.0
    elif age_hours < 12:
        score += 1.0
    elif age_hours < 24:
        score += 0.5

    # Keyword bonus
    combined = (title + " " + summary).lower()
    for keyword in BOOST_KEYWORDS:
        if keyword in combined:
            score += 1.0
            break

    return score


# ---------------------------------------------------------------------------
# OPML loading
# ---------------------------------------------------------------------------

def load_opml(opml_path: str, db_path: str = DB_PATH) -> int:
    """
    Parse an OPML file and insert feeds into the database.

    Handles unescaped ampersands in URLs by pre-processing the raw XML.
    Returns the number of feeds successfully added (deduplication: existing
    feeds are not re-inserted but still count toward the return value only
    for new inserts).
    """
    with open(opml_path, "r", encoding="utf-8") as f:
        raw = f.read()

    # Fix unescaped & in attribute values (common in older OPML exports)
    # Only replace & not followed by a valid XML entity reference
    raw = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", raw)

    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)

    count = 0
    for outline in root.iter("outline"):
        xml_url = outline.get("xmlUrl") or outline.get("xmlurl")
        if not xml_url:
            continue
        title = outline.get("title") or outline.get("text") or xml_url
        tier = _classify_tier(xml_url)
        add_feed(url=xml_url, title=title, tier=tier, db_path=db_path)
        count += 1

    logger.info("Loaded %d feeds from %s", count, opml_path)
    return count


# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------

def fetch_feed(
    feed_id: int,
    url: str,
    tier: int,
    db_path: str = DB_PATH,
) -> int:
    """
    Fetch and parse a single RSS/Atom feed.

    Scores each entry and inserts it into the articles table.
    Returns the number of new articles inserted.
    """
    logger.debug("Fetching feed %d: %s", feed_id, url)
    parsed = feedparser.parse(url)

    if parsed.bozo:
        logger.warning("Feed %d (%s) returned bozo error: %s", feed_id, url, parsed.get("bozo_exception", "unknown"))

    now_utc = datetime.now(timezone.utc)
    inserted = 0

    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link") or ""
        if not guid:
            continue

        title = entry.get("title") or ""
        link = entry.get("link") or ""
        raw_summary = entry.get("summary") or entry.get("description") or ""
        summary = _strip_html(raw_summary)
        author = entry.get("author") or ""

        # Parse publication time
        published_parsed = getattr(entry, "published_parsed", None)
        if published_parsed:
            try:
                pub_ts = calendar.timegm(published_parsed)
                published_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                published_at = published_dt.isoformat()
                age_hours = (now_utc - published_dt).total_seconds() / 3600.0
            except Exception:
                published_at = now_utc.isoformat()
                age_hours = 0.0
        else:
            published_at = now_utc.isoformat()
            age_hours = 0.0

        article_score = score_article(
            tier=tier,
            title=title,
            summary=summary,
            age_hours=age_hours,
        )

        result = add_article(
            feed_id=feed_id,
            guid=guid,
            title=title,
            url=link,
            summary=summary,
            published_at=published_at,
            score=article_score,
            author=author,
            db_path=db_path,
        )
        if result is not None:
            inserted += 1

    # Update last_fetched_at for the feed
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE feeds SET last_fetched_at = ? WHERE id = ?",
            (now_utc.isoformat(), feed_id),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Feed %d (%s): %d new articles", feed_id, url, inserted)
    return inserted


def fetch_all_feeds(db_path: str = DB_PATH, delay: float = 0.5) -> int:
    """
    Fetch all active feeds, then expire old articles.

    Auto-loads feeds.opml if no feeds are present in the database.
    Returns the total number of new articles inserted.
    """
    init_db(db_path)

    feeds = list_feeds(active_only=True, db_path=db_path)
    if not feeds:
        logger.info("No feeds found — auto-loading OPML from %s", _DEFAULT_OPML)
        if os.path.exists(_DEFAULT_OPML):
            load_opml(_DEFAULT_OPML, db_path=db_path)
            feeds = list_feeds(active_only=True, db_path=db_path)
        else:
            logger.warning("Default OPML not found at %s", _DEFAULT_OPML)
            return 0

    total = 0
    for feed in feeds:
        try:
            count = fetch_feed(
                feed_id=feed["id"],
                url=feed["url"],
                tier=feed["tier"],
                db_path=db_path,
            )
            total += count
        except Exception as exc:
            logger.error("Error fetching feed %d (%s): %s", feed["id"], feed["url"], exc)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    "UPDATE feeds SET last_error = ? WHERE id = ?",
                    (str(exc), feed["id"]),
                )
                conn.commit()
            finally:
                conn.close()
        if delay > 0:
            time.sleep(delay)

    expired = expire_old_articles(max_age_days=14, db_path=db_path)
    if expired:
        logger.info("Expired %d articles older than 14 days", expired)

    logger.info("fetch_all_feeds complete: %d new articles across %d feeds", total, len(feeds))
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Crow's Nest RSS listener")
    p.add_argument("--db", default=DB_PATH, help="SQLite database path")
    p.add_argument("--refresh", action="store_true", help="Fetch all feeds now")
    p.add_argument("--top", type=int, metavar="N", help="Show top N articles")
    p.add_argument("--stats", action="store_true", help="Show feed/article stats")
    p.add_argument("--load-opml", metavar="PATH", help="Load feeds from OPML file")
    p.add_argument("--list-feeds", action="store_true", help="List all active feeds")
    p.add_argument("--expire", action="store_true", help="Expire old articles now")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    db_path = args.db
    init_db(db_path)

    if args.load_opml:
        count = load_opml(args.load_opml, db_path=db_path)
        print(f"Loaded {count} feeds from {args.load_opml}")

    if args.list_feeds:
        feeds = list_feeds(active_only=False, db_path=db_path)
        for f in feeds:
            print(f"  [{f['tier']}] {f['title']} — {f['url']}")
        print(f"Total: {len(feeds)} feeds")

    if args.refresh:
        total = fetch_all_feeds(db_path=db_path)
        print(f"Fetched {total} new articles")

    if args.expire:
        n = expire_old_articles(max_age_days=14, db_path=db_path)
        print(f"Expired {n} old articles")

    if args.top:
        articles = get_top_articles(limit=args.top, max_age_days=14, db_path=db_path)
        for a in articles:
            print(f"  [{a.get('feed_tier', '?')}] {a['score']:.1f}  {a['title']}")
            print(f"      {a['url']}")

    if args.stats:
        feeds = list_feeds(active_only=False, db_path=db_path)
        conn = get_connection(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) as n FROM articles").fetchone()
            total_articles = row["n"]
            row = conn.execute("SELECT COUNT(*) as n FROM articles WHERE surfaced = 0").fetchone()
            unsurfaced = row["n"]
        finally:
            conn.close()
        print(f"Feeds: {len(feeds)}")
        print(f"Articles (total): {total_articles}")
        print(f"Articles (unsurfaced): {unsurfaced}")


if __name__ == "__main__":
    main()
