"""Tests for the status.py --sources flag."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))


def test_sources_breakdown(tmp_path, capsys):
    from db import init_db, add_link
    from status import print_sources

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    for url_id, source in [("a", "signal"), ("b", "signal"), ("c", "ingest-api"), ("d", "obsidian"), ("e", "imessage")]:
        add_link(url=f"https://example.com/{url_id}", source_type=source, db_path=db_path)

    print_sources(db_path)
    output = capsys.readouterr().out

    assert "signal" in output
    assert "ingest-api" in output
    assert "obsidian" in output
    assert "imessage" in output
    assert "2" in output  # signal has 2


def test_sources_empty_db(tmp_path, capsys):
    from db import init_db
    from status import print_sources

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    print_sources(db_path)
    output = capsys.readouterr().out

    assert "no links in this period" in output
