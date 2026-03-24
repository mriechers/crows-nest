"""
Tests for consolidator.py — parsing, clustering, note generation, and archiving.
"""

import os
import sys
import tempfile
import shutil

import pytest

# Add the pipeline directory to path so imports resolve without package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from consolidator import (
    parse_clipping,
    scan_clippings,
    compute_clusters,
    generate_roundup_note,
    archive_clippings,
    _append_to_roundup,
    _note_name,
    _category_roots_for,
    _shared_category_root,
    STANDARD_TAGS,
    CATEGORY_TAGS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CLIPPING = """\
---
title: "10 CLI Tools to 10x Your Claude Code Game"
source: https://www.tiktok.com/t/ZTkJ7Tkbv/
created: 2026-03-23
content-type: social_video
platform: TikTok
creator: "chase_ai_"
published: 2026-03-21
para: inbox
tags:
  - all
  - clippings
  - video-clip
  - inbox-capture
  - claude-code
  - cli-tools
  - developer-tools
  - automation
---

> [!summary]
> Chase presents 10 essential CLI tools that dramatically enhance Claude Code capabilities.

## Key Points

- CLI Anything — generates CLI tools from any open source project
- Playwright CLI — enables browser automation capabilities
- GitHub CLI — essential for pushing code to repositories

## Follow-Up Ideas

- [ ] Test CLI Anything with a personal open source project
- [ ] Set up Playwright CLI with Claude Code
"""

SAMPLE_CLIPPING_2 = """\
---
title: "Claude Code LSP Setup Beats Grep Search Performance"
source: https://example.com/lsp
created: 2026-03-22
content-type: youtube
platform: YouTube
creator: "techdev"
para: inbox
tags:
  - all
  - clippings
  - video-clip
  - inbox-capture
  - claude-code
  - developer-tools
  - lsp
  - productivity
---

> [!summary]
> Demonstrates how LSP integration improves Claude Code search performance.

## Key Points

- LSP provides semantic search vs grep's text matching
- Setup takes 5 minutes with the right config
"""

SAMPLE_CLIPPING_3 = """\
---
title: "Advanced Claude Code Workflows with MCP"
source: https://example.com/mcp
created: 2026-03-21
content-type: youtube
para: inbox
tags:
  - all
  - clippings
  - video-clip
  - inbox-capture
  - claude-code
  - developer-tools
  - mcp
  - automation
---

> [!summary]
> Deep dive into MCP server integration with Claude Code for advanced workflows.

## Key Points

- MCP servers extend Claude Code capabilities
- Custom tools can be built for domain-specific tasks

## Follow-Up Ideas

- [ ] Build a custom MCP server for the project
"""

SAMPLE_CLIPPING_4 = """\
---
title: "Horror Film Analysis — The Shining"
source: https://example.com/shining
created: 2026-03-20
content-type: youtube
para: inbox
tags:
  - all
  - clippings
  - video-clip
  - inbox-capture
  - horror
  - film-analysis
  - stanley-kubrick
---

> [!summary]
> Analysis of The Shining's psychological horror techniques.
"""

SAMPLE_FILM_1 = """\
---
title: "The Brutalist — Epic Architecture Drama"
source: https://example.com/brutalist
created: 2026-03-20
content-type: web
para: inbox
tags:
  - all
  - clippings
  - web-clip
  - inbox-capture
  - independent-film
  - drama
  - architecture
---

> [!summary]
> A sweeping epic about a Hungarian architect who emigrates to America after WWII.
"""

SAMPLE_FILM_2 = """\
---
title: "Anora — Cinderella Story Gone Wrong"
source: https://example.com/anora
created: 2026-03-19
content-type: web
para: inbox
tags:
  - all
  - clippings
  - web-clip
  - inbox-capture
  - film-review
  - comedy-drama
  - indie
---

> [!summary]
> A young sex worker from Brooklyn marries the son of a Russian oligarch.
"""

SAMPLE_PRODUCT_1 = """\
---
title: "Framework Laptop 16 Review"
source: https://example.com/framework
created: 2026-03-18
content-type: web
para: inbox
tags:
  - all
  - clippings
  - web-clip
  - inbox-capture
  - product
  - laptop
  - right-to-repair
---

> [!summary]
> The Framework 16 is the most modular laptop ever made.
"""

SAMPLE_PRODUCT_2 = """\
---
title: "Keychron Q1 Max Keyboard"
source: https://example.com/keychron
created: 2026-03-17
content-type: web
para: inbox
tags:
  - all
  - clippings
  - web-clip
  - inbox-capture
  - product
  - keyboard
  - mechanical-keyboard
---

> [!summary]
> A premium wireless mechanical keyboard with hot-swappable switches.
"""

MALFORMED_NOTE = """\
This is just some text without frontmatter.
No YAML here at all.
"""

MINIMAL_NOTE = """\
---
title: "Just a Title"
para: inbox
---

Some body text.
"""


@pytest.fixture
def clippings_dir(tmp_path):
    """Create a temp directory with sample clipping files."""
    d = tmp_path / "Clippings"
    d.mkdir()

    (d / "10 CLI Tools.md").write_text(SAMPLE_CLIPPING)
    (d / "Claude Code LSP.md").write_text(SAMPLE_CLIPPING_2)
    (d / "Advanced Claude Code MCP.md").write_text(SAMPLE_CLIPPING_3)
    (d / "Horror Film Shining.md").write_text(SAMPLE_CLIPPING_4)

    return str(d)


@pytest.fixture
def archive_dir(tmp_path):
    """Create a temp archive directory."""
    d = tmp_path / "ARCHIVE"
    d.mkdir()
    return str(d)


# ---------------------------------------------------------------------------
# parse_clipping tests
# ---------------------------------------------------------------------------

def test_parse_clipping(tmp_path):
    """Parse a representative note, verify all fields extracted."""
    fp = tmp_path / "test.md"
    fp.write_text(SAMPLE_CLIPPING)

    result = parse_clipping(str(fp))
    assert result is not None
    assert result["title"] == "10 CLI Tools to 10x Your Claude Code Game"
    assert result["source"] == "https://www.tiktok.com/t/ZTkJ7Tkbv/"
    assert result["created"] == "2026-03-23"
    assert result["content-type"] == "social_video"
    assert result["platform"] == "TikTok"
    assert result["creator"] == "chase_ai_"
    assert result["para"] == "inbox"
    assert "claude-code" in result["tags"]
    assert "all" in result["tags"]
    assert "video-clip" in result["tags"]
    assert "Chase presents" in result["summary"]
    assert len(result["key_points"]) == 3
    assert len(result["followups"]) == 2


def test_parse_clipping_malformed(tmp_path):
    """Returns None for non-clipping files."""
    fp = tmp_path / "bad.md"
    fp.write_text(MALFORMED_NOTE)
    assert parse_clipping(str(fp)) is None


def test_parse_clipping_no_tags(tmp_path):
    """Returns None for notes without tags."""
    fp = tmp_path / "minimal.md"
    fp.write_text(MINIMAL_NOTE)
    assert parse_clipping(str(fp)) is None


# ---------------------------------------------------------------------------
# Standard tags exclusion
# ---------------------------------------------------------------------------

def test_standard_tags_excluded(tmp_path):
    """Topic tags don't include all/clippings/video-clip/inbox-capture."""
    fp = tmp_path / "test.md"
    fp.write_text(SAMPLE_CLIPPING)

    result = parse_clipping(str(fp))
    for std_tag in STANDARD_TAGS:
        assert std_tag not in result["topic_tags"], f"{std_tag} should be excluded from topic_tags"

    assert "claude-code" in result["topic_tags"]
    assert "cli-tools" in result["topic_tags"]
    assert "developer-tools" in result["topic_tags"]
    assert "automation" in result["topic_tags"]


# ---------------------------------------------------------------------------
# Clustering tests
# ---------------------------------------------------------------------------

def test_compute_clusters_basic(clippings_dir):
    """4 notes sharing tags form expected clusters."""
    clippings = scan_clippings(clippings_dir)
    assert len(clippings) == 4

    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=3)
    assert len(clusters) == 1  # 3 Claude Code notes cluster together

    cluster = clusters[0]
    assert len(cluster["notes"]) == 3
    assert "claude-code" in cluster["tags"]
    assert "developer-tools" in cluster["tags"]

    # The horror film note should NOT be in this cluster
    titles = [n["title"] for n in cluster["notes"]]
    assert "Horror Film Analysis — The Shining" not in titles


def test_compute_clusters_min_size(clippings_dir):
    """Pairs below min_size are excluded."""
    clippings = scan_clippings(clippings_dir)
    # With min_size=4, the 3-note cluster should be excluded
    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=4)
    assert len(clusters) == 0


def test_compute_clusters_transitive(tmp_path):
    """A↔B and B↔C puts A,B,C in same cluster even if A↔C don't share enough tags."""
    d = tmp_path / "Clippings"
    d.mkdir()

    # A shares tags with B (python, testing)
    (d / "a.md").write_text("""\
---
title: "Note A"
para: inbox
tags:
  - all
  - clippings
  - python
  - testing
---

> [!summary]
> Note A summary.
""")
    # B shares tags with both A (python, testing) and C (testing, ci-cd)
    (d / "b.md").write_text("""\
---
title: "Note B"
para: inbox
tags:
  - all
  - clippings
  - python
  - testing
  - ci-cd
---

> [!summary]
> Note B summary.
""")
    # C shares tags with B (testing, ci-cd) but NOT enough with A
    (d / "c.md").write_text("""\
---
title: "Note C"
para: inbox
tags:
  - all
  - clippings
  - testing
  - ci-cd
---

> [!summary]
> Note C summary.
""")

    clippings = scan_clippings(str(d))
    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=3)

    assert len(clusters) == 1
    assert len(clusters[0]["notes"]) == 3


# ---------------------------------------------------------------------------
# Roundup note generation
# ---------------------------------------------------------------------------

def test_generate_roundup_frontmatter(clippings_dir):
    """Verify frontmatter fields and tag structure."""
    clippings = scan_clippings(clippings_dir)
    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=3)
    assert len(clusters) >= 1

    cluster = clusters[0]
    cluster["roundup_title"] = "Claude Code Power Tools — Roundup"

    frontmatter, body = generate_roundup_note(cluster)

    assert 'title: "Claude Code Power Tools' in frontmatter
    assert "content-type: clippings-roundup" in frontmatter
    assert "para: inbox" in frontmatter
    assert f"source-count: {len(cluster['notes'])}" in frontmatter
    assert "- all" in frontmatter
    assert "- clippings-roundup" in frontmatter
    assert "- claude-code" in frontmatter


def test_generate_roundup_body_has_wikilinks(clippings_dir):
    """Body contains [[note title]] links."""
    clippings = scan_clippings(clippings_dir)
    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=3)
    cluster = clusters[0]
    cluster["roundup_title"] = "Test Roundup"

    _, body = generate_roundup_note(cluster)

    for note in cluster["notes"]:
        name = _note_name(note)
        assert f"[[{name}]]" in body

    assert "## What's Covered" in body
    assert "## Source Notes" in body


# ---------------------------------------------------------------------------
# Archive tests
# ---------------------------------------------------------------------------

def test_archive_updates_para(tmp_path, monkeypatch):
    """para field changes from inbox to archive."""
    # Set up clippings dir and archive dir
    clippings_d = tmp_path / "Clippings"
    clippings_d.mkdir()
    archive_d = tmp_path / "ARCHIVE"
    archive_d.mkdir()

    fp = clippings_d / "test_note.md"
    fp.write_text(SAMPLE_CLIPPING)

    parsed = parse_clipping(str(fp))

    # Monkeypatch OBSIDIAN_ARCHIVE to use temp dir
    monkeypatch.setattr("consolidator.OBSIDIAN_ARCHIVE", str(archive_d))

    cluster = {"notes": [parsed]}
    archived = archive_clippings(cluster, "Test Roundup")

    assert len(archived) == 1
    with open(archived[0], "r") as f:
        content = f.read()
    assert "para: archive" in content
    assert "para: inbox" not in content


def test_archive_adds_consolidated_into(tmp_path, monkeypatch):
    """consolidated-into field is inserted after para."""
    clippings_d = tmp_path / "Clippings"
    clippings_d.mkdir()
    archive_d = tmp_path / "ARCHIVE"
    archive_d.mkdir()

    fp = clippings_d / "test_note.md"
    fp.write_text(SAMPLE_CLIPPING)

    parsed = parse_clipping(str(fp))
    monkeypatch.setattr("consolidator.OBSIDIAN_ARCHIVE", str(archive_d))

    cluster = {"notes": [parsed]}
    roundup_title = "Claude Code Tools — Roundup"
    archive_clippings(cluster, roundup_title)

    with open(os.path.join(
        str(archive_d),
        str(datetime.now().year),
        "Clippings",
        "test_note.md",
    ), "r") as f:
        content = f.read()

    assert f'consolidated-into: "[[{roundup_title}]]"' in content
    # consolidated-into should appear right after para: archive
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "para: archive":
            assert "consolidated-into:" in lines[i + 1]
            break


# Need datetime for archive test
from datetime import datetime


# ---------------------------------------------------------------------------
# Category tag clustering tests
# ---------------------------------------------------------------------------

def test_category_roots_for():
    """Compound tags are resolved to their category roots."""
    assert _category_roots_for({"horror-films", "film-review"}) == {"film"}
    assert _category_roots_for({"product", "laptop"}) == {"product"}
    assert _category_roots_for({"indie-games", "horror"}) == {"game"}
    assert _category_roots_for({"python", "testing"}) == set()


def test_shared_category_root():
    """Two tag sets sharing a category root are detected."""
    assert _shared_category_root({"independent-film"}, {"film-review"}) == {"film"}
    assert _shared_category_root({"python"}, {"testing"}) == set()


def test_category_tags_cluster_with_compound_tags(tmp_path):
    """Two films with compound film tags (independent-film, film-review) should cluster."""
    d = tmp_path / "Clippings"
    d.mkdir()
    (d / "brutalist.md").write_text(SAMPLE_FILM_1)
    (d / "anora.md").write_text(SAMPLE_FILM_2)

    clippings = scan_clippings(str(d))
    assert len(clippings) == 2

    # These share no exact tags — only category root "film" via compound tags
    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=2)
    assert len(clusters) == 1
    assert len(clusters[0]["notes"]) == 2


def test_category_tags_products_cluster(tmp_path):
    """Two products sharing only 'product' tag should cluster."""
    d = tmp_path / "Clippings"
    d.mkdir()
    (d / "framework.md").write_text(SAMPLE_PRODUCT_1)
    (d / "keychron.md").write_text(SAMPLE_PRODUCT_2)

    clippings = scan_clippings(str(d))
    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=2)
    assert len(clusters) == 1
    assert "product" in clusters[0]["tags"]


def test_category_and_topic_clusters_separate(tmp_path):
    """Films and dev tools should form separate clusters."""
    d = tmp_path / "Clippings"
    d.mkdir()
    (d / "brutalist.md").write_text(SAMPLE_FILM_1)
    (d / "anora.md").write_text(SAMPLE_FILM_2)
    (d / "cli_tools.md").write_text(SAMPLE_CLIPPING)
    (d / "lsp.md").write_text(SAMPLE_CLIPPING_2)
    (d / "mcp.md").write_text(SAMPLE_CLIPPING_3)

    clippings = scan_clippings(str(d))
    clusters = compute_clusters(clippings, min_shared_tags=2, min_cluster_size=2)

    # Should have 2 clusters: films and dev tools
    assert len(clusters) == 2
    # One cluster has film-related tags, the other has claude-code
    film_cluster = [c for c in clusters if "film" in _category_roots_for(set(c.get("tags", [])))]
    dev_cluster = [c for c in clusters if "claude-code" in c.get("tags", [])]
    assert len(film_cluster) == 1
    assert len(dev_cluster) == 1
    # Film cluster should not contain dev notes
    film_titles = {n["title"] for n in film_cluster[0]["notes"]}
    assert "The Brutalist — Epic Architecture Drama" in film_titles
    assert "Anora — Cinderella Story Gone Wrong" in film_titles


# ---------------------------------------------------------------------------
# Append to existing roundup tests
# ---------------------------------------------------------------------------

def test_append_to_roundup(tmp_path):
    """New notes are appended to an existing roundup without duplicates."""
    # Create an existing roundup note
    roundup = tmp_path / "Films to Check Out.md"
    roundup.write_text("""\
---
title: "Films to Check Out"
created: 2026-03-20
content-type: clippings-roundup
para: inbox
source-count: 1
tags:
  - all
  - clippings-roundup
  - film
---

> [!summary]
> A roundup of 1 related clippings covering films.

## What's Covered

- **[[The Brutalist — Epic Architecture Drama]]** — A sweeping epic.

## Source Notes

- [[The Brutalist — Epic Architecture Drama]]
""")

    # Parse a new film clipping
    fp = tmp_path / "anora.md"
    fp.write_text(SAMPLE_FILM_2)
    new_note = parse_clipping(str(fp))

    cluster = {"notes": [new_note], "tags": ["film"]}
    _append_to_roundup(str(roundup), cluster)

    content = roundup.read_text()
    assert "source-count: 2" in content
    assert "[[anora]]" in content
    # Original note still present
    assert "[[The Brutalist" in content


def test_append_skips_duplicates(tmp_path):
    """Notes already in the roundup are not added again."""
    roundup = tmp_path / "Films.md"
    roundup.write_text("""\
---
title: "Films"
source-count: 1
---

## What's Covered

- **[[brutalist]]** — A sweeping epic.

## Source Notes

- [[brutalist]]
""")

    # Create a note whose _note_name matches "brutalist"
    fp = tmp_path / "brutalist.md"
    fp.write_text(SAMPLE_FILM_1)
    note = parse_clipping(str(fp))

    cluster = {"notes": [note], "tags": ["film"]}
    _append_to_roundup(str(roundup), cluster)

    content = roundup.read_text()
    # source-count should NOT increase
    assert "source-count: 1" in content
