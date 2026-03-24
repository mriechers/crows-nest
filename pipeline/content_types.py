"""URL content-type classifier for the Crow's Nest pipeline."""

import os
from urllib.parse import urlparse

_YOUTUBE_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "m.youtube.com",
}

_PODCAST_DOMAINS = {
    "podcasts.apple.com",
    "open.spotify.com",
    "overcast.fm",
    "pocketcasts.com",
    "castro.fm",
    "podbean.com",
}

_SOCIAL_VIDEO_DOMAINS = {
    "tiktok.com",
    "www.tiktok.com",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "vimeo.com",
    "www.vimeo.com",
    "dailymotion.com",
    "www.dailymotion.com",
    "facebook.com",
    "www.facebook.com",
    "threads.net",
    "www.threads.net",
}

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".wma", ".aac"}


def classify_url(url: str) -> str:
    """Classify a URL into one of five content types.

    Categories (checked in order):
        youtube, podcast, social_video, audio, web_page

    Returns:
        str: The content type category name.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    if domain in _YOUTUBE_DOMAINS:
        return "youtube"

    if domain in _PODCAST_DOMAINS or "/podcast/" in path or "/episode/" in path:
        return "podcast"

    if domain in _SOCIAL_VIDEO_DOMAINS:
        return "social_video"

    # Strip query string before checking extension — urlparse already gives us
    # just the path, but the extension check needs to ignore query params.
    _, ext = os.path.splitext(path)
    if ext in _AUDIO_EXTENSIONS:
        return "audio"

    return "web_page"
