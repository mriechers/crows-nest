import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

# Ensure pipeline is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "pipeline"))

from pipeline.rss_listener import (
    load_opml,
    score_article,
    fetch_feed,
    TIER_WEIGHTS,
    BOOST_KEYWORDS,
)
from pipeline.db import init_db, add_feed, list_feeds, get_top_articles


def test_load_opml(tmp_path):
    """OPML loading creates feeds in the database."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    opml_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "pipeline", "feeds.opml",
    )
    count = load_opml(opml_path, db_path=db_path)
    assert count > 0

    feeds = list_feeds(db_path=db_path)
    assert len(feeds) == count
    tiers = {f["tier"] for f in feeds}
    assert 1 in tiers


def test_score_article_tier_weight():
    """Score includes tier weight as base."""
    score = score_article(tier=1, title="Some Title", summary="Some summary", age_hours=0)
    assert score >= TIER_WEIGHTS[1]


def test_score_article_recency_bonus():
    """Recent articles score higher than old ones."""
    recent = score_article(tier=2, title="News", summary="", age_hours=1)
    old = score_article(tier=2, title="News", summary="", age_hours=30)
    assert recent > old


def test_score_article_keyword_bonus():
    """Articles mentioning local keywords get a boost."""
    with_keyword = score_article(tier=2, title="Wisconsin Public Media Update", summary="", age_hours=12)
    without_keyword = score_article(tier=2, title="Generic Tech Article", summary="", age_hours=12)
    assert with_keyword > without_keyword


def test_fetch_feed_parses_entries(tmp_path):
    """fetch_feed processes feed entries into articles table."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    feed_id = add_feed(url="https://example.com/feed.xml", title="Test Feed", tier=2, db_path=db_path)

    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_entry = MagicMock()
    mock_entry.get = lambda k, d=None: {
        "id": "entry-1",
        "title": "Test Article",
        "link": "https://example.com/article",
        "summary": "<p>Test summary</p>",
        "author": "Test Author",
    }.get(k, d)
    mock_entry.published_parsed = (2026, 3, 31, 10, 0, 0, 0, 90, 0)
    mock_feed.entries = [mock_entry]

    with patch("pipeline.rss_listener.feedparser.parse", return_value=mock_feed):
        count = fetch_feed(feed_id, "https://example.com/feed.xml", tier=2, db_path=db_path)

    assert count == 1
    articles = get_top_articles(limit=5, max_age_days=30, db_path=db_path)
    assert len(articles) == 1
    assert articles[0]["title"] == "Test Article"
    assert "<p>" not in articles[0]["summary"]


def test_fetch_feed_handles_bozo_feed(tmp_path):
    """Bozo feeds log a warning but still process valid entries."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    feed_id = add_feed(url="https://example.com/bad.xml", title="Bad Feed", tier=1, db_path=db_path)

    mock_feed = MagicMock()
    mock_feed.bozo = True
    mock_feed.bozo_exception = "not well-formed (invalid token)"
    mock_entry = MagicMock()
    mock_entry.get = lambda k, d=None: {
        "id": "bozo-entry-1",
        "title": "Still Valid Article",
        "link": "https://example.com/valid",
        "summary": "Fine despite feed error",
    }.get(k, d)
    mock_entry.published_parsed = None
    mock_feed.entries = [mock_entry]

    with patch("pipeline.rss_listener.feedparser.parse", return_value=mock_feed):
        count = fetch_feed(feed_id, "https://example.com/bad.xml", tier=1, db_path=db_path)

    assert count == 1
    articles = get_top_articles(limit=5, max_age_days=30, db_path=db_path)
    assert len(articles) == 1
    assert articles[0]["title"] == "Still Valid Article"
    assert articles[0]["score"] >= 5.0
