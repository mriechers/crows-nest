"""One-time migration: move clippings from INBOX to AREAS."""

import os
import re
import shutil
import sys

try:
    from config import OBSIDIAN_VAULT
except ImportError:
    from pipeline.config import OBSIDIAN_VAULT


def migrate_clippings(
    source: str | None = None,
    destination: str | None = None,
) -> dict:
    """Move all clippings and update para: frontmatter.

    Args:
        source: Override source directory (default: 0 - INBOX/CLIPPINGS)
        destination: Override destination (default: 2 - AREAS/CLIPPINGS - Need Sorting)

    Returns:
        dict with keys: moved (int), errors (list[str])
    """
    if source is None:
        source = os.path.join(OBSIDIAN_VAULT, "0 - INBOX", "CLIPPINGS")
    if destination is None:
        destination = os.path.join(OBSIDIAN_VAULT, "2 - AREAS", "CLIPPINGS - Need Sorting")

    if not os.path.isdir(source):
        print(f"Source not found: {source}")
        return {"moved": 0, "errors": []}

    os.makedirs(destination, exist_ok=True)

    moved = 0
    errors = []

    for root, dirs, files in os.walk(source):
        rel_root = os.path.relpath(root, source)
        dst_root = os.path.join(destination, rel_root) if rel_root != "." else destination
        os.makedirs(dst_root, exist_ok=True)

        for filename in files:
            if not filename.endswith(".md"):
                continue
            src_path = os.path.join(root, filename)
            dst_path = os.path.join(dst_root, filename)

            try:
                content = open(src_path, "r", encoding="utf-8").read()
                content = re.sub(
                    r"^(para:\s*)inbox\s*$",
                    r"\1areas",
                    content,
                    count=1,
                    flags=re.MULTILINE,
                )
                open(dst_path, "w", encoding="utf-8").write(content)
                os.remove(src_path)
                moved += 1
                print(f"  Moved: {filename}")
            except Exception as e:
                errors.append(f"{filename}: {e}")

    try:
        shutil.rmtree(source)
        print(f"Removed empty source: {source}")
    except OSError:
        pass

    print(f"\nMigration complete: {moved} files moved, {len(errors)} errors")
    return {"moved": moved, "errors": errors}


if __name__ == "__main__":
    print("Migrating clippings from INBOX to AREAS...")
    result = migrate_clippings()
    if result["errors"]:
        print("\nErrors:")
        for err in result["errors"]:
            print(f"  {err}")
        sys.exit(1)
