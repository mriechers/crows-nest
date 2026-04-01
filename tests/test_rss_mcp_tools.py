"""Test RSS DB functions that the MCP tools will wrap."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "pipeline"))

from pipeline.db import (
    init_db,
    add_feed,
    add_article,
    get_top_articles,
    list_feeds,
    get_connection,
)


def test_list_recent_articles_returns_scored(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    fid = add_feed(url="https://example.com/feed", title="Test", tier=1, db_path=db_path)
    add_article(
        feed_id=fid, guid="a1", title="Article 1", url="https://example.com/1",
        summary="Summary", published_at="2026-03-31T10:00:00+00:00", score=5.0,
        db_path=db_path,
    )
    add_article(
        feed_id=fid, guid="a2", title="Article 2", url="https://example.com/2",
        summary="Summary 2", published_at="2026-03-31T08:00:00+00:00", score=3.0,
        db_path=db_path,
    )

    articles = get_top_articles(limit=10, max_age_days=30, db_path=db_path)
    assert len(articles) == 2
    assert articles[0]["score"] >= articles[1]["score"]


def test_search_articles_by_keyword(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    fid = add_feed(url="https://example.com/feed", title="Test", tier=1, db_path=db_path)
    add_article(
        feed_id=fid, guid="a1", title="Wisconsin Budget News",
        url="https://example.com/1", summary="The state budget...",
        published_at="2026-03-31T10:00:00+00:00", score=5.0, db_path=db_path,
    )
    add_article(
        feed_id=fid, guid="a2", title="Tech Industry Update",
        url="https://example.com/2", summary="Silicon Valley...",
        published_at="2026-03-31T08:00:00+00:00", score=3.0, db_path=db_path,
    )

    # Search by title keyword
    conn = get_connection(db_path)
    try:
        query = "wisconsin"
        rows = conn.execute(
            """SELECT a.id, a.title, a.url, a.summary, a.score, a.published_at,
                      f.title as source, f.tier
               FROM articles a
               JOIN feeds f ON a.feed_id = f.id
               WHERE (LOWER(a.title) LIKE ? OR LOWER(a.summary) LIKE ?)
               ORDER BY a.score DESC
               LIMIT ?""",
            (f"%{query.lower()}%", f"%{query.lower()}%", 10),
        ).fetchall()
        results = [dict(r) for r in rows]
    finally:
        conn.close()

    assert len(results) == 1
    assert results[0]["title"] == "Wisconsin Budget News"
