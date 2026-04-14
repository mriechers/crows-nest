# Vault-Relative DB Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store vault-relative paths (e.g. `2 - AREAS/INTERNET CLIPPINGS/2026/04/12/note.md`) in the DB's `obsidian_note_path` column instead of absolute paths, making the pipeline portable across machines and mount points.

**Architecture:** Add a `to_vault_relative()` / `to_abs_note_path()` helper pair in `config.py`. All DB write sites strip the vault prefix before storing; the one DB read site that does file I/O resolves back to absolute. A one-time migration converts existing rows.

**Tech Stack:** Python 3, SQLite, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pipeline/config.py` | Modify (lines 55–59) | Add `to_vault_relative()` and `to_abs_note_path()` helpers |
| `pipeline/summarizer.py` | Modify (line 1280) | Strip prefix before DB write |
| `pipeline/archiver.py` | Modify (lines 257, 289, 348) | Resolve to absolute after DB read |
| `pipeline/sync_clippings.py` | Modify (lines 327, 339) | Strip prefix before DB writes |
| `pipeline/backfill_date_folders.py` | Modify (line 80) | Store relative path, not absolute |
| `tests/pipeline/test_config_paths.py` | Create | Tests for the two new helpers |
| `tests/pipeline/test_backfill.py` | Create | Tests for the backfill script |

---

### Task 1: Helper Functions in config.py

**Files:**
- Modify: `pipeline/config.py:55-59`
- Create: `tests/pipeline/test_config_paths.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/pipeline/test_config_paths.py`:

```python
"""Tests for vault-relative path helpers."""

import os

import pytest


def test_to_vault_relative_strips_prefix(monkeypatch):
    """Absolute path under vault root becomes vault-relative."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_vault_relative

    result = to_vault_relative("/home/user/vault/2 - AREAS/INTERNET CLIPPINGS/note.md")
    assert result == os.path.join("2 - AREAS", "INTERNET CLIPPINGS", "note.md")


def test_to_vault_relative_with_trailing_slash(monkeypatch):
    """Works whether or not OBSIDIAN_VAULT has a trailing slash."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault/")
    from config import to_vault_relative

    result = to_vault_relative("/home/user/vault/2 - AREAS/note.md")
    assert result == os.path.join("2 - AREAS", "note.md")


def test_to_vault_relative_already_relative(monkeypatch):
    """If the path is already relative (no vault prefix), return it unchanged."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_vault_relative

    result = to_vault_relative("2 - AREAS/INTERNET CLIPPINGS/note.md")
    assert result == "2 - AREAS/INTERNET CLIPPINGS/note.md"


def test_to_abs_note_path_prepends_vault(monkeypatch):
    """Vault-relative path becomes absolute by prepending OBSIDIAN_VAULT."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_abs_note_path

    result = to_abs_note_path("2 - AREAS/INTERNET CLIPPINGS/note.md")
    assert result == "/home/user/vault/2 - AREAS/INTERNET CLIPPINGS/note.md"


def test_to_abs_note_path_empty_returns_empty(monkeypatch):
    """Empty or None input returns empty string (used for missing paths)."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_abs_note_path

    assert to_abs_note_path("") == ""
    assert to_abs_note_path(None) == ""


def test_roundtrip(monkeypatch):
    """to_abs(to_relative(abs_path)) returns the original absolute path."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_abs_note_path, to_vault_relative

    original = "/home/user/vault/2 - AREAS/INTERNET CLIPPINGS/2026/04/12/note.md"
    assert to_abs_note_path(to_vault_relative(original)) == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Volumes/Mark's\ SSD/Developer/second-brain/crows-nest && source .venv/bin/activate && pytest tests/pipeline/test_config_paths.py -v`
Expected: FAIL — `ImportError: cannot import name 'to_vault_relative' from 'config'`

- [ ] **Step 3: Implement the helpers**

Add to `pipeline/config.py` immediately after the `OBSIDIAN_ARCHIVE` line (after line 59):

```python


# ---------------------------------------------------------------------------
# Vault-relative path helpers
# ---------------------------------------------------------------------------

def to_vault_relative(abs_path: str) -> str:
    """Strip OBSIDIAN_VAULT prefix to get a vault-relative path for DB storage.

    If the path doesn't start with OBSIDIAN_VAULT (already relative, or from
    a different mount point), returns it unchanged.
    """
    vault = OBSIDIAN_VAULT.rstrip(os.sep) + os.sep
    if abs_path.startswith(vault):
        return abs_path[len(vault):]
    return abs_path


def to_abs_note_path(vault_relative: str) -> str:
    """Reconstruct absolute path from a vault-relative DB value.

    Returns empty string for empty/None input (common for links without notes).
    """
    if not vault_relative:
        return ""
    return os.path.join(OBSIDIAN_VAULT, vault_relative)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pipeline/test_config_paths.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/config.py tests/pipeline/test_config_paths.py
git commit -m "feat: add to_vault_relative/to_abs_note_path helpers in config"
```

---

### Task 2: Update Summarizer Write Site

**Files:**
- Modify: `pipeline/summarizer.py:1277-1281`

- [ ] **Step 1: Write the failing test**

Add to `tests/pipeline/test_summarizer.py`:

```python
def test_write_obsidian_note_returns_absolute_path(tmp_path):
    """write_obsidian_note returns an absolute path (callers convert for DB)."""
    import summarizer

    original = summarizer.OBSIDIAN_CLIPPINGS
    summarizer.OBSIDIAN_CLIPPINGS = str(tmp_path)
    try:
        path = summarizer.write_obsidian_note(
            title="Abs Path Note",
            frontmatter="---\ntitle: Test\n---",
            body="Content",
            created_at="2026-04-12T10:00:00",
        )
        assert os.path.isabs(path)
        assert os.path.exists(path)
    finally:
        summarizer.OBSIDIAN_CLIPPINGS = original
```

This test documents the existing behavior — `write_obsidian_note` returns absolute, and the caller is responsible for converting. It should PASS immediately, confirming we don't need to change the function itself.

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/pipeline/test_summarizer.py::test_write_obsidian_note_returns_absolute_path -v`
Expected: PASS

- [ ] **Step 3: Apply the change to the summarizer's DB write call**

In `pipeline/summarizer.py`, add the import near the top where other config imports are:

```python
from config import to_vault_relative
```

Then change lines 1277–1281 from:

```python
                update_status(
                    link_id=link_id,
                    status="summarized",
                    obsidian_note_path=note_path,
                    db_path=db_path,
                )
```

to:

```python
                update_status(
                    link_id=link_id,
                    status="summarized",
                    obsidian_note_path=to_vault_relative(note_path),
                    db_path=db_path,
                )
```

- [ ] **Step 4: Run existing summarizer tests to ensure nothing breaks**

Run: `pytest tests/pipeline/test_summarizer.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/summarizer.py tests/pipeline/test_summarizer.py
git commit -m "refactor: store vault-relative path in summarizer DB write"
```

---

### Task 3: Update Archiver Read Site

**Files:**
- Modify: `pipeline/archiver.py:257`

- [ ] **Step 1: Apply the change**

In `pipeline/archiver.py`, add the import near the top where other config imports are:

```python
from config import to_abs_note_path
```

Then change line 257 from:

```python
        obsidian_note = link.get("obsidian_note_path") or ""
```

to:

```python
        obsidian_note = to_abs_note_path(link.get("obsidian_note_path") or "")
```

This is the only consumer that opens the file on disk (via `update_obsidian_note()` at lines 289 and 348). The function itself (`update_obsidian_note`) takes an absolute path and works unchanged.

Note: The R2 manifest at line 336 will now store the absolute path (reconstructed). This is fine — manifests are metadata snapshots, not consumed by other pipeline stages.

- [ ] **Step 2: Run existing archiver tests**

Run: `pytest tests/pipeline/ -v -k archiver`
Expected: PASS (or no archiver-specific tests collected — this module's tests focus on integration)

- [ ] **Step 3: Commit**

```bash
git add pipeline/archiver.py
git commit -m "refactor: resolve vault-relative path to absolute in archiver"
```

---

### Task 4: Update sync_clippings Write Sites

**Files:**
- Modify: `pipeline/sync_clippings.py:327, 339`

- [ ] **Step 1: Apply the changes**

In `pipeline/sync_clippings.py`, add the import near the top where other config imports are:

```python
from config import to_vault_relative
```

Then change line 327 from:

```python
                update_status(
                    link_id=link_id,
                    status="summarized",
                    obsidian_note_path=note_path,
                    db_path=db_path,
                )
```

to:

```python
                update_status(
                    link_id=link_id,
                    status="summarized",
                    obsidian_note_path=to_vault_relative(note_path),
                    db_path=db_path,
                )
```

And change line 339 from:

```python
            update_status(
                link_id=db_row["id"],
                status=db_row["status"],
                obsidian_note_path=note_path,
                db_path=db_path,
            )
```

to:

```python
            update_status(
                link_id=db_row["id"],
                status=db_row["status"],
                obsidian_note_path=to_vault_relative(note_path),
                db_path=db_path,
            )
```

- [ ] **Step 2: Run existing sync_clippings tests**

Run: `pytest tests/pipeline/ -v -k sync`
Expected: PASS (or no tests collected)

- [ ] **Step 3: Commit**

```bash
git add pipeline/sync_clippings.py
git commit -m "refactor: store vault-relative path in sync_clippings DB writes"
```

---

### Task 5: Update Backfill Script

**Files:**
- Modify: `pipeline/backfill_date_folders.py:22-25, 72-87`
- Create: `tests/pipeline/test_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_backfill.py`:

```python
"""Tests for backfill_date_folders."""

import os
import sqlite3

import pytest


def _make_note(directory: str, filename: str, created_date: str) -> str:
    """Create a minimal clippings note with frontmatter."""
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        f.write(f"---\ntitle: Test\ncreated: {created_date}\n---\n\nBody text\n")
    return path


def _init_db(db_path: str, note_path: str) -> None:
    """Create a minimal links table with one row."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS links ("
        "  id INTEGER PRIMARY KEY,"
        "  url TEXT UNIQUE,"
        "  status TEXT DEFAULT 'summarized',"
        "  obsidian_note_path TEXT,"
        "  updated_at TEXT"
        ")"
    )
    conn.execute(
        "INSERT INTO links (url, obsidian_note_path, updated_at) VALUES (?, ?, datetime('now'))",
        ("https://example.com/test", note_path, ),
    )
    conn.commit()
    conn.close()


def test_backfill_stores_vault_relative_path(tmp_path, monkeypatch):
    """After backfill --apply, DB should contain vault-relative paths."""
    import backfill_date_folders

    vault_root = str(tmp_path / "vault")
    clippings_dir = os.path.join(vault_root, "2 - AREAS", "INTERNET CLIPPINGS")
    os.makedirs(clippings_dir)

    # Create a flat note
    _make_note(clippings_dir, "Test Note.md", "2026-03-15")

    # Set up DB with an absolute path (mimics pre-migration state)
    db_path = str(tmp_path / "test.db")
    abs_note_path = os.path.join(clippings_dir, "Test Note.md")
    _init_db(db_path, abs_note_path)

    monkeypatch.setattr(backfill_date_folders, "OBSIDIAN_CLIPPINGS", clippings_dir)
    monkeypatch.setattr(backfill_date_folders, "OBSIDIAN_VAULT", vault_root)

    # Simulate --apply
    import sys
    monkeypatch.setattr(sys, "argv", ["backfill", "--apply", "--db", db_path])
    backfill_date_folders.main()

    # File should have moved
    expected_file = os.path.join(clippings_dir, "2026", "03", "15", "Test Note.md")
    assert os.path.exists(expected_file)
    assert not os.path.exists(abs_note_path)

    # DB should store vault-relative path
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT obsidian_note_path FROM links WHERE url = ?", ("https://example.com/test",)).fetchone()
    conn.close()
    stored_path = row[0]
    assert not os.path.isabs(stored_path), f"Expected relative, got: {stored_path}"
    assert stored_path == os.path.join("2 - AREAS", "INTERNET CLIPPINGS", "2026", "03", "15", "Test Note.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pipeline/test_backfill.py -v`
Expected: FAIL — `OBSIDIAN_VAULT` not importable from backfill module, and the stored path is still absolute

- [ ] **Step 3: Update the backfill script**

In `pipeline/backfill_date_folders.py`, change the import block (lines 22–25) from:

```python
try:
    from pipeline.config import DB_PATH, OBSIDIAN_CLIPPINGS
except ImportError:
    from config import DB_PATH, OBSIDIAN_CLIPPINGS
```

to:

```python
try:
    from pipeline.config import DB_PATH, OBSIDIAN_CLIPPINGS, OBSIDIAN_VAULT, to_vault_relative
except ImportError:
    from config import DB_PATH, OBSIDIAN_CLIPPINGS, OBSIDIAN_VAULT, to_vault_relative
```

Then change the `update_db_path` function (lines 72–87) from:

```python
def update_db_path(db_path: str, old_path: str, new_path: str) -> bool:
    """Update obsidian_note_path in DB. Returns True if a row was updated."""
    conn = sqlite3.connect(db_path)
    try:
        # Try matching on the exact path or just the filename
        # (DB may store a different base dir than where we're reading from)
        basename = os.path.basename(old_path)
        cur = conn.execute(
            "UPDATE links SET obsidian_note_path = ?, updated_at = datetime('now') "
            "WHERE obsidian_note_path LIKE ?",
            (new_path, f"%{basename}"),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
```

to:

```python
def update_db_path(db_path: str, old_path: str, new_path: str) -> bool:
    """Update obsidian_note_path in DB. Returns True if a row was updated.

    Stores vault-relative path. Matches existing rows by filename suffix
    to handle both absolute and already-relative stored paths.
    """
    conn = sqlite3.connect(db_path)
    try:
        basename = os.path.basename(old_path)
        relative_new = to_vault_relative(new_path)
        cur = conn.execute(
            "UPDATE links SET obsidian_note_path = ?, updated_at = datetime('now') "
            "WHERE obsidian_note_path LIKE ?",
            (relative_new, f"%{basename}"),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/pipeline/test_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/backfill_date_folders.py tests/pipeline/test_backfill.py
git commit -m "refactor: backfill script stores vault-relative paths in DB"
```

---

### Task 6: Migrate Existing DB Rows

**Files:**
- Create: `pipeline/migrate_relative_paths.py`

- [ ] **Step 1: Write the migration script**

Create `pipeline/migrate_relative_paths.py`:

```python
#!/usr/bin/env python3
"""One-time migration: convert absolute obsidian_note_path values to vault-relative.

Usage:
    python pipeline/migrate_relative_paths.py                # dry run
    python pipeline/migrate_relative_paths.py --apply        # apply changes
    python pipeline/migrate_relative_paths.py --apply --db /path/to/db
"""

import argparse
import sqlite3
import sys

try:
    from pipeline.config import DB_PATH, OBSIDIAN_VAULT, to_vault_relative
except ImportError:
    from config import DB_PATH, OBSIDIAN_VAULT, to_vault_relative


def main():
    parser = argparse.ArgumentParser(description="Migrate obsidian_note_path to vault-relative")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--db", default=DB_PATH, help="Path to crows-nest DB")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, obsidian_note_path FROM links WHERE obsidian_note_path IS NOT NULL AND obsidian_note_path != ''"
    ).fetchall()

    converted = 0
    already_relative = 0

    for row in rows:
        link_id = row["id"]
        old_path = row["obsidian_note_path"]
        new_path = to_vault_relative(old_path)

        if new_path == old_path:
            already_relative += 1
            continue

        if args.apply:
            conn.execute(
                "UPDATE links SET obsidian_note_path = ?, updated_at = datetime('now') WHERE id = ?",
                (new_path, link_id),
            )
            print(f"  CONVERTED id={link_id}: {old_path}")
            print(f"         -> {new_path}")
        else:
            print(f"  WOULD CONVERT id={link_id}: {old_path}")
            print(f"             -> {new_path}")

        converted += 1

    if args.apply:
        conn.commit()

    conn.close()

    print()
    mode = "Converted" if args.apply else "Would convert"
    print(f"{mode}: {converted}")
    print(f"Already relative: {already_relative}")
    print(f"Total rows checked: {len(rows)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry run the migration**

Run: `cd /Volumes/Mark's\ SSD/Developer/second-brain/crows-nest && source .venv/bin/activate && python pipeline/migrate_relative_paths.py`
Expected: Lists all rows that would be converted, showing the absolute path stripped to relative

- [ ] **Step 3: Apply the migration**

Run: `python pipeline/migrate_relative_paths.py --apply`
Expected: Output shows all rows converted. Verify with:
```bash
sqlite3 data/crows-nest.db "SELECT id, obsidian_note_path FROM links WHERE obsidian_note_path LIKE '/%' LIMIT 5"
```
Expected: No rows returned (no absolute paths remain)

- [ ] **Step 4: Commit**

```bash
git add pipeline/migrate_relative_paths.py
git commit -m "feat: add one-time migration script for vault-relative paths"
```

---

### Task 7: Run Backfill (Date Folders)

This task is operational — no code changes, just running the backfill script that was created earlier and updated in Task 5.

- [ ] **Step 1: Dry run**

Run: `python pipeline/backfill_date_folders.py`
Expected: Lists ~124 notes that would move to `YYYY/MM/DD` subfolders

- [ ] **Step 2: Apply**

Run: `python pipeline/backfill_date_folders.py --apply`
Expected: All flat notes moved, DB updated with vault-relative paths

- [ ] **Step 3: Verify**

```bash
# No flat .md files should remain
ls /Users/mriechers/Developer/second-brain/obsidian/MarkBrain/2\ -\ AREAS/INTERNET\ CLIPPINGS/*.md 2>/dev/null | wc -l
# Expected: 0

# Date folders should contain notes
ls /Users/mriechers/Developer/second-brain/obsidian/MarkBrain/2\ -\ AREAS/INTERNET\ CLIPPINGS/2026/03/
# Expected: folders like 23, 24, 26

# DB paths should all be relative
sqlite3 data/crows-nest.db "SELECT obsidian_note_path FROM links WHERE obsidian_note_path LIKE '/%' LIMIT 1"
# Expected: no rows
```

---

### Task 8: Full Integration Smoke Test

Verify the end-to-end pipeline still works with vault-relative paths.

- [ ] **Step 1: Run all existing tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Queue a test URL and run through the pipeline**

```bash
python pipeline/add_link.py "https://example.com/vault-relative-test"
python pipeline/status.py
```
Expected: New link shows as `pending` in the dashboard

- [ ] **Step 3: Check that recent notes in DB have relative paths**

```bash
sqlite3 data/crows-nest.db "SELECT id, obsidian_note_path FROM links WHERE obsidian_note_path IS NOT NULL ORDER BY id DESC LIMIT 5"
```
Expected: All paths look like `2 - AREAS/INTERNET CLIPPINGS/2026/04/12/note.md` (no leading `/`)

- [ ] **Step 4: Final commit — clean up test link**

```bash
sqlite3 data/crows-nest.db "DELETE FROM links WHERE url = 'https://example.com/vault-relative-test'"
```
