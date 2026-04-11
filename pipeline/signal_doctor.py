"""Diagnostic CLI for the Signal listener's signal-cli integration.

Runs through the most common failure modes for the Crow's Nest Signal
listener and prints a verdict for each, along with specific recovery
steps. Intended to be run by hand when the pipeline stops ingesting
Signal messages, but can also be invoked by ``pipeline/status.py
--health`` as an escalation step.

Exit code:
    0 — every check passed (signal-cli is ready to receive messages)
    1 — one or more checks failed
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# Allow running directly from any working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SIGNAL_HEALTH_FILE  # noqa: E402
from keychain_secrets import get_secret  # noqa: E402


SIGNAL_CLI = "signal-cli"
SIGNAL_DATA_DIR = os.path.expanduser("~/.local/share/signal-cli")


class CheckResult:
    """Result of a single diagnostic check."""

    __slots__ = ("name", "ok", "detail", "recovery")

    def __init__(self, name: str, ok: bool, detail: str, recovery: str = ""):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.recovery = recovery


def check_signal_user() -> CheckResult:
    """Confirm the SIGNAL_USER phone number is configured."""
    phone = get_secret("SIGNAL_USER") or ""
    if not phone:
        return CheckResult(
            "SIGNAL_USER configured",
            ok=False,
            detail="not set in Keychain or environment",
            recovery=(
                "Set your Signal phone number (E.164 format, e.g. +16085551234):\n"
                "  macOS: security add-generic-password -a \"$USER\" "
                "-s developer.workspace.SIGNAL_USER -w +16085551234 -U\n"
                "  Linux: export SIGNAL_USER=+16085551234"
            ),
        )
    return CheckResult(
        "SIGNAL_USER configured",
        ok=True,
        detail=phone,
    )


def check_signal_cli_binary() -> CheckResult:
    """Confirm the signal-cli binary is on PATH."""
    path = shutil.which(SIGNAL_CLI)
    if not path:
        return CheckResult(
            "signal-cli binary",
            ok=False,
            detail="not found on PATH",
            recovery=(
                "Install signal-cli:\n"
                "  macOS: brew install signal-cli\n"
                "  Linux: see https://github.com/AsamK/signal-cli/wiki/Installation"
            ),
        )
    return CheckResult("signal-cli binary", ok=True, detail=path)


def check_data_directory() -> CheckResult:
    """Confirm signal-cli's data directory exists."""
    if not os.path.isdir(SIGNAL_DATA_DIR):
        return CheckResult(
            "signal-cli data directory",
            ok=False,
            detail=f"missing: {SIGNAL_DATA_DIR}",
            recovery=(
                "signal-cli has never been registered on this machine. "
                "Link it as a secondary device to your phone:\n"
                "  signal-cli link -n \"crows-nest\"\n"
                "then scan the QR code it prints from the Signal mobile app."
            ),
        )
    return CheckResult(
        "signal-cli data directory",
        ok=True,
        detail=SIGNAL_DATA_DIR,
    )


def check_receive(phone: str) -> CheckResult:
    """Run a short `signal-cli receive` to confirm the account is live."""
    cmd = [SIGNAL_CLI, "-u", phone, "-o", "json", "receive", "--timeout", "1"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return CheckResult(
            "signal-cli receive",
            ok=False,
            detail="signal-cli binary vanished between checks",
            recovery="Re-install signal-cli and retry.",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "signal-cli receive",
            ok=False,
            detail="receive hung for >10s (timed out)",
            recovery=(
                "Network/server hang. Retry later; if persistent, "
                "check your internet connection and Signal's status page."
            ),
        )

    stderr = (result.stderr or "").strip()
    if result.returncode == 0:
        return CheckResult(
            "signal-cli receive",
            ok=True,
            detail="account reached Signal servers successfully",
        )

    if "not registered" in stderr.lower():
        return CheckResult(
            "signal-cli receive",
            ok=False,
            detail=f"account {phone} is not registered",
            recovery=(
                "Your linked device has been invalidated. Re-link it:\n"
                f"  signal-cli -a \"{phone}\" link -n \"crows-nest\"\n"
                "then scan the QR code from the Signal mobile app "
                "(Settings → Linked Devices → Link New Device)."
            ),
        )

    return CheckResult(
        "signal-cli receive",
        ok=False,
        detail=f"exit {result.returncode}: {stderr[:200]}",
        recovery="Inspect the stderr above and consult signal-cli docs.",
    )


def read_health() -> dict:
    """Return the current contents of the signal-health.json file."""
    if not os.path.exists(SIGNAL_HEALTH_FILE):
        return {}
    try:
        with open(SIGNAL_HEALTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def summarize_health(health: dict) -> str:
    """Produce a human-readable summary of the listener's current health."""
    if not health:
        return "no health file found (listener has not run yet)"

    status = health.get("status", "unknown")
    lines = [f"status: {status}"]
    if health.get("error"):
        lines.append(f"error:  {health['error']}")
    if health.get("message"):
        lines.append(f"detail: {health['message']}")
    if health.get("last_success_at"):
        try:
            last = datetime.fromisoformat(health["last_success_at"])
            age = (datetime.now(timezone.utc) - last).total_seconds() / 60
            lines.append(f"last success: {health['last_success_at']} ({int(age)}m ago)")
        except ValueError:
            lines.append(f"last success: {health['last_success_at']}")
    streak = health.get("consecutive_failures")
    if streak:
        lines.append(f"consecutive failures: {streak}")
    return "\n  ".join(lines)


def run_checks() -> list[CheckResult]:
    """Run all diagnostic checks in order and return results."""
    results: list[CheckResult] = []

    user_result = check_signal_user()
    results.append(user_result)

    binary_result = check_signal_cli_binary()
    results.append(binary_result)

    results.append(check_data_directory())

    if user_result.ok and binary_result.ok:
        results.append(check_receive(user_result.detail))

    return results


def main() -> None:
    print()
    print("CROW'S NEST — Signal Doctor")
    print("=" * 40)
    print()

    health = read_health()
    print("Listener health file:")
    print("  " + summarize_health(health))
    print()

    results = run_checks()
    all_ok = True

    for res in results:
        mark = "OK  " if res.ok else "FAIL"
        print(f"  [{mark}] {res.name}: {res.detail}")
        if not res.ok:
            all_ok = False
            if res.recovery:
                for line in res.recovery.splitlines():
                    print(f"         {line}")
            print()

    print()
    if all_ok:
        print("  DIAGNOSIS: signal-cli looks healthy")
    else:
        print("  DIAGNOSIS: signal-cli needs attention — see failures above")
    print()

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
