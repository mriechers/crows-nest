"""Tests for RSS feed management tools: manage_feeds(deactivate) and list_all_articles."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timezone

from pipeline.db import init_db, add_feed, add_article, get_connection
import mcp_knowledge.mcp_adapter as server_mod


@pytest.fixture()
def rss_db(tmp_path, monkeypatch):
    """Create a temporary RSS DB with test data and patch _DB_PATH."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)
    monkeypatch.setattr(server_mod, "_RSS_AVAILABLE", True)

    now = datetime.now(timezone.utc).isoformat()

    fid1 = add_feed(
        url="https://example.com/feed.xml",
        title="Example Feed",
        tier=1,
        db_path=db_path,
    )
    fid2 = add_feed(
        url="https://other.com/feed.xml",
        title="Other Feed",
        tier=2,
        db_path=db_path,
    )

    # 5 articles on feed 1: all inserted as unsurfaced (add_article default)
    for i in range(5):
        add_article(
            feed_id=fid1,
            guid=f"guid-{i}",
            title=f"Article {i}",
            url=f"https://example.com/{i}",
            summary=f"Summary {i}",
            published_at=now,
            score=0.5,
            db_path=db_path,
        )

    # 2 articles on feed 2: unsurfaced
    for i in range(2):
        add_article(
            feed_id=fid2,
            guid=f"other-guid-{i}",
            title=f"Other Article {i}",
            url=f"https://other.com/{i}",
            summary=f"Other Summary {i}",
            published_at=now,
            score=0.3,
            db_path=db_path,
        )

    # Mark articles 3 and 4 (guids guid-3, guid-4) as surfaced directly in DB
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE articles SET surfaced = 1 WHERE guid IN (?, ?)", ("guid-3", "guid-4")
    )
    conn.commit()
    conn.close()

    return db_path


# ---------------------------------------------------------------------------
# list_all_articles
# ---------------------------------------------------------------------------


class TestListAllArticles:
    def test_returns_all_articles_default(self, rss_db):
        result = server_mod._list_all_articles()
        assert "articles" in result
        assert "total" in result
        assert "limit" in result
        assert "offset" in result
        assert result["total"] == 7
        assert len(result["articles"]) == 7

    def test_articles_have_expected_keys(self, rss_db):
        result = server_mod._list_all_articles()
        article = result["articles"][0]
        required = {"id", "title", "url", "summary", "score", "published_at",
                    "surfaced", "feed_title", "feed_url", "tier"}
        assert required.issubset(set(article.keys()))

    def test_filter_by_feed_url(self, rss_db):
        result = server_mod._list_all_articles(feed_url="https://example.com/feed.xml")
        assert result["total"] == 5
        assert len(result["articles"]) == 5
        for article in result["articles"]:
            assert article["feed_url"] == "https://example.com/feed.xml"

    def test_filter_by_surfaced_false(self, rss_db):
        result = server_mod._list_all_articles(surfaced=False)
        assert result["total"] == 5  # 3 from feed1 + 2 from feed2
        assert len(result["articles"]) == 5
        for article in result["articles"]:
            assert article["surfaced"] == 0

    def test_filter_by_surfaced_true(self, rss_db):
        result = server_mod._list_all_articles(surfaced=True)
        assert result["total"] == 2  # 2 surfaced from feed1
        assert len(result["articles"]) == 2
        for article in result["articles"]:
            assert article["surfaced"] == 1

    def test_filter_by_feed_url_and_surfaced(self, rss_db):
        result = server_mod._list_all_articles(
            feed_url="https://example.com/feed.xml",
            surfaced=False,
        )
        assert result["total"] == 3

    def test_pagination_limit(self, rss_db):
        result = server_mod._list_all_articles(limit=3)
        assert result["total"] == 7
        assert len(result["articles"]) == 3
        assert result["limit"] == 3

    def test_pagination_offset(self, rss_db):
        result_page1 = server_mod._list_all_articles(limit=4, offset=0)
        result_page2 = server_mod._list_all_articles(limit=4, offset=4)
        ids_page1 = {a["id"] for a in result_page1["articles"]}
        ids_page2 = {a["id"] for a in result_page2["articles"]}
        # Pages must not overlap
        assert ids_page1.isdisjoint(ids_page2)
        # Together they cover all articles
        assert len(ids_page1 | ids_page2) == 7

    def test_returns_error_when_rss_unavailable(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_RSS_AVAILABLE", False)
        result = server_mod._list_all_articles()
        assert "error" in result


# ---------------------------------------------------------------------------
# manage_feeds — deactivate action
# ---------------------------------------------------------------------------


class TestManageFeedsDeactivate:
    def test_deactivate_existing_feed(self, rss_db):
        result = server_mod._manage_feeds(
            action="deactivate",
            url="https://example.com/feed.xml",
        )
        assert result == {"deactivated": True, "url": "https://example.com/feed.xml"}

    def test_deactivated_feed_excluded_from_list(self, rss_db):
        server_mod._manage_feeds(action="deactivate", url="https://example.com/feed.xml")
        feeds = server_mod._manage_feeds(action="list")
        urls = [f["url"] for f in feeds]
        assert "https://example.com/feed.xml" not in urls

    def test_deactivate_unknown_feed_returns_error(self, rss_db):
        result = server_mod._manage_feeds(
            action="deactivate",
            url="https://nonexistent.com/feed.xml",
        )
        assert "error" in result
        assert "nonexistent" in result["error"]

    def test_deactivate_without_url_returns_error(self, rss_db):
        result = server_mod._manage_feeds(action="deactivate")
        assert "error" in result
        assert "url is required" in result["error"]

    def test_deactivate_returns_error_when_rss_unavailable(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_RSS_AVAILABLE", False)
        result = server_mod._manage_feeds(
            action="deactivate",
            url="https://example.com/feed.xml",
        )
        assert "error" in result
