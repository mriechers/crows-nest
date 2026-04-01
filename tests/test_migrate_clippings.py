import os
import tempfile
import shutil
from pipeline.migrate_clippings import migrate_clippings

def test_migrate_moves_files_and_updates_frontmatter(tmp_path):
    """Migration moves clippings and updates para: frontmatter."""
    src = tmp_path / "0 - INBOX" / "CLIPPINGS"
    dst = tmp_path / "2 - AREAS" / "CLIPPINGS - Need Sorting"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)

    note = src / "Test Note.md"
    note.write_text(
        "---\ntitle: Test\npara: inbox\ntags:\n  - clippings\n---\n\n# Test\nBody text\n"
    )

    roundup = src / "ROUNDUP"
    roundup.mkdir()
    roundup_note = roundup / "Roundup 1.md"
    roundup_note.write_text(
        "---\ntitle: Roundup\npara: inbox\n---\n\n# Roundup\n"
    )

    result = migrate_clippings(source=str(src), destination=str(dst))

    assert not note.exists()
    assert (dst / "Test Note.md").exists()
    assert (dst / "ROUNDUP" / "Roundup 1.md").exists()

    content = (dst / "Test Note.md").read_text()
    assert "para: areas" in content
    assert "para: inbox" not in content

    assert result["moved"] == 2


def test_migrate_handles_empty_source(tmp_path):
    """Migration handles empty source directory gracefully."""
    src = tmp_path / "empty"
    src.mkdir()
    dst = tmp_path / "dest"
    dst.mkdir()

    result = migrate_clippings(source=str(src), destination=str(dst))
    assert result["moved"] == 0
