import os
import tempfile
from datetime import datetime, timedelta, timezone
from pipeline.db import (
    init_db,
    get_connection,
    add_feed,
    add_article,
    get_top_articles,
    mark_articles_surfaced,
    expire_old_articles,
    list_feeds,
)


def _ago(days: int = 0, hours: int = 0) -> str:
    """Return an ISO-8601 timestamp *days* and *hours* before now (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).isoformat()


def test_add_feed_and_list(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    feed_id = add_feed(
        url="https://example.com/feed.xml",
        title="Example Feed",
        tier=1,
        category="local",
        db_path=db_path,
    )
    assert feed_id > 0

    feeds = list_feeds(db_path=db_path)
    assert len(feeds) == 1
    assert feeds[0]["title"] == "Example Feed"
    assert feeds[0]["tier"] == 1


def test_add_feed_deduplicates_by_url(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    id1 = add_feed(url="https://example.com/feed.xml", title="Feed", tier=1, db_path=db_path)
    id2 = add_feed(url="https://example.com/feed.xml", title="Feed Updated", tier=2, db_path=db_path)
    assert id1 == id2

    feeds = list_feeds(db_path=db_path)
    assert len(feeds) == 1


def test_add_article_and_get_top(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    feed_id = add_feed(url="https://example.com/feed.xml", title="Feed", tier=1, db_path=db_path)
    add_article(
        feed_id=feed_id,
        guid="article-1",
        title="Breaking News",
        url="https://example.com/article-1",
        summary="Something happened",
        published_at=_ago(hours=6),
        score=5.0,
        db_path=db_path,
    )

    articles = get_top_articles(limit=5, db_path=db_path)
    assert len(articles) == 1
    assert articles[0]["title"] == "Breaking News"
    assert articles[0]["score"] == 5.0


def test_mark_surfaced_excludes_from_top(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    feed_id = add_feed(url="https://example.com/feed.xml", title="Feed", tier=1, db_path=db_path)
    add_article(
        feed_id=feed_id,
        guid="a1",
        title="Article 1",
        url="https://example.com/1",
        summary="",
        published_at=_ago(hours=6),
        score=5.0,
        db_path=db_path,
    )

    articles = get_top_articles(limit=5, db_path=db_path)
    assert len(articles) == 1

    mark_articles_surfaced([articles[0]["id"]], db_path=db_path)

    articles = get_top_articles(limit=5, db_path=db_path)
    assert len(articles) == 0


def test_expire_old_articles(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    feed_id = add_feed(url="https://example.com/feed.xml", title="Feed", tier=1, db_path=db_path)
    add_article(
        feed_id=feed_id,
        guid="old-1",
        title="Old News",
        url="https://example.com/old",
        summary="",
        published_at=_ago(days=30),
        score=1.0,
        db_path=db_path,
    )
    add_article(
        feed_id=feed_id,
        guid="new-1",
        title="New News",
        url="https://example.com/new",
        summary="",
        published_at=_ago(hours=6),
        score=5.0,
        db_path=db_path,
    )

    deleted = expire_old_articles(max_age_days=14, db_path=db_path)
    assert deleted == 1

    articles = get_top_articles(limit=10, max_age_days=14, db_path=db_path)
    assert len(articles) == 1
    assert articles[0]["title"] == "New News"


def test_add_article_deduplicates_by_guid(tmp_path):
    """Inserting the same GUID twice returns None the second time."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    feed_id = add_feed(url="https://example.com/feed.xml", title="Feed", tier=1, db_path=db_path)

    result1 = add_article(
        feed_id=feed_id, guid="same-guid", title="Article",
        url="https://example.com/1", summary="",
        published_at=_ago(hours=6), score=5.0, db_path=db_path,
    )
    assert result1 is not None

    result2 = add_article(
        feed_id=feed_id, guid="same-guid", title="Article Duplicate",
        url="https://example.com/2", summary="Different",
        published_at=_ago(hours=5), score=6.0, db_path=db_path,
    )
    assert result2 is None

    articles = get_top_articles(limit=10, max_age_days=30, db_path=db_path)
    assert len(articles) == 1
    assert articles[0]["title"] == "Article"
