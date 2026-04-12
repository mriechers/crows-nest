"""
Crow's Nest pipeline status dashboard.

Prints status counts, recent activity, and failed links.
Usage: python3 status.py [--db PATH]
       python3 status.py --health        (exit 0 if healthy, 1 if not)
"""

import argparse
import os
import sys
from datetime import datetime, timezone

# Allow running directly from any working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LOG_DIR
from db import DB_PATH, get_connection, init_db


STATUS_ICONS = {
    "pending":      "⏳",
    "downloading":  "⬇️",
    "transcribed":  "📝",
    "summarizing":  "🤖",
    "summarized":   "✅",
    "archived":     "📦",
    "failed":       "❌",
}

# Order to display statuses
STATUS_ORDER = [
    "pending",
    "downloading",
    "transcribed",
    "summarizing",
    "summarized",
    "archived",
    "failed",
]


def _truncate(url: str, max_len: int = 40) -> str:
    if len(url) <= max_len:
        return url
    return url[:max_len - 1] + "…"


def print_dashboard(db_path: str) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        # --- Status counts ---
        cursor = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM links GROUP BY status"
        )
        counts = {row["status"]: row["cnt"] for row in cursor.fetchall()}

        total = sum(counts.values())

        print()
        print("CROW'S NEST — Pipeline Status")
        print("=" * 40)
        print()

        for status in STATUS_ORDER:
            icon = STATUS_ICONS.get(status, "  ")
            cnt = counts.get(status, 0)
            print(f"  {icon}  {status:<14} {cnt:>4}")

        # Any statuses not in our known list
        for status, cnt in sorted(counts.items()):
            if status not in STATUS_ORDER:
                icon = STATUS_ICONS.get(status, "  ")
                print(f"  {icon}  {status:<14} {cnt:>4}")

        print()
        print(f"  {'TOTAL':<16} {total:>4}")
        print()

        # --- Recent activity ---
        print("Recent Activity (last 5)")
        print("-" * 40)
        cursor = conn.execute(
            """
            SELECT url, status, content_type, updated_at
            FROM links
            ORDER BY updated_at DESC
            LIMIT 5
            """
        )
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                url_display = _truncate(row["url"], 40)
                status_icon = STATUS_ICONS.get(row["status"], "  ")
                content_type = row["content_type"] or "unknown"
                print(f"  {status_icon} {url_display:<41} {row['status']:<14} {content_type}")
        else:
            print("  (no links yet)")
        print()

        # --- Failed links ---
        cursor = conn.execute(
            "SELECT url, error FROM links WHERE status = 'failed' ORDER BY updated_at DESC"
        )
        failed = cursor.fetchall()
        if failed:
            print("Failed Links")
            print("-" * 40)
            for row in failed:
                url_display = _truncate(row["url"], 60)
                error = row["error"] or "(no error message)"
                print(f"  {STATUS_ICONS['failed']} {url_display}")
                print(f"       {error}")
            print()

    finally:
        conn.close()


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Pipeline scripts that must have a __main__ guard, mapped to their log file
# and the max age (in minutes) before the log is considered stale.
PIPELINE_JOBS = {
    "processor.py": {
        "log": os.path.join(LOG_DIR, "launchd-crows-nest-processor.log"),
        "max_stale_minutes": 60,     # runs every 30 min
    },
    "summarizer.py": {
        "log": os.path.join(LOG_DIR, "launchd-crows-nest-summarizer.log"),
        "max_stale_minutes": 180,    # runs every 2 hr
    },
    "archiver.py": {
        "log": os.path.join(LOG_DIR, "launchd-crows-nest-archiver.log"),
        "max_stale_minutes": 1500,   # runs daily at 3 AM
    },
    "ingest_poller.py": {
        "log": os.path.join(LOG_DIR, "launchd-ingest-poller.log"),
        "max_stale_minutes": 15,     # runs every 5 min
    },
    "obsidian_scanner.py": {
        "log": os.path.join(LOG_DIR, "launchd-obsidian-scanner.log"),
        "max_stale_minutes": 15,     # runs every 5 min
    },
}


def check_main_guards() -> list[str]:
    """Verify every pipeline script has an `if __name__ == "__main__":` block."""
    problems = []
    for script_name in PIPELINE_JOBS:
        path = os.path.join(SCRIPT_DIR, script_name)
        if not os.path.exists(path):
            problems.append(f"{script_name}: file not found")
            continue
        with open(path, "r", encoding="utf-8") as f:
            contents = f.read()
        if 'if __name__ ==' not in contents:
            problems.append(f"{script_name}: missing if __name__ == \"__main__\" guard")
    return problems


def check_log_freshness() -> list[str]:
    """Check that each log file has been modified recently enough."""
    problems = []
    now = datetime.now(timezone.utc)
    for script_name, info in PIPELINE_JOBS.items():
        log_path = info["log"]
        max_stale = info["max_stale_minutes"]
        if not os.path.exists(log_path):
            problems.append(f"{script_name}: log file missing ({log_path})")
            continue
        stat = os.stat(log_path)
        if stat.st_size == 0:
            problems.append(f"{script_name}: log file is empty (never ran successfully)")
            continue
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        age_minutes = (now - mtime).total_seconds() / 60
        if age_minutes > max_stale:
            hours = int(age_minutes // 60)
            mins = int(age_minutes % 60)
            problems.append(
                f"{script_name}: log stale ({hours}h{mins}m old, threshold {max_stale}m)"
            )
    return problems


def print_health() -> bool:
    """Run all health checks, print results, return True if healthy."""
    all_ok = True

    print()
    print("CROW'S NEST — Health Check")
    print("=" * 40)
    print()

    guard_problems = check_main_guards()
    if guard_problems:
        all_ok = False
        print("  __main__ guard check:")
        for p in guard_problems:
            print(f"    FAIL  {p}")
    else:
        print("  __main__ guards:  OK (all scripts have entry points)")

    print()

    freshness_problems = check_log_freshness()
    if freshness_problems:
        all_ok = False
        print("  Log freshness check:")
        for p in freshness_problems:
            print(f"    WARN  {p}")
    else:
        print("  Log freshness:    OK (all logs recently updated)")

    print()

    if all_ok:
        print("  STATUS: HEALTHY")
    else:
        print("  STATUS: PROBLEMS DETECTED")
    print()

    return all_ok


def print_sources(db_path: str, days: int = 7) -> None:
    """Print a breakdown of links by source_type for the last N days."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT source_type, COUNT(*) AS cnt
            FROM links
            WHERE created_at >= datetime('now', ?)
            GROUP BY source_type
            ORDER BY cnt DESC
            """,
            (f"-{days} days",),
        )
        rows = cursor.fetchall()

        print()
        print(f"CROW'S NEST — Sources (last {days} days)")
        print("=" * 40)
        print()

        if rows:
            total = sum(row["cnt"] for row in rows)
            for row in rows:
                source = row["source_type"] or "unknown"
                cnt = row["cnt"]
                bar = "█" * min(cnt, 30)
                print(f"  {source:<16} {cnt:>4}  {bar}")
            print()
            print(f"  {'TOTAL':<16} {total:>4}")
        else:
            print("  (no links in this period)")
        print()

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crow's Nest pipeline status dashboard"
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"Path to the SQLite database (default: {DB_PATH})",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run health checks and exit with code 0 (healthy) or 1 (problems)",
    )
    parser.add_argument(
        "--sources",
        action="store_true",
        help="Show breakdown of links by source type for the last 7 days",
    )
    args = parser.parse_args()

    if args.health:
        healthy = print_health()
        sys.exit(0 if healthy else 1)

    if args.sources:
        print_sources(args.db)
        return

    print_dashboard(args.db)


if __name__ == "__main__":
    main()
