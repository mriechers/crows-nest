"""
Content processor for the Crow's Nest pipeline.

Stage 2: picks up pending links, routes by content type, processes them,
and updates the database status machine.
"""

import json
import os
import re
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

from config import WHISPER_SCRIPT, OBSIDIAN_ARCHIVE, convert_heic_to_jpeg, resize_image
from db import init_db, get_connection, get_pending, claim_link, update_status, log_processing
from content_types import classify_url
from utils import media_dir_for, sanitize_title, setup_logging

MAX_RETRIES = 3

logger = setup_logging("crows-nest.processor")

SIGNAL_ATTACHMENTS = os.path.expanduser("~/.local/share/signal-cli/attachments")
SUPPORTED_IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_transcript(media_dir: str) -> str | None:
    """Walk media_dir tree and return the first .txt file found."""
    for dirpath, _dirnames, filenames in os.walk(media_dir):
        for name in filenames:
            if name.endswith(".txt"):
                return os.path.join(dirpath, name)
    return None


# ---------------------------------------------------------------------------
# Image handler
# ---------------------------------------------------------------------------

def _convert_heic_to_jpeg(src: str, dst: str) -> None:
    """Convert HEIC to JPEG using the best available tool (sips or ImageMagick)."""
    convert_heic_to_jpeg(src, dst)


def _resize_image(path: str, max_dim: int = 1568) -> None:
    """Resize image so longest edge is max_dim pixels. Modifies in place."""
    resize_image(path, max_dim)


def process_image(
    link_id: int,
    media_dir: str,
    metadata: dict,
    context: str,
    timestamp_slug: str,
    db_path: str,
) -> None:
    """Copy images to vault and media archive, save metadata."""
    import shutil

    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(OBSIDIAN_ARCHIVE, exist_ok=True)

    attachment_paths = metadata.get("attachment_paths", [])
    vault_filenames = []

    for i, src_path in enumerate(attachment_paths, 1):
        if not os.path.exists(src_path):
            logger.warning("link %d: attachment not found: %s", link_id, src_path)
            continue

        ext = os.path.splitext(src_path)[1].lower()
        temp_file = None

        if ext == ".heic":
            # Convert to JPEG
            temp_file = os.path.join(media_dir, f"_temp_{i}.jpg")
            _convert_heic_to_jpeg(src_path, temp_file)
            src_path = temp_file
            ext = ".jpg"
        elif ext not in SUPPORTED_IMAGE_TYPES:
            logger.warning("link %d: unsupported image format: %s", link_id, ext)
            continue

        filename = f"{timestamp_slug}-{i}{ext}"
        vault_filenames.append(filename)

        # Copy full-res to media archive
        media_dest = os.path.join(media_dir, filename)
        shutil.copy2(src_path, media_dest)

        # Copy to vault archive (resized for display)
        vault_dest = os.path.join(OBSIDIAN_ARCHIVE, filename)
        shutil.copy2(src_path, vault_dest)
        _resize_image(vault_dest)

        # Clean up HEIC temp file
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)

    # Save metadata with vault filenames
    metadata["vault_filenames"] = vault_filenames
    metadata["context"] = context
    metadata["content_type"] = "image"
    metadata["platform"] = "Signal"
    metadata["processed_at"] = datetime.now(timezone.utc).isoformat()

    metadata_path = os.path.join(media_dir, "metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    update_status(
        link_id=link_id,
        status="transcribed",
        download_path=media_dir,
        transcript_path=metadata_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_image", "success",
                   f"{len(vault_filenames)} images processed", db_path)
    logger.info("link %d: %d images copied to vault and media", link_id, len(vault_filenames))


# ---------------------------------------------------------------------------
# Web page handler
# ---------------------------------------------------------------------------

def fetch_web_content(url: str) -> tuple[str, str]:
    """Fetch a URL with curl and return (title, plain_text).

    Strips <script> and <style> blocks, then all remaining HTML tags.
    Falls back gracefully on curl failure.
    """
    result = subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-A",
         "Mozilla/5.0 (compatible; crows-nest/1.0)", url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed for {url}: {result.stderr.strip()[:300]}")

    html = result.stdout
    if not html.strip():
        raise RuntimeError(f"empty response from {url}")

    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else url

    # Strip script / style blocks
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html,
                     flags=re.IGNORECASE | re.DOTALL)
    # Strip all tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return title, cleaned


def process_web_page(
    link_id: int,
    url: str,
    content: str,
    title: str,
    media_dir: str,
    db_path: str,
) -> None:
    """Save fetched web content to disk and update DB to transcribed."""
    article_path = os.path.join(media_dir, "article.md")
    metadata_path = os.path.join(media_dir, "metadata.json")

    # Write article markdown
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\nSource: {url}\n\n{content}\n")

    # Write metadata JSON
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""
    metadata = {
        "url": url,
        "title": title,
        "content_type": "web_page",
        "platform": domain,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    update_status(
        link_id=link_id,
        status="transcribed",
        download_path=article_path,
        transcript_path=article_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_web_page", "success",
                   f"saved article.md: {article_path}", db_path)
    logger.info("link %d: web page saved to %s", link_id, article_path)


# ---------------------------------------------------------------------------
# Video handler
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Podcast transcript fetching via RSS
# ---------------------------------------------------------------------------

def _apple_lookup_by_id(podcast_id: str) -> str | None:
    """Look up an RSS feed URL via the iTunes API using a podcast ID."""
    try:
        lookup_url = f"https://itunes.apple.com/lookup?id={podcast_id}&entity=podcast"
        req = urllib.request.Request(lookup_url, headers={
            "User-Agent": "CrowsNest/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        if results:
            return results[0].get("feedUrl")
    except Exception as exc:
        logger.warning("Apple Podcasts lookup failed (non-fatal): %s", exc)
    return None


def _apple_lookup_by_name(show_name: str) -> str | None:
    """Search the iTunes API for a podcast by name, return RSS feed URL."""
    try:
        encoded = urllib.parse.quote_plus(show_name)
        search_url = f"https://itunes.apple.com/search?term={encoded}&entity=podcast&limit=3"
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "CrowsNest/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        # Pick the best match by name similarity
        for result in results:
            name = result.get("collectionName", "").lower()
            if _title_similarity(show_name.lower(), name) > 0.5:
                feed_url = result.get("feedUrl")
                if feed_url:
                    return feed_url
        # Fall back to first result if any
        if results and results[0].get("feedUrl"):
            return results[0]["feedUrl"]
    except Exception as exc:
        logger.warning("Apple Podcasts name search failed (non-fatal): %s", exc)
    return None


def _extract_rss_from_html(html: str) -> str | None:
    """Find an RSS feed link in an HTML page's <head>."""
    # Match <link rel="alternate" type="application/rss+xml" href="...">
    # Attributes can appear in any order
    for pattern in [
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/rss\+xml["\']',
        r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
    ]:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _fetch_page(url: str) -> str | None:
    """Fetch a web page and return its HTML. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "CrowsNest/1.0 (content-pipeline)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("page fetch failed for %s: %s", url, exc)
        return None


def _resolve_rss_feed(url: str) -> str | None:
    """Resolve a podcast platform URL to its RSS feed URL.

    Supports:
    - Apple Podcasts (via iTunes lookup API)
    - Spotify (scrape page for show name → Apple lookup)
    - Overcast (page scraping for RSS link)
    - Any web page with a <link type="application/rss+xml"> tag
      or Schema.org PodcastEpisode markup

    Returns the feed URL or None.
    """
    parsed = urllib.parse.urlparse(url)
    domain = parsed.hostname or ""

    # Apple Podcasts: extract podcast ID, hit lookup API
    if "podcasts.apple.com" in domain:
        id_match = re.search(r'/id(\d+)', parsed.path)
        if id_match:
            feed_url = _apple_lookup_by_id(id_match.group(1))
            if feed_url:
                logger.info("resolved Apple Podcasts -> RSS: %s", feed_url)
                return feed_url

    # Spotify: scrape the page for the show name, search Apple for the RSS feed
    if "open.spotify.com" in domain and "/episode/" in parsed.path:
        html = _fetch_page(url)
        if html:
            show_name = None

            # Try <title> tag first — format: "Episode - Show | Podcast on Spotify"
            title_match = re.search(r"<title>([^<]+)</title>", html)
            if title_match:
                page_title = title_match.group(1).strip()
                # Strip the " | Podcast on Spotify" suffix
                page_title = re.sub(r'\s*\|\s*Podcast on Spotify\s*$', '', page_title)
                # Split on " - " to get "Episode - Show"
                if " - " in page_title:
                    parts = page_title.rsplit(" - ", 1)
                    candidate = parts[-1].strip()
                    if candidate and candidate.lower() not in ("spotify", "podcast"):
                        show_name = candidate

            # Fallback: try og:title (sometimes has "Episode - Show")
            if not show_name:
                og_match = re.search(
                    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                    html, re.IGNORECASE,
                )
                if not og_match:
                    og_match = re.search(
                        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
                        html, re.IGNORECASE,
                    )
                if og_match:
                    og_title = og_match.group(1)
                    for sep in [" - ", " | "]:
                        if sep in og_title:
                            parts = og_title.split(sep)
                            candidate = parts[-1].strip()
                            if candidate and candidate.lower() not in (
                                "spotify", "podcast on spotify", "podcast",
                            ):
                                show_name = candidate
                                break

            if show_name:
                logger.info("extracted show name from Spotify: '%s'", show_name)
                feed_url = _apple_lookup_by_name(show_name)
                if feed_url:
                    logger.info("resolved Spotify -> RSS (via Apple): %s", feed_url)
                    return feed_url
                else:
                    logger.info("could not find RSS feed for show '%s'", show_name)

    # Overcast: page contains RSS feed link
    if "overcast.fm" in domain:
        html = _fetch_page(url)
        if html:
            feed_url = _extract_rss_from_html(html)
            if feed_url:
                logger.info("resolved Overcast -> RSS: %s", feed_url)
                return feed_url

    # Generic web page: check for RSS link or PodcastEpisode schema
    if domain and not any(d in domain for d in [
        "podcasts.apple.com", "open.spotify.com", "overcast.fm",
        "tiktok.com", "youtube.com", "youtu.be", "instagram.com",
    ]):
        html = _fetch_page(url)
        if html:
            # Check for podcast indicators
            has_podcast_schema = '"PodcastEpisode"' in html or '"PodcastSeries"' in html
            feed_url = _extract_rss_from_html(html)

            if feed_url and has_podcast_schema:
                logger.info("resolved podcast web page -> RSS: %s", feed_url)
                return feed_url
            elif feed_url:
                # Has RSS but no podcast schema — might be a blog
                # Only use if the RSS feed itself looks like a podcast
                logger.debug("found RSS link but no podcast schema for %s", url)

    return None


def _extract_apple_episode_id(url: str) -> str | None:
    """Extract the episode ID from an Apple Podcasts URL."""
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    episode_ids = qs.get("i", [])
    return episode_ids[0] if episode_ids else None


def _match_episode_in_feed(feed_xml: str, url: str, yt_metadata: dict) -> dict | None:
    """Find the matching episode in an RSS feed.

    Matches by Apple episode ID (embedded in the enclosure URL's GUID),
    episode title, or enclosure URL.  Returns a dict with 'title',
    'transcript_url', 'transcript_type', and 'description' if found.
    """
    apple_episode_id = _extract_apple_episode_id(url)
    target_title = (yt_metadata.get("title") or "").lower().strip()

    # Split feed into items
    items = re.findall(r"<item>(.*?)</item>", feed_xml, re.DOTALL)

    for item_xml in items:
        # Check for podcast:transcript tag
        transcript_match = re.search(
            r'<podcast:transcript\s+url=["\']([^"\']+)["\']'
            r'\s+type=["\']([^"\']+)["\']',
            item_xml,
        )
        if not transcript_match:
            continue

        # This episode has a transcript — check if it's the right one
        matched = False

        # Match by Apple episode ID in GUID or enclosure URL
        if apple_episode_id:
            if apple_episode_id in item_xml:
                matched = True

        # Match by title
        if not matched and target_title:
            title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item_xml)
            if title_match:
                feed_title = title_match.group(1).strip().lower()
                # Fuzzy: check if either contains the other
                if (target_title in feed_title
                        or feed_title in target_title
                        or _title_similarity(target_title, feed_title) > 0.6):
                    matched = True

        if matched:
            title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item_xml)
            result = {
                "title": title_match.group(1).strip() if title_match else "",
                "transcript_url": transcript_match.group(1),
                "transcript_type": transcript_match.group(2),
            }
            # Also extract enclosure URL for audio fallback
            enc_match = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', item_xml)
            if enc_match:
                result["enclosure_url"] = enc_match.group(1)
            return result

    return None


def _find_episode_audio_in_feed(feed_xml: str, url: str, yt_metadata: dict) -> str | None:
    """Find the audio enclosure URL for a matching episode in an RSS feed.

    Used as a fallback when yt-dlp can't download audio (e.g. Spotify DRM).
    Returns the direct audio URL or None.
    """
    apple_episode_id = _extract_apple_episode_id(url)
    target_title = (yt_metadata.get("title") or "").lower().strip()

    items = re.findall(r"<item>(.*?)</item>", feed_xml, re.DOTALL)

    for item_xml in items:
        enc_match = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', item_xml)
        if not enc_match:
            continue

        matched = False

        if apple_episode_id and apple_episode_id in item_xml:
            matched = True

        if not matched and target_title:
            title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item_xml)
            if title_match:
                feed_title = title_match.group(1).strip().lower()
                if (target_title in feed_title
                        or feed_title in target_title
                        or _title_similarity(target_title, feed_title) > 0.6):
                    matched = True

        if matched:
            return enc_match.group(1)

    return None


def _title_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity between two titles."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    overlap = words_a & words_b
    return len(overlap) / max(len(words_a), len(words_b))


def _download_transcript(transcript_url: str, transcript_type: str,
                         media_dir: str) -> str | None:
    """Download and convert a podcast transcript to plain text.

    Supports VTT, SRT, HTML, and plain text formats.
    Returns path to the transcript .txt file, or None on failure.
    """
    try:
        req = urllib.request.Request(transcript_url, headers={
            "User-Agent": "CrowsNest/1.0",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("transcript download failed: %s", exc)
        return None

    if len(content) < 50:
        logger.info("transcript too short (%d chars), skipping", len(content))
        return None

    # Convert based on format
    if "vtt" in transcript_type:
        # Save VTT then convert with existing function
        vtt_path = os.path.join(media_dir, "transcript.vtt")
        with open(vtt_path, "w", encoding="utf-8") as f:
            f.write(content)
        text = _vtt_to_text(vtt_path)
    elif "srt" in transcript_type:
        text = _srt_to_text(content)
    elif "html" in transcript_type:
        text = _html_to_text(content)
    else:
        # Assume plain text
        text = content

    if len(text) < 50:
        logger.info("transcript too short after %s conversion (%d chars), skipping",
                     transcript_type, len(text))
        return None

    transcript_path = os.path.join(media_dir, "transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(text)

    return transcript_path


def _srt_to_text(content: str) -> str:
    """Convert SRT subtitle content to plain text."""
    lines = []
    seen = set()
    for line in content.splitlines():
        # Skip sequence numbers, timestamps, blank lines
        if (not line.strip()
                or re.match(r"^\d+$", line.strip())
                or re.match(r"^\d{2}:\d{2}:\d{2}", line.strip())):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return "\n".join(lines)


def _html_to_text(content: str) -> str:
    """Convert HTML transcript to plain text."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.DOTALL)
    # Convert <br>, <p>, <div> to newlines
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</?(p|div|h[1-6])[^>]*>", "\n", text)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Decode entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = text.replace("&#x27;", "'").replace("&#x2F;", "/")
    # Decode any remaining numeric entities (guard against out-of-range values)
    text = re.sub(
        r'&#x([0-9a-fA-F]+);',
        lambda m: chr(int(m.group(1), 16)) if int(m.group(1), 16) <= 0x10FFFF else '',
        text,
    )
    text = re.sub(
        r'&#(\d+);',
        lambda m: chr(int(m.group(1))) if int(m.group(1)) <= 0x10FFFF else '',
        text,
    )
    return text.strip()


def _try_scrape_page_transcript(
    url: str, media_dir: str, link_id: int,
) -> str | None:
    """Try to extract an embedded transcript from a podcast episode web page.

    Looks for common patterns:
    - Elements with id="transcript" or class containing "transcript"
    - <h2>/<h3> headings with "Transcript" followed by content

    Returns path to transcript .txt file, or None.
    """
    html = _fetch_page(url)
    if not html:
        return None

    transcript_text = None

    # Pattern 1: element with id="transcript"
    # Handles both <div id="transcript"> and <fieldset id="transcript">
    match = re.search(
        r'id=["\']transcript["\'][^>]*>(.*?)(?:</div>|</fieldset>|</section>)',
        html, re.DOTALL | re.IGNORECASE,
    )

    # Pattern 2: element with class containing "transcript"
    if not match:
        match = re.search(
            r'class=["\'][^"\']*transcript[^"\']*["\'][^>]*>(.*?)(?:</div>|</section>)',
            html, re.DOTALL | re.IGNORECASE,
        )

    # Pattern 3: heading "Transcript" followed by content until next heading
    if not match:
        match = re.search(
            r'<h[23][^>]*>\s*(?:Full\s+)?Transcript\s*</h[23]>\s*(.*?)(?=<h[23]|<footer|</article)',
            html, re.DOTALL | re.IGNORECASE,
        )

    if not match:
        return None

    raw_html = match.group(1)
    transcript_text = _html_to_text(raw_html)

    if not transcript_text or len(transcript_text) < 100:
        logger.debug("link %d: page transcript too short (%d chars)",
                     link_id, len(transcript_text) if transcript_text else 0)
        return None

    transcript_path = os.path.join(media_dir, "transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(transcript_text)

    logger.info("link %d: scraped page transcript (%d chars) from %s",
                link_id, len(transcript_text), url)
    return transcript_path


def _try_fetch_podcast_transcript(
    url: str, media_dir: str, link_id: int, yt_metadata: dict,
) -> tuple[str | None, str | None]:
    """Try to find and download a podcast transcript via RSS feed.

    Resolves the podcast URL to an RSS feed, searches for a
    <podcast:transcript> tag on the matching episode, downloads
    and converts the transcript.

    Returns (transcript_path, audio_url):
        transcript_path — path to transcript .txt file, or None
        audio_url — direct audio enclosure URL from RSS (for yt-dlp
                    fallback when DRM blocks download), or None
    """
    feed_url = _resolve_rss_feed(url)
    if not feed_url:
        logger.info("link %d: could not resolve RSS feed for %s", link_id, url)
        return None, None

    # Fetch the RSS feed
    try:
        req = urllib.request.Request(feed_url, headers={
            "User-Agent": "CrowsNest/1.0",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            feed_xml = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("link %d: RSS feed fetch failed: %s", link_id, exc)
        return None, None

    # Try to find the audio enclosure URL (useful as yt-dlp fallback)
    audio_url = _find_episode_audio_in_feed(feed_xml, url, yt_metadata)

    # Find the matching episode with transcript
    episode = _match_episode_in_feed(feed_xml, url, yt_metadata)
    if not episode:
        logger.info("link %d: no transcript tag in RSS feed for this episode", link_id)
        return None, audio_url

    logger.info("link %d: found RSS transcript (%s) for '%s'",
                link_id, episode["transcript_type"], episode["title"])

    # Download and convert the transcript
    transcript_path = _download_transcript(
        episode["transcript_url"], episode["transcript_type"], media_dir,
    )
    return transcript_path, audio_url or episode.get("enclosure_url")


# ---------------------------------------------------------------------------
# Subtitle fetching (YouTube, social video)
# ---------------------------------------------------------------------------

def _vtt_to_text(vtt_path: str) -> str:
    """Convert a VTT subtitle file to plain text.

    Strips timestamps, positioning metadata, and deduplicates overlapping
    caption lines.  Preserves speaker labels (e.g. "Narrator:").
    """
    with open(vtt_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = []
    seen = set()
    for line in content.splitlines():
        # Skip VTT header, blank lines, timestamps, and positioning
        if (not line.strip()
                or line.startswith("WEBVTT")
                or line.startswith("Kind:")
                or line.startswith("Language:")
                or re.match(r"^\d{2}:\d{2}:\d{2}", line)
                or line.startswith("NOTE")):
            continue

        # Strip HTML tags (some VTT files use <c> color tags)
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if not clean:
            continue

        # Deduplicate — VTT cues often repeat text across overlapping windows
        if clean not in seen:
            seen.add(clean)
            lines.append(clean)

    return "\n".join(lines)


def _try_fetch_subtitles(url: str, media_dir: str, link_id: int) -> str | None:
    """Try to download existing subtitles for a video via yt-dlp.

    Checks for uploaded captions first (higher quality), then falls back
    to auto-generated captions.  Returns the path to a plain-text
    transcript file, or None if no subtitles are available.
    """
    vtt_output = os.path.join(media_dir, "%(title)s.%(ext)s")

    # Try uploaded captions first — human-authored, better quality
    try:
        result = subprocess.run(
            [
                "yt-dlp", "--write-sub", "--sub-lang", "en",
                "--skip-download", "--sub-format", "vtt",
                "--output", vtt_output, url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.info("link %d: no uploaded captions (yt-dlp: %s)",
                        link_id, result.stderr.strip()[:200] or "no subtitles")
    except subprocess.TimeoutExpired:
        logger.warning("link %d: yt-dlp subtitle fetch timed out", link_id)

    # Check if a .vtt file was written
    vtt_path = None
    for name in os.listdir(media_dir):
        if name.endswith(".en.vtt"):
            vtt_path = os.path.join(media_dir, name)
            break

    # If no uploaded captions, try auto-generated
    if not vtt_path:
        try:
            result = subprocess.run(
                [
                    "yt-dlp", "--write-auto-sub", "--sub-lang", "en",
                    "--skip-download", "--sub-format", "vtt",
                    "--output", vtt_output, url,
                ],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.info("link %d: no auto-captions (yt-dlp: %s)",
                            link_id, result.stderr.strip()[:200] or "no subtitles")
        except subprocess.TimeoutExpired:
            logger.warning("link %d: yt-dlp auto-caption fetch timed out", link_id)

        for name in os.listdir(media_dir):
            if name.endswith(".en.vtt"):
                vtt_path = os.path.join(media_dir, name)
                break

    if not vtt_path:
        return None

    # Convert VTT to plain text
    text = _vtt_to_text(vtt_path)
    if len(text) < 50:
        logger.info("link %d: subtitles too short (%d chars), will use Whisper", link_id, len(text))
        return None

    # Write plain text transcript
    transcript_path = os.path.join(media_dir, "transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(text)

    logger.info("link %d: fetched subtitles (%d chars) — skipping Whisper", link_id, len(text))
    return transcript_path


def process_video(
    link_id: int,
    url: str,
    content_type: str,
    media_dir: str,
    context: str,
    db_path: str,
) -> None:
    """Download video with yt-dlp, transcribe with subtitles or Whisper.

    Tries to fetch existing subtitles (uploaded, then auto-generated)
    before falling back to audio download + Whisper transcription.
    """
    logger.info("link %d: downloading video from %s", link_id, url)

    # Step 1: Fetch rich metadata before downloading
    yt_metadata = {}
    try:
        meta_result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=60,
        )
        if meta_result.returncode == 0:
            yt_metadata = json.loads(meta_result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        logger.warning("link %d: metadata fetch failed (non-fatal): %s", link_id, exc)

    # If yt-dlp couldn't get metadata (DRM, etc.), try scraping the page
    if not yt_metadata.get("title"):
        try:
            html = _fetch_page(url)
            if html:
                # Extract title from og:title or <title>
                og_match = re.search(
                    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                    html, re.IGNORECASE,
                )
                if not og_match:
                    og_match = re.search(
                        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
                        html, re.IGNORECASE,
                    )
                if og_match:
                    yt_metadata["title"] = og_match.group(1).strip()
                    logger.info("link %d: scraped title from page: '%s'",
                                link_id, yt_metadata["title"])
        except Exception as exc:
            logger.debug("link %d: page scrape for title failed: %s", link_id, exc)

    # Step 2: Try to fetch a transcript (cheapest sources first)
    transcript_path = None
    rss_audio_url = None  # fallback audio URL from RSS feed

    # 2a: Scrape transcript directly from the episode page (cheapest)
    if content_type == "podcast":
        try:
            transcript_path = _try_scrape_page_transcript(url, media_dir, link_id)
        except Exception as exc:
            logger.info("link %d: page transcript scrape failed (non-fatal): %s", link_id, exc)

    # 2b: Podcast RSS transcript tag
    if not transcript_path and content_type == "podcast":
        try:
            transcript_path, rss_audio_url = _try_fetch_podcast_transcript(
                url, media_dir, link_id, yt_metadata,
            )
        except Exception as exc:
            logger.info("link %d: podcast transcript fetch failed (non-fatal): %s", link_id, exc)

    # 2c: yt-dlp subtitles (uploaded captions, then auto-generated)
    if not transcript_path:
        try:
            transcript_path = _try_fetch_subtitles(url, media_dir, link_id)
        except Exception as exc:
            logger.info("link %d: subtitle fetch failed (non-fatal): %s", link_id, exc)

    # Step 3: Download audio only if we need Whisper
    audio_file = None
    if not transcript_path:
        result = subprocess.run(
            [
                "yt-dlp",
                "--extract-audio",
                "--audio-format", "m4a",
                "--output", os.path.join(media_dir, "%(title)s.%(ext)s"),
                url,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            # yt-dlp failed (DRM, geo-block, etc.) — try RSS audio URL fallback
            if rss_audio_url:
                logger.info("link %d: yt-dlp failed, trying RSS audio URL: %s",
                            link_id, rss_audio_url[:80])
                audio_filename = sanitize_title(
                    yt_metadata.get("title") or "episode"
                ) + ".mp3"
                audio_path = os.path.join(media_dir, audio_filename)
                dl_result = subprocess.run(
                    ["curl", "-sL", "--max-time", "300", "-o", audio_path, rss_audio_url],
                    capture_output=True, text=True,
                )
                if dl_result.returncode == 0 and os.path.exists(audio_path):
                    audio_file = audio_path
                    logger.info("link %d: downloaded audio via RSS fallback", link_id)
                else:
                    raise RuntimeError(
                        f"yt-dlp failed ({result.stderr.strip()[:200]}) "
                        f"and RSS audio fallback also failed"
                    )
            else:
                raise RuntimeError(f"yt-dlp failed: {result.stderr[:500]}")

        if not audio_file:
            for name in os.listdir(media_dir):
                if name.endswith((".m4a", ".mp3", ".wav", ".opus", ".webm")):
                    audio_file = os.path.join(media_dir, name)
                    break

        if not audio_file:
            raise RuntimeError("audio download succeeded but no audio file found in media_dir")

    # Derive video title from downloaded file or metadata
    video_title = ""
    if audio_file:
        video_title = os.path.splitext(os.path.basename(audio_file))[0]
    elif yt_metadata.get("title"):
        video_title = yt_metadata["title"]

    # Detect platform from URL domain
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""
    platform = "unknown"
    for name, domains in [
        ("TikTok", ("tiktok.com",)),
        ("YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
        ("Instagram", ("instagram.com",)),
        ("X/Twitter", ("x.com", "twitter.com")),
        ("Vimeo", ("vimeo.com",)),
        ("Facebook", ("facebook.com",)),
    ]:
        if any(domain.endswith(d) for d in domains):
            platform = name
            break

    # Build rich metadata from yt-dlp output
    metadata = {
        "url": url,
        "title": yt_metadata.get("title") or video_title,
        "content_type": content_type,
        "platform": platform,
        "creator": yt_metadata.get("uploader") or yt_metadata.get("channel") or yt_metadata.get("creator") or "",
        "creator_url": yt_metadata.get("uploader_url") or yt_metadata.get("channel_url") or "",
        "description": yt_metadata.get("description") or "",
        "upload_date": yt_metadata.get("upload_date") or "",  # YYYYMMDD format
        "duration": yt_metadata.get("duration") or 0,  # seconds
        "duration_string": yt_metadata.get("duration_string") or "",
        "view_count": yt_metadata.get("view_count") or 0,
        "like_count": yt_metadata.get("like_count") or 0,
        "comment_count": yt_metadata.get("comment_count") or 0,
        "thumbnail": yt_metadata.get("thumbnail") or "",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path = os.path.join(media_dir, "metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    update_status(link_id=link_id, status="downloading",
                  download_path=audio_file or "", db_path=db_path)

    # Step 4: Transcribe with Whisper if no subtitles were found
    if not transcript_path:
        prompt_arg = context if context else ""
        whisper_cmd = [WHISPER_SCRIPT, audio_file]
        if prompt_arg:
            whisper_cmd = [WHISPER_SCRIPT, "--prompt", prompt_arg, audio_file]

        result = subprocess.run(whisper_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"whisper-transcribe failed: {result.stderr[:500]}")

        transcript_path = _find_transcript(media_dir)
        if not transcript_path:
            raise RuntimeError("Whisper ran but no .txt transcript found in media_dir tree")

    update_status(
        link_id=link_id,
        status="transcribed",
        transcript_path=transcript_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_video", "success",
                   f"transcript: {transcript_path}", db_path)
    logger.info("link %d: video transcribed to %s", link_id, transcript_path)


# ---------------------------------------------------------------------------
# Audio handler
# ---------------------------------------------------------------------------

def process_audio(
    link_id: int,
    url: str,
    media_dir: str,
    context: str,
    db_path: str,
) -> None:
    """Download audio with curl, transcribe with Whisper."""
    logger.info("link %d: downloading audio from %s", link_id, url)

    filename = sanitize_title(url.split("/")[-1].split("?")[0]) or "audio.mp3"
    audio_path = os.path.join(media_dir, filename)

    result = subprocess.run(
        ["curl", "-sL", "--max-time", "300", "-o", audio_path, url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl audio download failed: {result.stderr[:500]}")

    update_status(link_id=link_id, status="downloading",
                  download_path=audio_path, db_path=db_path)

    # Transcribe
    prompt_arg = context if context else ""
    whisper_cmd = [WHISPER_SCRIPT, audio_path]
    if prompt_arg:
        whisper_cmd = [WHISPER_SCRIPT, "--prompt", prompt_arg, audio_path]

    result = subprocess.run(whisper_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"whisper-transcribe failed: {result.stderr[:500]}")

    transcript_path = _find_transcript(media_dir)
    if not transcript_path:
        raise RuntimeError("Whisper ran but no .txt transcript found in media_dir tree")

    update_status(
        link_id=link_id,
        status="transcribed",
        transcript_path=transcript_path,
        db_path=db_path,
    )
    log_processing(link_id, "process_audio", "success",
                   f"transcript: {transcript_path}", db_path)
    logger.info("link %d: audio transcribed to %s", link_id, transcript_path)


# ---------------------------------------------------------------------------
# Stale claim recovery
# ---------------------------------------------------------------------------

def recover_stale_claims(db_path: str, stale_minutes: int = 30) -> None:
    """Reset links stuck mid-processing back to pending."""
    stale_statuses = ("downloading", "summarizing", "archiving")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    cutoff_str = cutoff.isoformat()

    conn = get_connection(db_path)
    try:
        placeholders = ",".join("?" for _ in stale_statuses)
        cursor = conn.execute(
            f"SELECT id, status, updated_at FROM links "
            f"WHERE status IN ({placeholders}) AND updated_at < ?",
            (*stale_statuses, cutoff_str),
        )
        stale_rows = cursor.fetchall()
        for row in stale_rows:
            conn.execute(
                "UPDATE links SET status = 'pending', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )
            logger.warning(
                "link %d: reset from stale status '%s' (last updated %s)",
                row["id"], row["status"], row["updated_at"],
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(db_path: str) -> None:
    """Claim and process pending links, routing by content type."""
    init_db(db_path)
    recover_stale_claims(db_path)

    pending = get_pending(status="pending", limit=20, db_path=db_path)
    logger.info("found %d pending link(s)", len(pending))

    for link in pending:
        link_id = link["id"]
        url = link["url"]
        content_type = link.get("content_type") or classify_url(url)
        context = link.get("context") or ""

        claimed = claim_link(link_id, from_status="pending",
                             to_status="downloading", db_path=db_path)
        if not claimed:
            logger.info("link %d: already claimed by another worker, skipping", link_id)
            continue

        logger.info("link %d: processing %s (%s)", link_id, url, content_type)

        try:
            if content_type == "web_page":
                title, content = fetch_web_content(url)
                mdir = media_dir_for(title)
                process_web_page(link_id, url, content, title, mdir, db_path)

            elif content_type in ("youtube", "social_video", "podcast"):
                # Podcasts use yt-dlp too — it handles Apple Podcasts,
                # Spotify, and other platform pages.  Direct audio URLs
                # are classified as "audio" instead.
                mdir = media_dir_for(sanitize_title(url))
                process_video(link_id, url, content_type, mdir, context, db_path)

            elif content_type == "audio":
                mdir = media_dir_for(sanitize_title(url))
                process_audio(link_id, url, mdir, context, db_path)

            elif content_type == "image":
                link_meta = json.loads(link.get("metadata") or "{}")
                ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                mdir = media_dir_for(ts)
                process_image(link_id, mdir, link_meta, context, ts, db_path)

            else:
                # Unknown — treat as web page
                logger.warning("link %d: unknown content_type '%s', treating as web_page",
                               link_id, content_type)
                title, content = fetch_web_content(url)
                mdir = media_dir_for(title)
                process_web_page(link_id, url, content, title, mdir, db_path)

        except Exception as exc:
            error_msg = str(exc)
            logger.error("link %d: error — %s", link_id, error_msg)

            # Re-read current retry_count from DB
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT retry_count FROM links WHERE id = ?", (link_id,)
                ).fetchone()
                current_retries = row["retry_count"] if row else 0
            finally:
                conn.close()

            new_retries = current_retries + 1
            if new_retries < MAX_RETRIES:
                update_status(
                    link_id=link_id,
                    status="pending",
                    retry_count=new_retries,
                    error=error_msg,
                    db_path=db_path,
                )
                logger.warning("link %d: retry %d/%d", link_id, new_retries, MAX_RETRIES)
            else:
                update_status(
                    link_id=link_id,
                    status="failed",
                    retry_count=new_retries,
                    error=error_msg,
                    db_path=db_path,
                )
                logger.error("link %d: max retries reached, marked failed", link_id)

            log_processing(link_id, "processor", "error", error_msg, db_path)


if __name__ == "__main__":
    from db import DB_PATH
    run(DB_PATH)
