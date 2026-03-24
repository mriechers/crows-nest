import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import re
import utils


def test_sanitize_title_basic():
    assert utils.sanitize_title("Hello World") == "Hello World"


def test_sanitize_title_special_chars():
    result = utils.sanitize_title('file/:*?"<>|name')
    for ch in '/:*?"<>|':
        assert ch not in result
    assert "filename" in result.replace(" ", "")


def test_sanitize_title_length_limit():
    long_title = "A" * 200
    result = utils.sanitize_title(long_title)
    assert len(result) <= 100


def test_sanitize_title_strips_whitespace():
    assert utils.sanitize_title("  hello  ") == "hello"


def test_extract_urls_single():
    text = "Check out https://example.com for more info."
    urls = utils.extract_urls(text)
    assert urls == ["https://example.com"]


def test_extract_urls_multiple():
    text = "Visit http://foo.com and https://bar.org today."
    urls = utils.extract_urls(text)
    assert "http://foo.com" in urls
    assert "https://bar.org" in urls
    assert len(urls) == 2


def test_extract_urls_no_protocol():
    text = "Visit example.com for details."
    urls = utils.extract_urls(text)
    assert urls == []


def test_extract_urls_with_punctuation():
    text = "See https://example.com."
    urls = utils.extract_urls(text)
    assert urls == ["https://example.com"]


def test_media_dir_for(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "MEDIA_ROOT", str(tmp_path))
    path = utils.media_dir_for("My Cool Title")
    assert "crows-nest" in utils.MEDIA_ROOT or str(tmp_path) in path
    # Should contain YYYY-MM pattern
    assert re.search(r"\d{4}-\d{2}", path)
    # Should contain sanitized title
    assert "My Cool Title" in path
    # Directory should be created
    assert os.path.isdir(path)
