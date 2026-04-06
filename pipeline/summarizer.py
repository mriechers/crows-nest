"""
Summarizer for the Crow's Nest pipeline.

Stage 3: reads transcripts, calls Claude via cli-agent-http for structured
summaries, and writes Obsidian notes to 0 - INBOX/Clippings/ with
vault-convention frontmatter.
"""

import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timezone

from config import OBSIDIAN_CLIPPINGS, OBSIDIAN_ARCHIVE, OBSIDIAN_VAULT
from db import get_pending, claim_link, update_status, log_processing
from utils import sanitize_title, setup_logging
from keychain_secrets import get_secret

logger = setup_logging("crows-nest.summarizer")

CONTENT_TYPE_TAG_MAP = {
    "youtube": "video-clip",
    "podcast": "audio-clip",
    "social_video": "video-clip",
    "audio": "audio-clip",
    "web_page": "web-clip",
    "image": "image-clip",
}

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Tag sanitization helper
# ---------------------------------------------------------------------------

def _sanitize_tag(tag: str) -> str:
    """Lowercase, replace anything not alphanumeric or hyphen with a hyphen,
    then strip leading/trailing hyphens and collapse runs."""
    tag = tag.lower()
    tag = re.sub(r"[^a-z0-9-]", "-", tag)
    tag = re.sub(r"-+", "-", tag)
    return tag.strip("-")


# ---------------------------------------------------------------------------
# Frontmatter builder
# ---------------------------------------------------------------------------

def build_frontmatter(
    title: str,
    source: str,
    content_type: str,
    tags: list,
    sender: str = None,
    metadata: dict = None,
) -> str:
    """Build YAML frontmatter matching Obsidian vault conventions.

    Always includes para: inbox and tags starting with 'all'.
    Includes via/sender only when sender is provided.
    Includes platform and creator from metadata when available.
    """
    metadata = metadata or {}
    type_tag = CONTENT_TYPE_TAG_MAP.get(content_type, "web-clip")
    topic_tags = [_sanitize_tag(t) for t in tags if t]

    all_tags = ["all", "clippings", type_tag, "inbox-capture"] + topic_tags
    tag_lines = "\n".join(f"  - {t}" for t in all_tags)

    created = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Quote title to handle colons and special YAML chars
    safe_title = title.replace('"', '\\"')
    lines = [
        "---",
        f'title: "{safe_title}"',
        f"source: {source}",
        f"created: {created}",
        f"content-type: {content_type}",
    ]

    if metadata.get("platform"):
        lines.append(f"platform: {metadata['platform']}")
    if metadata.get("creator"):
        safe_creator = metadata['creator'].replace('"', '\\"')
        lines.append(f'creator: "{safe_creator}"')
    if metadata.get("upload_date"):
        # Convert YYYYMMDD to YYYY-MM-DD
        ud = metadata["upload_date"]
        if len(ud) == 8 and ud.isdigit():
            ud = f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}"
        lines.append(f"published: {ud}")

    if metadata.get("image_count"):
        lines.append(f"image-count: {metadata['image_count']}")

    if sender:
        lines.append("via: signal")
        lines.append(f'sender: "{sender}"')

    lines.append("para: inbox")
    lines.append("tags:")
    lines.append(tag_lines)
    lines.append("---")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Note body generator
# ---------------------------------------------------------------------------

def generate_note_content(
    title: str,
    source_url: str,
    content_type: str,
    summary: str,
    key_points: list,
    transcript_text: str,
    metadata: dict = None,
    notable_quotes: list = None,
    people: list = None,
    related_links: list = None,
    followups: list = None,
    sender: str = None,
    saved_at: str = None,
    extracted_text: str = None,
) -> str:
    """Build the Markdown body for an Obsidian clipping note."""
    metadata = metadata or {}
    sections = []

    # Shared via callout (separate from content subjects)
    if sender:
        saved_date = saved_at or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sections.append(f"> [!info] Shared via Signal\n> Sent by **{sender}** on {saved_date}")

    # Image embeds — inserted between sender callout and summary
    if content_type == "image":
        vault_filenames = metadata.get("vault_filenames", [])
        if vault_filenames:
            embed_lines = "\n".join(f"![[{fn}]]" for fn in vault_filenames)
            sections.append(embed_lines)

    # Summary callout
    sections.append(f"> [!summary]\n> {summary}")

    # Extracted text section (images only, when non-empty)
    if content_type == "image" and extracted_text:
        sections.append(f"## Extracted Text\n\n{extracted_text}")

    # Key points
    if key_points:
        bullet_lines = "\n".join(f"- {pt}" for pt in key_points)
        sections.append(f"## Key Points\n\n{bullet_lines}")

    # Optional: Notable Quotes
    if notable_quotes:
        quote_lines = "\n".join(f"> {q}" for q in notable_quotes)
        sections.append(f"## Notable Quotes\n\n{quote_lines}")

    # Optional: People Mentioned
    if people:
        people_lines = "\n".join(f"- {p}" for p in people)
        sections.append(f"## People Mentioned\n\n{people_lines}")

    # Optional: Related Links
    if related_links:
        link_lines = "\n".join(f"- {rl}" for rl in related_links)
        sections.append(f"## Related Links\n\n{link_lines}")

    # Optional: Follow-Up Ideas
    if followups:
        followup_lines = "\n".join(f"- [ ] {f}" for f in followups)
        sections.append(f"## Follow-Up Ideas\n\n{followup_lines}")

    # --- Bibliographic Source Details ---
    platform = metadata.get("platform") or ""
    creator = metadata.get("creator") or ""
    creator_url = metadata.get("creator_url") or ""
    description = metadata.get("description") or ""
    upload_date = metadata.get("upload_date") or ""
    duration_str = metadata.get("duration_string") or ""
    duration_secs = metadata.get("duration") or 0
    view_count = metadata.get("view_count") or 0
    processed_at = metadata.get("processed_at") or ""

    # Format upload date
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

    # Format duration if we have seconds but no string
    if duration_secs and not duration_str:
        mins, secs = divmod(int(duration_secs), 60)
        hrs, mins = divmod(mins, 60)
        duration_str = f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}"

    source_lines = [f"- **Title**: {title}"]

    if creator:
        if creator_url:
            source_lines.append(f"- **Creator**: [{creator}]({creator_url})")
        else:
            source_lines.append(f"- **Creator**: {creator}")

    if platform:
        source_lines.append(f"- **Platform**: {platform}")

    if content_type == "image":
        image_count = metadata.get("image_count") or len(metadata.get("vault_filenames", []))
        source_lines.append(f"- **Type**: Image ({image_count} image{'s' if image_count != 1 else ''})")
    else:
        source_lines.append(f"- **Original URL**: {source_url}")

    if upload_date:
        source_lines.append(f"- **Published**: {upload_date}")

    if duration_str:
        source_lines.append(f"- **Duration**: {duration_str}")

    if view_count:
        source_lines.append(f"- **Views**: {view_count:,}")

    if processed_at:
        source_lines.append(f"- **Captured**: {processed_at[:10]}")

    if sender:
        saved_date = saved_at or ""
        source_lines.append(f"- **Shared via**: Signal from {sender}" +
                           (f" ({saved_date})" if saved_date else ""))

    if description:
        # Truncate long descriptions
        desc_preview = description[:300]
        if len(description) > 300:
            desc_preview += "..."
        source_lines.append(f"\n**Description**:\n> {desc_preview}")

    sections.append("---\n\n## Source Details\n\n" + "\n".join(source_lines))

    # Transcript (collapsed) — skipped for image content
    # Full transcript preserved; the <details> block handles readability.
    if transcript_text and content_type != "image":
        sections.append(
            f"<details><summary>Full Transcript</summary>\n\n"
            f"{transcript_text}\n\n</details>"
        )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

def call_claude_for_summary(content: str, content_type: str, title: str = "",
                            sender: str = None, creator: str = None) -> dict:
    """Call Claude via OpenRouter API for structured content analysis.

    Extracts: summary, key points, topic tags, notable quotes, people
    mentioned, links/resources referenced, and follow-up ideas.

    Uses OPENROUTER_API_KEY from macOS Keychain. Never raises — returns
    a safe fallback dict on any failure.
    """
    fallback = {
        "title": title or "untitled",
        "summary": f"Content from {content_type}: {title or 'untitled'}",
        "key_points": [],
        "tags": [],
        "notable_quotes": [],
        "people": [],
        "related_links": [],
        "followups": [],
    }

    api_key = get_secret("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not found in Keychain, using fallback")
        return fallback

    system_prompt = """You are a research assistant that analyzes content and extracts structured information. You always respond with valid JSON only, no markdown fencing, no explanation. CRITICAL: All string values in your JSON must have internal double quotes escaped as \\". Never use unescaped " inside JSON string values."""

    # Build context about sender vs creator
    sender_note = ""
    if sender:
        sender_note = f"\n\nIMPORTANT: This content was shared via Signal by '{sender}'. They are NOT the creator of this content — do NOT list them under people mentioned. They are the person who shared/forwarded this link."
    if creator:
        sender_note += f"\nThe content creator is '{creator}'."

    user_prompt = f"""Analyze this {content_type} transcript and return a JSON object with these exact keys:

- "summary": 2-4 sentence summary capturing the main ideas and why someone would find this valuable
- "key_points": array of 3-7 specific, actionable takeaways as bullet-point strings (not vague — include names, tools, numbers when mentioned)
- "tags": array of 3-7 lowercase hyphenated topic tags (e.g., "claude-code", "home-lab", "ai-tools")
- "notable_quotes": array of up to 3 direct quotes worth saving, formatted as: quote text — Speaker Name (do NOT use nested double quotes inside the JSON string values; use single quotes if needed)
- "people": array of "**Name** — role/relevance" strings for people mentioned IN THE CONTENT (or empty array). Do NOT include the person who shared the link.
- "related_links": array of any URLs, tools, products, repos, or named resources mentioned that someone might want to look up, formatted as "Name or description — context" (or empty array)
- "followups": array of 1-3 actionable next steps or things to investigate further (or empty array)
- "title": a clear, descriptive title for this content (5-10 words, like a good article headline — NOT the sender's name or "From Signal"){sender_note}

Title hint: {title}

Content:
{content[:12000]}"""

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            OPENROUTER_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/crows-nest-pipeline/crows-nest",
                "X-Title": "Crow's Nest Pipeline",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        logger.warning("openrouter api call failed: %s", exc)
        return fallback

    # Parse OpenRouter response: {"choices": [{"message": {"content": "..."}}]}
    try:
        response = json.loads(raw)
        assistant_text = response["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        logger.warning("unexpected openrouter response format: %s — raw: %s", exc, raw[:300])
        return fallback

    logger.debug("assistant response length: %d chars", len(assistant_text))

    # Extract JSON from assistant's response
    parsed = _extract_json(assistant_text)
    if parsed is None:
        # Dump full response for debugging
        debug_path = "/tmp/crows-nest-json-debug.txt"
        with open(debug_path, "w") as df:
            df.write(assistant_text)
        logger.warning("could not parse JSON from response (len=%d), dumped to %s",
                       len(assistant_text), debug_path)
        return fallback

    # Merge parsed into fallback so missing keys stay safe
    result = dict(fallback)
    result.update({k: v for k, v in parsed.items() if k in fallback})
    logger.info("claude analysis complete: %d key points, %d tags",
                len(result.get("key_points", [])), len(result.get("tags", [])))
    return result


# ---------------------------------------------------------------------------
# Claude vision API call for images
# ---------------------------------------------------------------------------

def call_claude_for_image_analysis(
    image_paths: list,
    context: str = "",
    sender: str = None,
) -> dict:
    """Call Claude Haiku via OpenRouter's OpenAI-compatible vision API.

    Reads images from OBSIDIAN_ARCHIVE (the resized copies), base64-encodes
    them, and sends an adaptive prompt that either extracts text verbatim
    (for screenshots/signs/articles) or describes the image (for photos/
    diagrams).

    Returns same dict structure as call_claude_for_summary plus extracted_text.
    Never raises — returns a safe fallback dict on any failure.
    """
    n = len(image_paths)
    fallback = {
        "title": "Image Analysis",
        "summary": f"Analysis of {n} image{'s' if n != 1 else ''}.",
        "extracted_text": "",
        "key_points": [],
        "tags": [],
        "people": [],
        "followups": [],
    }

    api_key = get_secret("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not found in Keychain, using fallback")
        return fallback

    system_prompt = """You are a vision analysis assistant. You analyze images and extract structured information. You always respond with valid JSON only, no markdown fencing, no explanation. CRITICAL: All string values in your JSON must have internal double quotes escaped as \\". Never use unescaped " inside JSON string values."""

    sender_note = ""
    if sender:
        sender_note = f"\n\nThis image was shared via Signal by '{sender}'. Do NOT list them under people — they are the person who shared it, not a subject of the image."
    if context:
        sender_note += f"\n\nContext from sender: {context}"

    prompt = f"""Analyze these {n} image(s). Determine whether each is text-heavy (screenshot, article, code, sign, receipt) or visual (infographic, diagram, photo).

For text-heavy images: extract ALL readable text verbatim, preserving layout where possible.
For visual images: describe what the image shows in detail.
{sender_note}

Return a JSON object with these exact keys:
- "title": descriptive title (5-10 words)
- "summary": 2-4 sentence description of what these images contain and why they're notable
- "extracted_text": all readable text from the images combined (empty string if no text)
- "key_points": array of 3-7 key takeaways
- "tags": array of 3-7 lowercase hyphenated topic tags
- "people": array of people mentioned IN the images (not the sender)
- "followups": array of 1-3 follow-up actions"""

    # Build content blocks: images first, then text prompt
    content_blocks = []
    loaded_count = 0
    for path in image_paths:
        if not os.path.exists(path):
            logger.warning("image path not found for vision analysis: %s", path)
            continue
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            ext = os.path.splitext(path)[1].lower()
            media_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(ext, "image/jpeg")
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            })
            loaded_count += 1
        except Exception as exc:
            logger.warning("could not read image %s: %s", path, exc)

    if loaded_count == 0:
        logger.warning("no images could be loaded for vision analysis, using fallback")
        return fallback

    content_blocks.append({"type": "text", "text": prompt})

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_blocks},
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            OPENROUTER_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/crows-nest-pipeline/crows-nest",
                "X-Title": "Crow's Nest Pipeline",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        logger.warning("openrouter vision api call failed: %s", exc)
        return fallback

    try:
        response = json.loads(raw)
        assistant_text = response["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        logger.warning("unexpected openrouter vision response format: %s — raw: %s", exc, raw[:300])
        return fallback

    logger.debug("vision assistant response length: %d chars", len(assistant_text))

    parsed = _extract_json(assistant_text)
    if parsed is None:
        debug_path = "/tmp/crows-nest-vision-debug.txt"
        with open(debug_path, "w") as df:
            df.write(assistant_text)
        logger.warning("could not parse JSON from vision response (len=%d), dumped to %s",
                       len(assistant_text), debug_path)
        return fallback

    result = dict(fallback)
    result.update({k: v for k, v in parsed.items() if k in fallback})
    logger.info("claude vision analysis complete: %d key points, %d tags, extracted_text=%d chars",
                len(result.get("key_points", [])), len(result.get("tags", [])),
                len(result.get("extracted_text", "")))
    return result


# ---------------------------------------------------------------------------
# Creator enrichment — web search for unnamed artifacts
# ---------------------------------------------------------------------------

# Tags (or tag components) that suggest a specific nameable artifact
_ARTIFACT_CATEGORIES = {
    "book": "book",
    "film": "film",
    "movie": "movie",
    "album": "album",
    "game": "game",
    "product": "product",
    "app": "app",
    "podcast": "podcast",
    "documentary": "documentary",
}


def _detect_artifact_category(tags: list[str]) -> str | None:
    """Check if any topic tag contains an artifact category keyword."""
    for tag in tags:
        parts = tag.split("-")
        for part in parts:
            # Simple plural stemming
            stem = part[:-1] if part.endswith("s") and len(part) > 3 else part
            if stem in _ARTIFACT_CATEGORIES:
                return _ARTIFACT_CATEGORIES[stem]
            if part in _ARTIFACT_CATEGORIES:
                return _ARTIFACT_CATEGORIES[part]
    return None


def enrich_with_creator_search(
    claude_result: dict,
    creator: str,
    metadata: dict = None,
) -> dict:
    """Search for a creator's specific artifact when the summary is vague.

    Fires when: there's a known creator AND tags suggest a category artifact
    (book, film, album, etc.). Adds top search results to related_links.

    Modifies claude_result in place and returns it.
    """
    if not creator:
        return claude_result

    tags = claude_result.get("tags", [])
    category = _detect_artifact_category(tags)
    if not category:
        return claude_result

    # Build search query
    query = f"{creator} {category}"
    logger.info("enrichment search: %s", query)

    try:
        # DuckDuckGo lite HTML — lightweight, no API key needed
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded_query}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "CrowsNest/1.0 (content-pipeline)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract result titles and URLs from DDG lite HTML
        # Format: <a rel="nofollow" href="URL" class='result-link'>Title</a>
        results = re.findall(
            r"<a[^>]+class='result-link'[^>]*href=['\"]([^'\"]+)['\"][^>]*>([^<]+)</a>",
            html,
        )
        if not results:
            # Try alternate pattern (href before class)
            results = re.findall(
                r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*class='result-link'[^>]*>([^<]+)</a>",
                html,
            )

        if results:
            existing_links = claude_result.get("related_links", []) or []
            added = 0
            for link_url, link_title in results[:6]:
                link_title = link_title.strip()

                # Resolve DDG redirect URLs to actual destination
                if "duckduckgo.com/l/" in link_url:
                    parsed_qs = urllib.parse.parse_qs(
                        urllib.parse.urlparse(link_url).query
                    )
                    uddg = parsed_qs.get("uddg", [None])[0]
                    if uddg:
                        link_url = urllib.parse.unquote(uddg)

                # Ensure URL has a scheme
                if link_url.startswith("//"):
                    link_url = "https:" + link_url

                # Skip ads, social profiles, and DDG internal links
                if any(skip in link_url for skip in [
                    "tiktok.com", "instagram.com", "twitter.com", "x.com",
                    "duckduckgo.com/y.js", "amazon.com/s?",
                    "bing.com/aclick",
                ]):
                    continue
                entry = f"{link_title} — {link_url}"
                if entry not in existing_links:
                    existing_links.append(entry)
                    added += 1
                if added >= 2:
                    break

            claude_result["related_links"] = existing_links
            if added:
                logger.info("enrichment: added %d reference link(s) for '%s %s'",
                            added, creator, category)
                # Refine title now that we have artifact context
                _refine_title_with_enrichment(claude_result)
        else:
            logger.debug("enrichment: no results found for '%s'", query)

    except Exception as exc:
        # Enrichment is best-effort — never block the pipeline
        logger.warning("enrichment search failed (non-fatal): %s", exc)

    return claude_result


def _refine_title_with_enrichment(claude_result: dict) -> dict:
    """Ask Haiku to refine the note title using enrichment context.

    Only called when enrich_with_creator_search added new related_links.
    Modifies claude_result["title"] in place.
    """
    api_key = get_secret("OPENROUTER_API_KEY")
    if not api_key:
        return claude_result

    current_title = claude_result.get("title", "")
    links = claude_result.get("related_links", [])
    summary = claude_result.get("summary", "")

    prompt = (
        f"Current title: {current_title}\n"
        f"Summary: {summary}\n"
        f"Reference links found:\n"
        + "\n".join(f"  - {l}" for l in links) +
        f"\n\nThese reference links were found by searching for the creator's work. "
        f"One of these links likely contains the SPECIFIC NAME of the book, film, "
        f"album, product, or other work discussed in the content. Look for a link "
        f"from a retailer, store, Wikipedia, or official site — those tend to have "
        f"the actual work title (e.g. 'The ADHD Field Guide for Adults' from Barnes "
        f"& Noble, not a site tagline). Ignore generic site descriptions.\n\n"
        f"Revise the title to include that specific work name. Examples:\n"
        f"- 'Book Release Day' + 'The ADHD Field Guide|Barnes & Noble' → "
        f"'ADHD Field Guide Release Day — Childhood Library Full Circle'\n"
        f"- 'New Horror Game Revealed' + 'Horda - Steam' → "
        f"'Horda — Darkwood Creator Reveals New Horror Game'\n\n"
        f"Keep it 5-12 words. Return ONLY the revised title, nothing else."
    )

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 60,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            OPENROUTER_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/crows-nest-pipeline/crows-nest",
                "X-Title": "Crow's Nest Pipeline",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")

        response = json.loads(raw)
        refined = response["choices"][0]["message"]["content"].strip().strip('"')

        if refined and refined != current_title:
            logger.info("title refined: '%s' -> '%s'", current_title, refined)
            claude_result["title"] = refined
        else:
            logger.debug("title unchanged after refinement")

    except Exception as exc:
        logger.warning("title refinement failed (non-fatal): %s", exc)

    return claude_result


def _extract_json(text: str) -> dict | None:
    """Try multiple strategies to extract a JSON object from text."""
    # Strip leading/trailing whitespace and any BOM/control chars
    text = text.strip().lstrip("\ufeff")

    # Remove any invisible Unicode characters that can break JSON parsing
    # (zero-width spaces, soft hyphens, etc.)
    text = re.sub(r'[\u200b-\u200f\u2028-\u202f\u00ad\ufeff]', '', text)

    # Strategy 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: code fence extraction
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: find the outermost {...} block via balanced brace matching
    start = text.find('{')
    if start >= 0:
        # Find the matching closing brace
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict):
                            return result
                    except (json.JSONDecodeError, TypeError):
                        break

    # Strategy 4: repair common JSON issues (unescaped quotes in values)
    # Replace smart/curly quotes with escaped straight quotes
    repaired = text.replace('\u201c', '\\"').replace('\u201d', '\\"')
    repaired = repaired.replace('\u2018', "'").replace('\u2019', "'")
    if repaired != text:
        try:
            result = json.loads(repaired)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 5: greedy regex fallback
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# ---------------------------------------------------------------------------
# Note writer
# ---------------------------------------------------------------------------

def write_obsidian_note(title: str, frontmatter: str, body: str) -> str:
    """Write a note to the Obsidian clippings directory.

    Handles filename collisions with (1), (2) suffixes.
    Returns the absolute file path.
    """
    os.makedirs(OBSIDIAN_CLIPPINGS, exist_ok=True)

    safe_title = sanitize_title(title)
    if not safe_title:
        safe_title = "untitled"

    base_path = os.path.join(OBSIDIAN_CLIPPINGS, f"{safe_title}.md")
    file_path = base_path

    counter = 1
    while os.path.exists(file_path):
        file_path = os.path.join(OBSIDIAN_CLIPPINGS, f"{safe_title} ({counter}).md")
        counter += 1

    note_content = f"{frontmatter}\n\n{body}\n"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(note_content)

    logger.info("wrote obsidian note: %s", file_path)
    return file_path


# --- Weekly Links Log ---

# Ordered tag-to-category rules. First matching rule wins.
# Each rule is (set_of_tags, category_name).
TAG_CATEGORY_RULES: list[tuple[set[str], str]] = [
    (
        {"marathon-game", "marathon-guide", "marathon-solo", "marathon-farming"},
        "Gaming",
    ),
    (
        {"gaming-tips", "pvp-strategy", "extraction-games", "game-mechanics",
         "indie-games", "character-builds", "speedrun"},
        "Gaming",
    ),
    (
        {"claude-code", "ai-agents", "mcp-server", "prompt-engineering",
         "llm-tools", "context-engineering", "workflow-automation",
         "developer-tools", "ai-automation", "harness-engineering",
         "browser-automation", "local-llm", "ai-workflows", "coding-agents"},
        "AI & Dev Tools",
    ),
    (
        {"horror-film", "horror-movies", "psychological-horror", "analog-horror",
         "found-footage", "cosmic-horror", "supernatural-horror", "horror-games",
         "movie-review", "film-review", "movie-recommendation", "thriller",
         "horror-nostalgia", "horror-content"},
        "Horror & Film",
    ),
    (
        {"career-coaching", "burnout-recovery", "professional-development",
         "leadership", "workplace-culture", "management-leadership",
         "employee-engagement", "toxic-workplace", "executive-education"},
        "Work & Leadership",
    ),
    (
        {"activism", "surveillance-capitalism", "ai-ethics", "corporate-lobbying",
         "regulatory-capture", "government-technology", "digital-rights",
         "content-moderation", "deepfake", "ai-satire", "privatization"},
        "Politics & Society",
    ),
    (
        {"relationships", "personal-growth", "self-care", "philosophy",
         "self-love", "existentialism", "communication-skills",
         "emotional-intelligence", "heartbreak", "dating-advice"},
        "Personal Growth",
    ),
    (
        {"3d-printing", "self-hosting", "open-source", "single-board-computer",
         "home-lab", "video-codec", "web-development", "right-to-repair",
         "iphone-customization", "open-source-hardware"},
        "Tech & Hardware",
    ),
    (
        {"home-cleaning", "desk-organization", "phone-accessories",
         "interior-design", "room-divider", "fashion-trends", "workspace-setup",
         "sustainable-products"},
        "Products & Home",
    ),
]

# Content-type fallback for entries with no matching tags.
CONTENT_TYPE_FALLBACK_MAP = {
    "podcast": "News & Current Events",
    "audio": "News & Current Events",
    "image": "Images",
}

WEEKLY_LOG_TEMPLATE = """---
title: "Weekly Links — {week_label}"
created: {created}
week_start: {week_start}
week_end: {week_end}
para: inbox
tags:
  - all
  - weekly-links
  - inbox-capture
---
# Weekly Links — {week_label}

## Other
"""


def categorize_from_tags(
    tags: list[str],
    content_type: str = "web_page",
) -> str:
    """Determine a topic category from a note's tags.

    Checks tags against TAG_CATEGORY_RULES in priority order.
    Falls back to content-type mapping, then "Other".
    """
    tag_set = set(tags)
    for rule_tags, category in TAG_CATEGORY_RULES:
        if tag_set & rule_tags:
            return category
    return CONTENT_TYPE_FALLBACK_MAP.get(content_type, "Other")


def _append_to_weekly_log(
    inbox_dir: str,
    title: str,
    url: str,
    content_type: str,
    source: str,
    tags: list[str] | None = None,
    capture_date: date | None = None,
) -> None:
    """Append an entry to the current week's links log.

    Creates the file from template if it doesn't exist.
    Categorizes the entry by topic tags and appends under the matching
    section header, creating the section dynamically if needed.
    """
    from datetime import date as date_type, timedelta

    if capture_date is None:
        capture_date = date_type.today()

    iso_cal = capture_date.isocalendar()
    week_label = f"{iso_cal.year}-W{iso_cal.week:02d}"

    week_start = capture_date - timedelta(days=capture_date.weekday())
    week_end = week_start + timedelta(days=6)

    filename = f"Weekly Links \u2014 {week_label}.md"
    filepath = os.path.join(inbox_dir, filename)

    if not os.path.exists(filepath):
        content = WEEKLY_LOG_TEMPLATE.format(
            week_label=week_label,
            created=week_start.isoformat(),
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    section = categorize_from_tags(tags or [], content_type)
    entry_line = f"- {capture_date.isoformat()} \u2014 [[{sanitize_title(title)}]] \u00b7 [{content_type}]({url}) \u00b7 via {source}\n"

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    section_header = f"## {section}\n"
    inserted = False
    for i, line in enumerate(lines):
        if line == section_header:
            lines.insert(i + 1, entry_line)
            inserted = True
            break

    if not inserted:
        # Insert new section before "## Other" so Other stays last.
        other_idx = None
        for i, line in enumerate(lines):
            if line == "## Other\n":
                other_idx = i
                break
        if other_idx is not None:
            lines.insert(other_idx, f"\n{section_header}\n")
            # list.insert adds one element regardless of embedded newlines,
            # so the entry goes at other_idx + 1 (right after the header).
            lines.insert(other_idx + 1, entry_line)
        else:
            lines.append(f"\n{section_header}\n")
            lines.append(entry_line)

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(db_path: str) -> None:
    """Claim transcribed links, summarize, and write Obsidian notes."""
    links = get_pending(status="transcribed", limit=5, db_path=db_path)
    logger.info("found %d transcribed link(s)", len(links))

    for link in links:
        link_id = link["id"]
        url = link["url"]
        content_type = link.get("content_type") or "web_page"
        sender = link.get("sender")
        transcript_path = link.get("transcript_path")

        claimed = claim_link(
            link_id, from_status="transcribed", to_status="summarizing", db_path=db_path
        )
        if not claimed:
            logger.info("link %d: already claimed, skipping", link_id)
            continue

        logger.info("link %d: summarizing %s (%s)", link_id, url, content_type)

        try:
            # Read transcript / metadata
            if not transcript_path or not os.path.exists(transcript_path):
                raise RuntimeError(
                    f"transcript file not found: {transcript_path!r}"
                )

            extracted_text = None

            if content_type == "image":
                # For images, transcript_path IS the metadata.json
                with open(transcript_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                transcript_text = ""

                # Build absolute paths to the resized vault copies
                vault_filenames = metadata.get("vault_filenames", [])
                image_paths = [
                    os.path.join(OBSIDIAN_ARCHIVE, fn) for fn in vault_filenames
                ]

                context = metadata.get("context") or link.get("context") or ""
                claude_result = call_claude_for_image_analysis(
                    image_paths=image_paths,
                    context=context,
                    sender=sender,
                )
                extracted_text = claude_result.get("extracted_text", "")

            else:
                with open(transcript_path, "r", encoding="utf-8") as f:
                    transcript_text = f.read()

                # Load metadata if available — check transcript dir and parent
                metadata = {}
                for search_dir in [os.path.dirname(transcript_path),
                                   os.path.dirname(os.path.dirname(transcript_path))]:
                    metadata_path = os.path.join(search_dir, "metadata.json")
                    if os.path.exists(metadata_path):
                        with open(metadata_path, "r", encoding="utf-8") as f:
                            metadata = json.load(f)
                        break

                # Call Claude — pass sender and creator so it can separate them
                creator = metadata.get("creator") or ""
                title_hint = (
                    metadata.get("title")
                    or link.get("context")
                    or sanitize_title(url)
                    or "untitled"
                )
                claude_result = call_claude_for_summary(
                    content=transcript_text,
                    content_type=content_type,
                    title=title_hint,
                    sender=sender,
                    creator=creator,
                )

            # Enrich with creator search for unnamed artifacts
            creator = metadata.get("creator") or ""
            claude_result = enrich_with_creator_search(
                claude_result, creator, metadata
            )

            # Use metadata title for images, AI title otherwise
            title_hint = (
                metadata.get("title")
                or link.get("context")
                or sanitize_title(url)
                or "untitled"
            )

            # Use AI-generated title if available, otherwise fall back
            title = claude_result.get("title") or title_hint

            # Build note
            frontmatter = build_frontmatter(
                title=title,
                source=url,
                content_type=content_type,
                tags=claude_result.get("tags", []),
                sender=sender,
                metadata=metadata,
            )

            body = generate_note_content(
                title=title,
                source_url=url,
                content_type=content_type,
                summary=claude_result.get("summary", ""),
                key_points=claude_result.get("key_points", []),
                transcript_text=transcript_text,
                metadata=metadata,
                notable_quotes=claude_result.get("notable_quotes"),
                people=claude_result.get("people"),
                related_links=claude_result.get("related_links"),
                followups=claude_result.get("followups"),
                sender=sender,
                saved_at=link.get("created_at", "")[:10] if link.get("created_at") else None,
                extracted_text=extracted_text,
            )

            note_path = write_obsidian_note(title, frontmatter, body)
            note_title = os.path.splitext(os.path.basename(note_path))[0]

            try:
                _append_to_weekly_log(
                    inbox_dir=os.path.join(OBSIDIAN_VAULT, "0 - INBOX"),
                    title=note_title,
                    url=link["url"],
                    content_type=link["content_type"] or "web_page",
                    source=link.get("sender") or link.get("source_type") or "unknown",
                    tags=claude_result.get("tags", []),
                )
            except Exception as e:
                logger.warning("Failed to append to weekly log: %s", e)

            update_status(
                link_id=link_id,
                status="summarized",
                obsidian_note_path=note_path,
                db_path=db_path,
            )
            log_processing(
                link_id, "summarizer", "success", f"note: {note_path}", db_path
            )
            logger.info("link %d: summarized -> %s", link_id, note_path)

        except Exception as exc:
            error_msg = str(exc)
            logger.error("link %d: error — %s", link_id, error_msg)
            update_status(
                link_id=link_id,
                status="transcribed",
                error=error_msg,
                db_path=db_path,
            )
            log_processing(link_id, "summarizer", "error", error_msg, db_path)

        # Brief pause between API calls to avoid rate limiting
        if len(links) > 1:
            time.sleep(2)


if __name__ == "__main__":
    from db import DB_PATH
    run(DB_PATH)
