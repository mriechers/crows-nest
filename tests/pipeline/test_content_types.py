import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from content_types import classify_url


def test_youtube_standard():
    assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"


def test_youtube_short():
    assert classify_url("https://youtu.be/dQw4w9WgXcQ") == "youtube"


def test_youtube_nocookie():
    assert classify_url("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ") == "youtube"


def test_podcast_apple():
    assert classify_url("https://podcasts.apple.com/us/podcast/some-show/id123456") == "podcast"


def test_podcast_spotify():
    assert classify_url("https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk") == "podcast"


def test_podcast_overcast():
    assert classify_url("https://overcast.fm/+abc123") == "podcast"


def test_podcast_path_pattern():
    assert classify_url("https://example.com/podcast/my-great-show") == "podcast"


def test_social_tiktok():
    assert classify_url("https://www.tiktok.com/@user/video/123456789") == "social_video"


def test_social_instagram():
    assert classify_url("https://www.instagram.com/reel/abc123/") == "social_video"


def test_social_x():
    assert classify_url("https://x.com/user/status/123456789") == "social_video"


def test_social_vimeo():
    assert classify_url("https://vimeo.com/123456789") == "social_video"


def test_audio_mp3():
    assert classify_url("https://example.com/episodes/show-ep1.mp3") == "audio"


def test_audio_m4a_query():
    assert classify_url("https://cdn.example.com/audio/track.m4a?token=abc") == "audio"


def test_web_page_default():
    assert classify_url("https://example.com") == "web_page"


def test_web_page_docs():
    assert classify_url("https://docs.python.org/3/library/urllib.parse.html") == "web_page"


def test_edge_case_m_youtube():
    # Mobile YouTube subdomain should classify as youtube
    assert classify_url("https://m.youtube.com/watch?v=abc123") == "youtube"
