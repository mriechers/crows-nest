"""Scheduler for crows-nest pipeline jobs.

All 6 pipeline stages run as subprocesses — they are too heavy
(video download, Whisper transcription, Claude API calls) for in-process async.
"""

from __future__ import annotations

from pathlib import Path

from service_base import Scheduler


def create_scheduler() -> Scheduler:
    """Build and return the crows-nest job scheduler."""
    scheduler = Scheduler()

    project_root = Path(__file__).resolve().parent.parent.parent
    venv_python = str(project_root / ".venv" / "bin" / "python")
    cwd = str(project_root)

    def cmd(script: str, *args: str) -> list[str]:
        return [venv_python, str(project_root / "pipeline" / script)] + list(args)

    scheduler.register_subprocess(
        "ingest-poll",
        cmd("ingest_poller.py"),
        interval_seconds=300,
        run_at_startup=True,
        cwd=cwd,
    )
    scheduler.register_subprocess(
        "obsidian-scan",
        cmd("obsidian_scanner.py"),
        interval_seconds=300,
        run_at_startup=True,
        cwd=cwd,
    )
    scheduler.register_subprocess(
        "process",
        cmd("processor.py", "--drain"),
        interval_seconds=1800,
        run_at_startup=True,
        cwd=cwd,
    )
    scheduler.register_subprocess(
        "rss-refresh",
        cmd("rss_listener.py", "--refresh"),
        interval_seconds=4 * 3600,
        cwd=cwd,
    )
    scheduler.register_subprocess(
        "summarize",
        cmd("summarizer.py", "--drain"),
        interval_seconds=2 * 3600,
        run_at_startup=True,
        cwd=cwd,
    )
    scheduler.register_subprocess(
        "archive",
        cmd("archiver.py"),
        interval_seconds=24 * 3600,
        cwd=cwd,
    )

    return scheduler
