"""Tests for pipeline/signal_doctor.py — diagnostic CLI for signal-cli."""

import json
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

import signal_doctor
from signal_doctor import (
    CheckResult,
    check_data_directory,
    check_receive,
    check_signal_cli_binary,
    check_signal_user,
    read_health,
    summarize_health,
)


# --- check_signal_user ---

def test_check_signal_user_missing():
    with patch("signal_doctor.get_secret", return_value=""):
        r = check_signal_user()
    assert r.ok is False
    assert "Keychain" in r.recovery or "SIGNAL_USER" in r.recovery


def test_check_signal_user_present():
    with patch("signal_doctor.get_secret", return_value="+16085551234"):
        r = check_signal_user()
    assert r.ok is True
    assert r.detail == "+16085551234"


# --- check_signal_cli_binary ---

def test_check_signal_cli_binary_missing():
    with patch("signal_doctor.shutil.which", return_value=None):
        r = check_signal_cli_binary()
    assert r.ok is False
    assert "install" in r.recovery.lower()


def test_check_signal_cli_binary_present():
    with patch("signal_doctor.shutil.which", return_value="/usr/local/bin/signal-cli"):
        r = check_signal_cli_binary()
    assert r.ok is True
    assert "/usr/local/bin/signal-cli" in r.detail


# --- check_data_directory ---

def test_check_data_directory_missing(tmp_path):
    missing = str(tmp_path / "nope")
    with patch("signal_doctor.SIGNAL_DATA_DIR", missing):
        r = check_data_directory()
    assert r.ok is False
    assert "link" in r.recovery.lower()


def test_check_data_directory_present(tmp_path):
    existing = str(tmp_path)
    with patch("signal_doctor.SIGNAL_DATA_DIR", existing):
        r = check_data_directory()
    assert r.ok is True


# --- check_receive ---

def test_check_receive_success():
    with patch("signal_doctor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        r = check_receive("+16085551234")
    assert r.ok is True


def test_check_receive_not_registered():
    with patch("signal_doctor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="User +16085551234 is not registered.",
        )
        r = check_receive("+16085551234")
    assert r.ok is False
    assert "not registered" in r.detail.lower()
    assert "link" in r.recovery.lower()


def test_check_receive_other_error():
    with patch("signal_doctor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=2,
            stderr="some other failure",
        )
        r = check_receive("+16085551234")
    assert r.ok is False
    assert "exit 2" in r.detail


def test_check_receive_timeout():
    import subprocess as _sp
    with patch("signal_doctor.subprocess.run") as mock_run:
        mock_run.side_effect = _sp.TimeoutExpired(cmd=["signal-cli"], timeout=10)
        r = check_receive("+16085551234")
    assert r.ok is False
    assert "timed out" in r.detail.lower()


# --- read_health / summarize_health ---

def test_read_health_missing_file(tmp_path):
    path = str(tmp_path / "signal-health.json")
    with patch("signal_doctor.SIGNAL_HEALTH_FILE", path):
        assert read_health() == {}


def test_read_health_returns_parsed(tmp_path):
    path = tmp_path / "signal-health.json"
    path.write_text(json.dumps({"status": "ok", "timestamp": "2026-04-11T00:00:00+00:00"}))
    with patch("signal_doctor.SIGNAL_HEALTH_FILE", str(path)):
        health = read_health()
    assert health["status"] == "ok"


def test_summarize_health_empty():
    assert "no health file" in summarize_health({})


def test_summarize_health_error_state():
    summary = summarize_health({
        "status": "degraded",
        "error": "not_registered",
        "message": "run signal-cli register",
        "consecutive_failures": 4,
    })
    assert "degraded" in summary
    assert "not_registered" in summary
    assert "4" in summary


# --- run_checks composition ---

def test_run_checks_skips_receive_when_prereqs_fail():
    with (
        patch("signal_doctor.get_secret", return_value=""),
        patch("signal_doctor.shutil.which", return_value=None),
        patch("signal_doctor.os.path.isdir", return_value=False),
    ):
        results = signal_doctor.run_checks()
    names = [r.name for r in results]
    assert "signal-cli receive" not in names  # skipped when prereqs fail


def test_run_checks_runs_receive_when_prereqs_pass(tmp_path):
    with (
        patch("signal_doctor.get_secret", return_value="+16085551234"),
        patch("signal_doctor.shutil.which", return_value="/usr/local/bin/signal-cli"),
        patch("signal_doctor.SIGNAL_DATA_DIR", str(tmp_path)),
        patch("signal_doctor.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        results = signal_doctor.run_checks()
    names = [r.name for r in results]
    assert "signal-cli receive" in names
    assert all(r.ok for r in results)
