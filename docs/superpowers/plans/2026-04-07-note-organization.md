# Note Organization: Source Type + Date Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `intake` field to Obsidian note frontmatter showing which channel the link came from, and organize clippings into `YYYY/MM/DD` date-based subfolders for discoverability.

**Architecture:** Two changes to `summarizer.py`: (1) `build_frontmatter()` gets a new `intake` param for the source_type, (2) `write_obsidian_note()` gets a `created_at` param and builds date subfolders under `OBSIDIAN_CLIPPINGS`.

**Tech Stack:** Python 3.11+

**GitHub Issues:** #56, #57

---

## File Structure

### Modified files

| File | Change |
|------|--------|
| `pipeline/summarizer.py` | Add `intake` param to `build_frontmatter()`, add `created_at` param to `write_obsidian_note()` for date subfolder |
| `tests/pipeline/test_summarizer.py` | Update existing tests, add new tests for intake field and date folders |

---

## Task 1: Add `intake` field to frontmatter

**Files:**
- Modify: `pipeline/summarizer.py:57-114` (build_frontmatter)
- Modify: `pipeline/summarizer.py:1126-1133` (caller)
- Modify: `tests/pipeline/test_summarizer.py`

- [ ] **Step 1: Write failing test**

Add to `tests/pipeline/test_summarizer.py`:

```python
def test_build_frontmatter_includes_intake():
    """Verify intake field appears in frontmatter when provided."""
    result = build_frontmatter(
        title="iMessage Link",
        source="https://example.com/test",
        content_type="web_page",
        tags=["test"],
        intake="imessage",
    )
    assert "intake: imessage" in result


def test_build_frontmatter_intake_defaults_to_unknown():
    """Verify intake defaults to 'unknown' when not provided."""
    result = build_frontmatter(
        title="Mystery Link",
        source="https://example.com/test",
        content_type="web_page",
        tags=[],
    )
    assert "intake: unknown" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/pipeline/test_summarizer.py -v`
Expected: FAIL — `build_frontmatter() got an unexpected keyword argument 'intake'`

- [ ] **Step 3: Add `intake` param to `build_frontmatter()`**

In `pipeline/summarizer.py`, add `intake: str = "unknown"` parameter to `build_frontmatter()` signature, and add `f"intake: {intake}"` to the frontmatter lines after the `content-type` line.

- [ ] **Step 4: Pass `intake` from the caller**

At line 1126, add `intake=link.get("source_type", "unknown")` to the `build_frontmatter()` call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/pipeline/test_summarizer.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/summarizer.py tests/pipeline/test_summarizer.py
git commit -m "feat: add intake field to Obsidian note frontmatter

Shows which channel the link came from (signal, imessage, obsidian,
ingest-api, cli). Defaults to 'unknown' for backward compatibility.

Closes #56"
```

---

## Task 2: Date-based folder organization

**Files:**
- Modify: `pipeline/summarizer.py:814-839` (write_obsidian_note)
- Modify: `pipeline/summarizer.py:1152` (caller)
- Modify: `tests/pipeline/test_summarizer.py`

- [ ] **Step 1: Write failing test**

Add to `tests/pipeline/test_summarizer.py`:

```python
def test_write_obsidian_note_date_subfolder(tmp_path):
    """Notes should be written to YYYY/MM/DD subfolders when created_at is provided."""
    import summarizer
    original = summarizer.OBSIDIAN_CLIPPINGS
    summarizer.OBSIDIAN_CLIPPINGS = str(tmp_path)
    try:
        path = summarizer.write_obsidian_note(
            title="Test Note",
            frontmatter="---\ntitle: Test\n---",
            body="Hello world",
            created_at="2026-04-06T12:00:00",
        )
        assert "/2026/04/06/" in path
        assert os.path.exists(path)
        assert path.endswith("Test Note.md")
    finally:
        summarizer.OBSIDIAN_CLIPPINGS = original


def test_write_obsidian_note_no_date_uses_flat(tmp_path):
    """Without created_at, notes go to the flat clippings directory."""
    import summarizer
    original = summarizer.OBSIDIAN_CLIPPINGS
    summarizer.OBSIDIAN_CLIPPINGS = str(tmp_path)
    try:
        path = summarizer.write_obsidian_note(
            title="Flat Note",
            frontmatter="---\ntitle: Flat\n---",
            body="No date",
        )
        # Should be directly in tmp_path, no date subfolder
        assert os.path.dirname(path) == str(tmp_path)
    finally:
        summarizer.OBSIDIAN_CLIPPINGS = original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/pipeline/test_summarizer.py -v`
Expected: FAIL — `write_obsidian_note() got an unexpected keyword argument 'created_at'`

- [ ] **Step 3: Add `created_at` param to `write_obsidian_note()`**

Add `created_at: str = None` parameter. When provided, parse the date and build `YYYY/MM/DD` subfolder under `OBSIDIAN_CLIPPINGS`.

- [ ] **Step 4: Pass `created_at` from the caller**

At line 1152, add `created_at=link.get("created_at")` to the `write_obsidian_note()` call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/pipeline/test_summarizer.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/summarizer.py tests/pipeline/test_summarizer.py
git commit -m "feat: organize clippings into YYYY/MM/DD date subfolders

Notes now land in INTERNET CLIPPINGS/2026/04/07/ instead of a flat
directory. Date derived from the link's created_at timestamp.
Falls back to flat directory when no date is available.

Closes #57"
```
