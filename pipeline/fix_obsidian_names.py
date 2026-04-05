#!/usr/bin/env python3
"""Fix Obsidian note filenames and weekly log wikilinks containing banned characters.

Scans the Clippings directory for filenames with characters Obsidian prohibits,
renames them, and updates any wikilinks in weekly log files to match.

Usage:
    python fix_obsidian_names.py          # dry run (default)
    python fix_obsidian_names.py --apply  # apply changes
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from config import OBSIDIAN_VAULT

CLIPPINGS_DIR = os.path.join(OBSIDIAN_VAULT, "2 - AREAS", "CLIPPINGS - Need Sorting")
INBOX_DIR = os.path.join(OBSIDIAN_VAULT, "0 - INBOX")

UNSAFE = re.compile(r'[<>:"/\\|?*\[\]#^]')


def sanitize(name: str) -> str:
    result = UNSAFE.sub("", name)
    result = re.sub(r"\s+", " ", result)
    return result.strip()


def fix_filenames(apply: bool) -> dict[str, str]:
    """Rename clipping files with banned characters. Returns {old_stem: new_stem}."""
    renames = {}
    if not os.path.isdir(CLIPPINGS_DIR):
        print(f"Clippings directory not found: {CLIPPINGS_DIR}")
        return renames

    for fname in os.listdir(CLIPPINGS_DIR):
        if not fname.endswith(".md"):
            continue
        stem = fname[:-3]
        clean = sanitize(stem)
        if clean != stem:
            renames[stem] = clean
            old_path = os.path.join(CLIPPINGS_DIR, fname)
            new_path = os.path.join(CLIPPINGS_DIR, f"{clean}.md")

            # Handle collision
            counter = 1
            while os.path.exists(new_path) and new_path != old_path:
                new_path = os.path.join(CLIPPINGS_DIR, f"{clean} ({counter}).md")
                counter += 1

            if apply:
                os.rename(old_path, new_path)
                print(f"  RENAMED: {fname} -> {os.path.basename(new_path)}")
            else:
                print(f"  WOULD RENAME: {fname} -> {os.path.basename(new_path)}")

    # Also check ROUNDUP subdirectory
    roundup_dir = os.path.join(CLIPPINGS_DIR, "ROUNDUP")
    if os.path.isdir(roundup_dir):
        for fname in os.listdir(roundup_dir):
            if not fname.endswith(".md"):
                continue
            stem = fname[:-3]
            clean = sanitize(stem)
            if clean != stem:
                renames[stem] = clean
                old_path = os.path.join(roundup_dir, fname)
                new_path = os.path.join(roundup_dir, f"{clean}.md")

                counter = 1
                while os.path.exists(new_path) and new_path != old_path:
                    new_path = os.path.join(roundup_dir, f"{clean} ({counter}).md")
                    counter += 1

                if apply:
                    os.rename(old_path, new_path)
                    print(f"  RENAMED: ROUNDUP/{fname} -> {os.path.basename(new_path)}")
                else:
                    print(f"  WOULD RENAME: ROUNDUP/{fname} -> {os.path.basename(new_path)}")

    return renames


def fix_weekly_logs(apply: bool, renames: dict[str, str]) -> None:
    """Fix wikilinks in weekly log files."""
    if not os.path.isdir(INBOX_DIR):
        print(f"Inbox directory not found: {INBOX_DIR}")
        return

    for fname in os.listdir(INBOX_DIR):
        if not fname.startswith("Weekly Links") or not fname.endswith(".md"):
            continue

        filepath = os.path.join(INBOX_DIR, fname)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        original = content

        # Fix all wikilinks that contain banned characters
        def fix_wikilink(m: re.Match) -> str:
            inner = m.group(1)
            clean = sanitize(inner)
            # If we renamed the file, use the renamed version
            if inner in renames:
                clean = renames[inner]
            return f"[[{clean}]]"

        content = re.sub(r"\[\[([^\]]+)\]\]", fix_wikilink, content)

        if content != original:
            changes = []
            for old_line, new_line in zip(original.splitlines(), content.splitlines()):
                if old_line != new_line:
                    changes.append(f"    - {old_line.strip()}")
                    changes.append(f"    + {new_line.strip()}")

            if apply:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"  FIXED: {fname}")
            else:
                print(f"  WOULD FIX: {fname}")

            for line in changes:
                print(line)


def main():
    apply = "--apply" in sys.argv

    if not apply:
        print("DRY RUN (pass --apply to make changes)\n")

    print("=== Checking clipping filenames ===")
    renames = fix_filenames(apply)
    if not renames:
        print("  All filenames OK")

    print("\n=== Checking weekly log wikilinks ===")
    fix_weekly_logs(apply, renames)

    if not apply and renames:
        print(f"\n{len(renames)} file(s) would be renamed. Run with --apply to fix.")


if __name__ == "__main__":
    main()
