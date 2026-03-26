"""
Clippings Consolidator for the Crow's Nest pipeline.

Scans Obsidian Clippings, clusters topically similar notes, verifies
groupings with Claude, generates roundup summary notes, and archives
the originals.

Usage:
    python consolidator.py              # scan and print proposed clusters
    python consolidator.py --execute    # full workflow: verify, generate, archive
    python consolidator.py --min-tags 2 --min-size 3
"""

import argparse
import glob
import json
import os
import re
import shutil
import urllib.request
import urllib.error
from datetime import datetime, timezone

from config import OBSIDIAN_CLIPPINGS, OBSIDIAN_ARCHIVE, OBSIDIAN_VAULT
from summarizer import _extract_json, _sanitize_tag
from utils import sanitize_title, setup_logging
from keychain_secrets import get_secret

logger = setup_logging("crows-nest.consolidator")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-haiku-4-5"


def _note_name(note: dict) -> str:
    """Get the Obsidian-safe note name (filename without .md extension).

    Wikilinks must match the filename, not the frontmatter title, because
    sanitize_title strips characters like colons and em-dashes.
    """
    fn = note.get("filename", "")
    if fn.endswith(".md"):
        return fn[:-3]
    return sanitize_title(note.get("title", "untitled"))

# Tags that indicate format/pipeline metadata, not topic
STANDARD_TAGS = {
    "all", "clippings", "video-clip", "web-clip", "audio-clip",
    "image-clip", "inbox-capture",
}

# Category tags — broad content-type categories where a single shared tag
# is enough to cluster notes together.  These represent "collection" topics
# where users want a running list (e.g. "Films to Check Out") rather than
# requiring deep topical overlap.
CATEGORY_TAGS = {
    "film", "movie", "documentary", "tv-show", "television",
    "product", "gear", "gadget", "tool",
    "book", "reading",
    "podcast", "audio",
    "recipe", "food",
    "music", "album", "artist",
    "game", "video-game",
    "app", "software",
    "place", "travel", "destination",
}


def _category_roots_for(tags: set[str]) -> set[str]:
    """Return category roots present in a set of topic tags.

    A tag "matches" a category root if the root appears as a hyphen-delimited
    component.  e.g. "horror-films" matches "film" (via stem "films"→"film"),
    "film-review" matches "film", "product" matches "product".
    """
    roots = set()
    for tag in tags:
        parts = tag.split("-")
        for part in parts:
            # Simple English plural stemming for common suffixes
            stem = part
            if stem.endswith("s") and len(stem) > 3:
                stem = stem[:-1]
            if stem in CATEGORY_TAGS or part in CATEGORY_TAGS:
                roots.add(stem if stem in CATEGORY_TAGS else part)
    return roots


def _shared_category_root(tags_a: set[str], tags_b: set[str]) -> set[str]:
    """Return category roots shared between two tag sets."""
    return _category_roots_for(tags_a) & _category_roots_for(tags_b)


# ---------------------------------------------------------------------------
# 1a. Frontmatter parser
# ---------------------------------------------------------------------------

def parse_clipping(filepath: str) -> dict | None:
    """Parse an Obsidian clipping note into a structured dict.

    Returns None for files that don't match the clipping format
    (no frontmatter delimiters or missing required fields).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read clipping %s: %s", filepath, exc)
        return None

    # Split on frontmatter delimiters
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_text = parts[1]
    body_text = parts[2]

    # Parse frontmatter fields
    result = {"filepath": filepath, "filename": os.path.basename(filepath)}

    # Simple key: value fields
    for field in ("title", "source", "created", "content-type", "platform",
                  "creator", "para", "published"):
        match = re.search(rf'^{re.escape(field)}:\s*(.+)$', frontmatter_text, re.MULTILINE)
        if match:
            val = match.group(1).strip().strip('"')
            result[field] = val

    # Tags — collect indented list items after "tags:"
    tags = []
    in_tags = False
    for line in frontmatter_text.splitlines():
        if line.strip() == "tags:":
            in_tags = True
            continue
        if in_tags:
            tag_match = re.match(r'^\s+-\s+(.+)$', line)
            if tag_match:
                tags.append(tag_match.group(1).strip())
            else:
                in_tags = False
    result["tags"] = tags

    # Must have at least a title and tags to be a valid clipping
    if "title" not in result or not tags:
        return None

    # Topic tags (excluding standard/format tags)
    result["topic_tags"] = [t for t in tags if t not in STANDARD_TAGS]

    # Body: summary from callout
    summary_match = re.search(r'>\s*\[!summary\]\n>\s*(.+?)(?:\n\n|\Z)', body_text, re.DOTALL)
    if summary_match:
        # Join multi-line summary (each line starts with "> ")
        raw_summary = summary_match.group(1)
        result["summary"] = re.sub(r'\n>\s*', ' ', raw_summary).strip()

    # Body: key points
    kp_match = re.search(r'## Key Points\n\n((?:- .+\n?)+)', body_text)
    if kp_match:
        result["key_points"] = [
            line.lstrip("- ").strip()
            for line in kp_match.group(1).strip().splitlines()
            if line.startswith("- ")
        ]

    # Body: follow-up items
    fu_match = re.search(r'## Follow-Up Ideas\n\n((?:- \[[ x]\] .+\n?)+)', body_text)
    if fu_match:
        result["followups"] = [
            line.lstrip("- ").strip()
            for line in fu_match.group(1).strip().splitlines()
            if line.startswith("- ")
        ]

    return result


# ---------------------------------------------------------------------------
# 1b. Scanner
# ---------------------------------------------------------------------------

def scan_clippings(clippings_dir: str = None) -> list[dict]:
    """Glob *.md in clippings dir and parse each file."""
    clippings_dir = clippings_dir or OBSIDIAN_CLIPPINGS
    files = sorted(glob.glob(os.path.join(clippings_dir, "*.md")))
    results = []
    for fp in files:
        parsed = parse_clipping(fp)
        if parsed is not None:
            results.append(parsed)
    logger.info("scanned %d files, parsed %d clippings", len(files), len(results))
    return results


# ---------------------------------------------------------------------------
# 1c. Tag-based clustering (union-find)
# ---------------------------------------------------------------------------

class _UnionFind:
    """Simple union-find / disjoint set."""

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def compute_clusters(
    clippings: list[dict],
    min_shared_tags: int = 2,
    min_cluster_size: int = 2,
) -> list[dict]:
    """Group clippings by topic tag overlap using union-find.

    Two notes are related if they share min_shared_tags+ topic tags,
    OR if they share any category tag (broad content-type categories
    like "film", "product", "book" where a single match is enough).

    Returns clusters with min_cluster_size+ members.
    """
    n = len(clippings)
    if n < min_cluster_size:
        return []

    uf = _UnionFind(n)

    # Build pairwise connections
    for i in range(n):
        tags_i = set(clippings[i].get("topic_tags", []))
        for j in range(i + 1, n):
            tags_j = set(clippings[j].get("topic_tags", []))
            shared = tags_i & tags_j

            # Category tags: check if both notes have tags containing
            # the same category root (e.g. "film" matches "film-review",
            # "horror-films", "independent-film")
            has_shared_category = _shared_category_root(tags_i, tags_j)
            if has_shared_category or len(shared) >= min_shared_tags:
                uf.union(i, j)

    # Group by root
    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(i)

    # Build cluster dicts, filter by min size
    clusters = []
    for indices in groups.values():
        if len(indices) < min_cluster_size:
            continue

        notes = [clippings[i] for i in indices]

        # Find most common shared topic tags
        from collections import Counter
        tag_counts = Counter()
        for note in notes:
            tag_counts.update(note.get("topic_tags", []))

        # Tags that appear in at least half the cluster
        threshold = len(notes) / 2
        common_tags = [tag for tag, count in tag_counts.most_common()
                       if count >= threshold]

        # If no common exact tags, check for shared category roots
        # across the cluster (the reason these notes were grouped)
        if not common_tags:
            all_tags = set()
            for note in notes:
                all_tags.update(note.get("topic_tags", []))
            category_roots = _category_roots_for(all_tags)
            if category_roots:
                common_tags = sorted(category_roots)

        # Label from top tags
        label = "-".join(common_tags[:3]) if common_tags else "misc"

        clusters.append({
            "tags": common_tags,
            "label": label,
            "notes": notes,
        })

    # Sort by cluster size descending
    clusters.sort(key=lambda c: len(c["notes"]), reverse=True)
    return clusters


# ---------------------------------------------------------------------------
# 1d. Claude cluster verification
# ---------------------------------------------------------------------------

def verify_clusters_with_claude(clusters: list[dict]) -> list[dict]:
    """Ask Claude Haiku to verify/adjust proposed clusters.

    Returns clusters with Claude's suggested human-readable titles.
    """
    api_key = get_secret("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not found, skipping cluster verification")
        # Add default titles
        for cluster in clusters:
            cluster["roundup_title"] = cluster["label"].replace("-", " ").title() + " — Roundup"
        return clusters

    # Build prompt describing clusters
    cluster_descriptions = []
    for i, cluster in enumerate(clusters):
        titles = [n.get("title", "untitled") for n in cluster["notes"]]
        summaries = [n.get("summary", "")[:150] for n in cluster["notes"]]
        note_list = "\n".join(
            f"    - \"{t}\" — {s}" for t, s in zip(titles, summaries)
        )
        cluster_descriptions.append(
            f"  Cluster {i+1} (label: \"{cluster['label']}\", "
            f"shared tags: {cluster['tags'][:5]}, "
            f"{len(cluster['notes'])} notes):\n{note_list}"
        )

    prompt = f"""I have {len(clusters)} proposed clusters of Obsidian clipping notes grouped by shared topic tags. Review each cluster and:

1. Confirm the grouping makes sense (are these truly about the same topic?)
2. Suggest a human-readable title for each cluster's roundup note (e.g., "Claude Code Power Tools & Workflows")
3. Flag any notes that seem misplaced

Clusters:
{chr(10).join(cluster_descriptions)}

Return a JSON object with:
- "clusters": array of objects, each with:
  - "index": original cluster number (1-based)
  - "title": suggested roundup title (descriptive, 5-10 words, ending with " — Roundup")
  - "confirmed": true if grouping is good, false if it needs splitting
  - "notes_to_remove": array of note titles that don't belong (empty if all are fine)"""

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are a knowledge management assistant. Respond with valid JSON only, no markdown fencing."},
            {"role": "user", "content": prompt},
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
                "X-Title": "Crow's Nest Consolidator",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        logger.warning("cluster verification API call failed: %s", exc)
        for cluster in clusters:
            cluster["roundup_title"] = cluster["label"].replace("-", " ").title() + " — Roundup"
        return clusters

    try:
        response = json.loads(raw)
        assistant_text = response["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError):
        logger.warning("unexpected response format from cluster verification")
        for cluster in clusters:
            cluster["roundup_title"] = cluster["label"].replace("-", " ").title() + " — Roundup"
        return clusters

    parsed = _extract_json(assistant_text)
    if parsed and "clusters" in parsed:
        for item in parsed["clusters"]:
            idx = item.get("index", 0) - 1
            if 0 <= idx < len(clusters):
                clusters[idx]["roundup_title"] = item.get("title", clusters[idx]["label"] + " — Roundup")

                # Remove misplaced notes if flagged
                to_remove = set(item.get("notes_to_remove", []))
                if to_remove:
                    clusters[idx]["notes"] = [
                        n for n in clusters[idx]["notes"]
                        if n.get("title") not in to_remove
                    ]
    else:
        logger.warning("could not parse cluster verification response")
        for cluster in clusters:
            cluster.setdefault("roundup_title", cluster["label"].replace("-", " ").title() + " — Roundup")

    return clusters


# ---------------------------------------------------------------------------
# 1e. Roundup note generation
# ---------------------------------------------------------------------------

def generate_roundup_note(cluster: dict) -> tuple[str, str]:
    """Generate frontmatter and body for a roundup summary note.

    Returns (frontmatter_str, body_str).
    """
    title = cluster.get("roundup_title", cluster["label"] + " — Roundup")
    notes = cluster["notes"]
    common_tags = cluster.get("tags", [])
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Frontmatter
    safe_title = title.replace('"', '\\"')
    tag_list = ["all", "clippings-roundup"] + [_sanitize_tag(t) for t in common_tags if t]
    tag_lines = "\n".join(f"  - {t}" for t in tag_list)

    frontmatter = "\n".join([
        "---",
        f'title: "{safe_title}"',
        f"created: {created}",
        "content-type: clippings-roundup",
        "para: inbox",
        f"source-count: {len(notes)}",
        "tags:",
        tag_lines,
        "---",
    ])

    # Body
    sections = []

    # Executive summary
    note_titles = [n.get("title", "untitled") for n in notes]
    summaries_text = "; ".join(
        n.get("summary", n.get("title", ""))[:100] for n in notes
    )
    sections.append(
        f"> [!summary]\n"
        f"> A roundup of {len(notes)} related clippings covering: {summaries_text[:500]}"
    )

    # What's Covered — per-note synopses with wikilinks
    covered_lines = []
    for note in notes:
        name = _note_name(note)
        note_summary = note.get("summary", "")
        if note_summary:
            # Truncate to first sentence or 150 chars
            short = note_summary[:150]
            if ". " in short:
                short = short[:short.index(". ") + 1]
            covered_lines.append(f"- **[[{name}]]** — {short}")
        else:
            covered_lines.append(f"- **[[{name}]]**")
    sections.append("## What's Covered\n\n" + "\n".join(covered_lines))

    # Key Ideas & Resources — deduplicated from all notes
    all_key_points = []
    seen_points = set()
    for note in notes:
        for kp in note.get("key_points", []):
            normalized = kp.lower().strip()
            if normalized not in seen_points:
                seen_points.add(normalized)
                all_key_points.append(kp)
    if all_key_points:
        kp_lines = "\n".join(f"- {kp}" for kp in all_key_points)
        sections.append(f"## Key Ideas & Resources\n\n{kp_lines}")

    # Follow-Up Ideas — merged checkboxes
    all_followups = []
    seen_followups = set()
    for note in notes:
        for fu in note.get("followups", []):
            normalized = fu.lower().strip()
            if normalized not in seen_followups:
                seen_followups.add(normalized)
                all_followups.append(fu)
    if all_followups:
        fu_lines = "\n".join(f"- {fu}" for fu in all_followups)
        sections.append(f"## Follow-Up Ideas\n\n{fu_lines}")

    # Source Notes — simple wikilink list
    source_lines = "\n".join(f"- [[{_note_name(n)}]]" for n in notes)
    sections.append(f"## Source Notes\n\n{source_lines}")

    body = "\n\n".join(sections)
    return frontmatter, body


# ---------------------------------------------------------------------------
# 1f. Append to existing roundup
# ---------------------------------------------------------------------------

def _append_to_roundup(roundup_path: str, cluster: dict) -> bool:
    """Append new notes to an existing category roundup note.

    Updates the source-count in frontmatter and appends entries to the
    "What's Covered" and "Source Notes" sections.

    Returns True if all new notes were successfully inserted, False if
    any section was missing or insertion could not be verified.
    """
    with open(roundup_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Collect existing wikilinks to avoid duplicates
    existing_links = set(re.findall(r'\[\[([^\]]+)\]\]', content))

    new_notes = [
        n for n in cluster["notes"]
        if _note_name(n) not in existing_links
    ]
    if not new_notes:
        logger.info("all notes already in roundup, nothing to append")
        return True

    # Update source-count in frontmatter
    count_match = re.search(r'^source-count:\s*(\d+)$', content, re.MULTILINE)
    if count_match:
        old_count = int(count_match.group(1))
        content = content.replace(
            count_match.group(0),
            f"source-count: {old_count + len(new_notes)}",
        )

    # Append to "## What's Covered" section
    covered_entries = []
    for note in new_notes:
        name = _note_name(note)
        note_summary = note.get("summary", "")
        if note_summary:
            short = note_summary[:150]
            if ". " in short:
                short = short[:short.index(". ") + 1]
            covered_entries.append(f"- **[[{name}]]** — {short}")
        else:
            covered_entries.append(f"- **[[{name}]]**")

    # Find the end of the "## What's Covered" section content
    # by looking for the next heading or end of file
    covered_header = re.search(r'^## What\'s Covered\s*\n', content, re.MULTILINE)
    if covered_header:
        # Find the next ## heading after this one
        next_heading = re.search(r'^\n##\s', content[covered_header.end():], re.MULTILINE)
        if next_heading:
            insert_pos = covered_header.end() + next_heading.start()
        else:
            # Section is at end of file — insert before final newline
            insert_pos = len(content.rstrip()) + 1
        new_covered = "\n".join(covered_entries) + "\n"
        content = content[:insert_pos] + new_covered + content[insert_pos:]
    else:
        logger.warning("could not find '## What's Covered' section in %s, entries not appended",
                       roundup_path)
        return False

    # Append to "## Source Notes" section
    source_entries = "\n".join(f"- [[{_note_name(n)}]]" for n in new_notes)
    source_header = re.search(r'^## Source Notes\s*\n', content, re.MULTILINE)
    if source_header:
        next_heading = re.search(r'^\n##\s', content[source_header.end():], re.MULTILINE)
        if next_heading:
            insert_pos = source_header.end() + next_heading.start()
        else:
            insert_pos = len(content.rstrip()) + 1
        content = content[:insert_pos] + source_entries + "\n" + content[insert_pos:]
    else:
        logger.warning("could not find '## Source Notes' section in %s, entries not appended",
                       roundup_path)
        return False

    # Verify all new wikilinks are present before writing
    for note in new_notes:
        name = _note_name(note)
        if f"[[{name}]]" not in content:
            logger.warning("verification failed: [[%s]] not found in modified content, aborting write",
                           name)
            return False

    with open(roundup_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("appended %d note(s) to %s", len(new_notes), roundup_path)
    return True


# ---------------------------------------------------------------------------
# 1g. Archive operation
# ---------------------------------------------------------------------------

def archive_clippings(cluster: dict, roundup_title: str) -> list[str]:
    """Move original clipping notes to archive, updating frontmatter.

    Returns list of new file paths.
    """
    year = datetime.now().strftime("%Y")
    archive_dir = os.path.join(OBSIDIAN_ARCHIVE, year, "Clippings")
    os.makedirs(archive_dir, exist_ok=True)

    moved = []
    for note in cluster["notes"]:
        filepath = note.get("filepath")
        if not filepath or not os.path.exists(filepath):
            logger.warning("skipping missing file: %s", filepath)
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                original_content = f.read()

            content = original_content

            # Update para: inbox -> para: archive
            content = re.sub(r'^para:\s*inbox$', 'para: archive', content, flags=re.MULTILINE)

            # Insert consolidated-into after the para: line
            safe_roundup = roundup_title.replace('"', '\\"')
            content = re.sub(
                r'^(para:\s*archive)$',
                lambda m: f'{m.group(1)}\nconsolidated-into: "[[{safe_roundup}]]"',
                content,
                flags=re.MULTILINE,
            )

            # Write modified content to destination, then remove source
            dest = os.path.join(archive_dir, os.path.basename(filepath))
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)
            os.remove(filepath)

            moved.append(dest)
            logger.info("archived: %s -> %s", os.path.basename(filepath), dest)

        except Exception as exc:
            logger.error("failed to archive %s: %s", os.path.basename(filepath), exc)

    return moved


# ---------------------------------------------------------------------------
# 1g. CLI interface
# ---------------------------------------------------------------------------

def print_clusters(clusters: list[dict]) -> None:
    """Pretty-print proposed clusters for review."""
    if not clusters:
        print("\nNo clusters found. Notes may not share enough topic tags.")
        return

    print(f"\n{'='*60}")
    print(f"Found {len(clusters)} cluster(s)")
    print(f"{'='*60}")

    for i, cluster in enumerate(clusters):
        title = cluster.get("roundup_title", cluster["label"])
        notes = cluster["notes"]
        tags = cluster.get("tags", [])

        print(f"\n--- Cluster {i+1}: {title} ---")
        print(f"    Shared tags: {', '.join(tags[:6])}")
        print(f"    Notes ({len(notes)}):")
        for note in notes:
            print(f"      - {note.get('title', 'untitled')}")


def execute_workflow(
    clippings_dir: str = None,
    min_shared_tags: int = 2,
    min_cluster_size: int = 3,
) -> None:
    """Full workflow: scan, cluster, verify, generate, archive."""
    clippings = scan_clippings(clippings_dir)
    if not clippings:
        print("No clippings found.")
        return

    clusters = compute_clusters(clippings, min_shared_tags, min_cluster_size)
    if not clusters:
        print("No clusters found with current thresholds.")
        return

    print_clusters(clusters)

    # Verify with Claude
    print("\nVerifying clusters with Claude...")
    clusters = verify_clusters_with_claude(clusters)

    # Generate roundup notes and archive originals
    inbox_dir = os.path.join(OBSIDIAN_VAULT, "0 - INBOX")
    os.makedirs(inbox_dir, exist_ok=True)

    for cluster in clusters:
        roundup_title = cluster.get("roundup_title", cluster["label"] + " — Roundup")
        safe_filename = sanitize_title(roundup_title)
        note_path = os.path.join(inbox_dir, f"{safe_filename}.md")

        # Check if this is a category cluster with an existing roundup to append to
        is_category = bool(_category_roots_for(set(cluster.get("tags", []))))

        if is_category and os.path.exists(note_path):
            # Append new notes to existing roundup
            success = _append_to_roundup(note_path, cluster)
            if not success:
                print(f"\nWARNING: Failed to append to {note_path} — originals NOT archived")
                continue
            print(f"\nAppended {len(cluster['notes'])} note(s) to existing roundup: {note_path}")
        else:
            # Generate new roundup note
            frontmatter, body = generate_roundup_note(cluster)
            note_content = f"{frontmatter}\n\n{body}\n"

            # Handle collision for non-category clusters
            if not is_category:
                counter = 1
                while os.path.exists(note_path):
                    note_path = os.path.join(inbox_dir, f"{safe_filename} ({counter}).md")
                    counter += 1

            with open(note_path, "w", encoding="utf-8") as f:
                f.write(note_content)
            print(f"\nCreated roundup: {note_path}")

        # Archive originals — only after roundup was successfully created/updated
        archived = archive_clippings(cluster, safe_filename)
        print(f"  Archived {len(archived)} original note(s)")

    print(f"\nDone! Created {len(clusters)} roundup note(s).")


def main():
    parser = argparse.ArgumentParser(
        description="Consolidate topically similar Obsidian clippings into roundup notes."
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Scan-only mode (this is the default)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Run full workflow (default is scan-only)",
    )
    parser.add_argument(
        "--min-tags", type=int, default=2,
        help="Minimum shared topic tags for clustering (default: 2)",
    )
    parser.add_argument(
        "--min-size", type=int, default=2,
        help="Minimum cluster size (default: 2)",
    )
    args = parser.parse_args()

    if args.execute:
        execute_workflow(
            min_shared_tags=args.min_tags,
            min_cluster_size=args.min_size,
        )
    else:
        clippings = scan_clippings()
        clusters = compute_clusters(clippings, args.min_tags, args.min_size)
        print_clusters(clusters)


if __name__ == "__main__":
    main()
