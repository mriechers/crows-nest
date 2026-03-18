#!/usr/bin/env python3.11
"""
Interactive documentation harvester powered by Crawl4AI.

Snapshots documentation sources defined in a sources.json manifest and writes
three artifacts per source: a Markdown file (indexed by the MCP server), the
raw HTML, and a JSON metadata sidecar.

Usage examples:
    python3.11 scripts/crawl_docs.py
    python3.11 scripts/crawl_docs.py --init
    python3.11 scripts/crawl_docs.py --slug help
    python3.11 scripts/crawl_docs.py --category guides --append
    python3.11 scripts/crawl_docs.py --dry-run
    python3.11 scripts/crawl_docs.py --tier 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from crawl4ai import AsyncWebCrawler


# ---------------------------------------------------------------------------
# Sources manifest helpers
# ---------------------------------------------------------------------------


def load_sources(path: Path) -> list[dict]:
    """Load the sources manifest from disk, returning an empty list if absent."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"Expected 'sources' to be a list in {path}")
    return sources


def save_sources(path: Path, sources: list[dict]) -> None:
    """Persist the sources manifest to disk, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": sources}, indent=2), encoding="utf-8")


def slugify(value: str) -> str:
    """Convert an arbitrary string to a URL-safe slug."""
    candidate = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return candidate or "source"


# ---------------------------------------------------------------------------
# Interactive source capture
# ---------------------------------------------------------------------------


def prompt_for_sources(existing: list[dict]) -> list[dict]:
    """Interactively collect new source entries from the user.

    Appends to *existing* and returns the combined list.
    """
    print("Enter documentation sources (leave category blank to finish):")
    captured: list[dict] = []

    while True:
        category = input("  Category (e.g., guides): ").strip()
        if not category:
            break

        url = input("  URL: ").strip()
        if not url:
            print("    URL required; skipping entry.")
            continue

        default_slug = slugify(url.split("/")[-1] or category)
        slug = input(f"  Slug [{default_slug}]: ").strip() or default_slug
        tier_str = input("  Tier [1]: ").strip() or "1"
        notes = input("  Notes (optional): ").strip()

        captured.append(
            {
                "category": category,
                "slug": slug,
                "url": url,
                "tier": int(tier_str),
                "notes": notes,
            }
        )
        print(f"    Added {category}/{slug}")

    if not captured:
        print("No new sources captured.")
        return existing

    return existing + captured


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_sources(
    sources: Iterable[dict],
    categories: set[str] | None,
    slugs: set[str] | None,
) -> list[dict]:
    """Return only sources matching the requested categories and/or slugs.

    Raises ValueError when the filters produce an empty result, which is
    almost certainly a user error.
    """
    filtered = []
    for entry in sources:
        if categories and entry.get("category") not in categories:
            continue
        if slugs and entry.get("slug") not in slugs:
            continue
        filtered.append(entry)

    if not filtered:
        raise ValueError("No sources matched the requested filters.")

    return filtered


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def write_outputs(
    base_path: Path,
    entry: dict,
    html: str,
    markdown: str,
    metadata: dict,
    dry_run: bool,
) -> None:
    """Write the three knowledge artifacts for a single crawled source.

    Files written:
        <base_path>/<category>/<slug>.html   — raw HTML
        <base_path>/<category>/<slug>.md     — Markdown (indexed by MCP server)
        <base_path>/<category>/<slug>.json   — metadata sidecar
    """
    category_path = base_path / entry["category"]
    stem = entry["slug"]
    print(f"  -> {entry['category']}/{stem} (dry-run={dry_run})")

    if dry_run:
        return

    category_path.mkdir(parents=True, exist_ok=True)
    (category_path / f"{stem}.html").write_text(html, encoding="utf-8")
    (category_path / f"{stem}.md").write_text(markdown, encoding="utf-8")
    (category_path / f"{stem}.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Async crawler
# ---------------------------------------------------------------------------


async def crawl_sources(
    sources: Iterable[dict],
    base_path: Path,
    dry_run: bool,
) -> tuple[list, list]:
    """Crawl each source URL and write its three output artifacts.

    Returns:
        succeeded: list of (category, slug) tuples for successful crawls
        failed:    list of (entry, exception) tuples for failures
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    succeeded: list[tuple[str, str]] = []
    failed: list[tuple[dict, Exception]] = []
    crawler = AsyncWebCrawler()

    try:
        for entry in sources:
            url = entry["url"]
            try:
                container = await crawler.arun(url=url)
                if not container or not container[0].success:
                    raise RuntimeError("crawl failed or returned empty result")

                result = container[0]
                metadata = {
                    "url": url,
                    "category": entry["category"],
                    "slug": entry["slug"],
                    "retrieved_at": timestamp,
                    "notes": entry.get("notes", ""),
                    "status_code": result.status_code,
                    "success": result.success,
                    "content_length": len(result.html or ""),
                }
                write_outputs(
                    base_path,
                    entry,
                    result.html or "",
                    result.markdown.raw_markdown or "",
                    metadata,
                    dry_run,
                )
                entry["last_refreshed"] = timestamp
                succeeded.append((entry["category"], entry["slug"]))

            except Exception as exc:
                failed.append((entry, exc))

    finally:
        await crawler.close()

    return succeeded, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Snapshot documentation sources with Crawl4AI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sources",
        default="knowledge/sources.json",
        type=Path,
        metavar="PATH",
        help="Path to the sources manifest (default: knowledge/sources.json)",
    )
    parser.add_argument(
        "--output",
        default="knowledge",
        type=Path,
        metavar="DIR",
        help="Output directory for crawled artifacts (default: knowledge/)",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        metavar="CATEGORY",
        help="Only crawl sources in this category (repeatable)",
    )
    parser.add_argument(
        "--slug",
        action="append",
        dest="slugs",
        metavar="SLUG",
        help="Only crawl the source with this slug (repeatable)",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=[1, 2],
        help="Only crawl sources with this tier value",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without writing anything",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Interactively define sources (replaces existing list unless --append)",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Add new sources interactively without replacing existing ones",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = load_sources(args.sources)

    # Determine whether to enter interactive source-capture mode.
    if args.init and not args.append:
        # Replace the existing source list entirely.
        sources = prompt_for_sources(existing=[])
    elif args.append or (args.init and sources):
        # Append new entries to the existing list.
        sources = prompt_for_sources(existing=sources)
    elif not sources:
        # No sources defined at all — prompt the user before proceeding.
        print(f"No sources defined. Launching interactive setup for {args.sources}.")
        sources = prompt_for_sources(existing=[])

    if not sources:
        print("Nothing to crawl.")
        return 0

    # Persist the (possibly updated) manifest before crawling so that a
    # partial run doesn't lose newly entered sources.
    save_sources(args.sources, sources)

    # Apply filters.
    categories = set(args.categories) if args.categories else None
    slugs = set(args.slugs) if args.slugs else None
    if categories or slugs:
        sources = filter_sources(sources, categories, slugs)

    if args.tier:
        sources = [s for s in sources if s.get("tier") == args.tier]
        if not sources:
            print(f"No sources matched tier {args.tier}.")
            return 0

    # Run the crawl.
    succeeded, failed = asyncio.run(crawl_sources(sources, args.output, args.dry_run))

    # Flush last_refreshed timestamps back to disk.
    if succeeded and not args.dry_run:
        all_sources = load_sources(args.sources)
        save_sources(args.sources, all_sources)

    # Report results.
    for category, slug in succeeded:
        print(f"[OK] {category}/{slug}")
    for entry, exc in failed:
        print(f"[FAIL] {entry.get('category')}/{entry.get('slug')}: {exc}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
