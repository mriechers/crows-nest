"""Tests for signal_listener.py — JSON-mode parsing, image detection, batching."""

import json
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from signal_listener import (
    parse_signal_message,
    _parse_json_output,
    _batch_image_messages,
    receive_messages,
    _write_health,
    _read_health,
    _record_success,
    _record_failure,
    DEGRADED_FAILURE_THRESHOLD,
    resolve_sender,
)


# --- resolve_sender ---

def test_resolve_sender_prefers_name():
    assert resolve_sender("+16085551234", "Alice") == "Alice"

def test_resolve_sender_falls_back_to_phone():
    assert resolve_sender("+16085551234", "") == "+16085551234"

def test_resolve_sender_none_name():
    assert resolve_sender("+16085551234", None) == "+16085551234"

def test_resolve_sender_whitespace_only():
    assert resolve_sender("+16085551234", "   ") == "+16085551234"

def test_resolve_sender_strips_whitespace():
    assert resolve_sender("+16085551234", "  Bob  ") == "Bob"

def test_resolve_sender_phone_echo():
    """When Signal echoes the phone number as the name, use the phone."""
    assert resolve_sender("+16085551234", "+16085551234") == "+16085551234"

def test_resolve_sender_both_empty():
    assert resolve_sender("", "") == ""

def test_resolve_sender_name_no_phone():
    assert resolve_sender("", "Alice") == "Alice"


# --- parse_signal_message (unchanged) ---

def test_parse_message_with_url():
    result = parse_signal_message("yo check this out https://example.com")
    assert result["urls"] == ["https://example.com"]
    assert "https://example.com" in result["context"]

def test_parse_message_no_url():
    result = parse_signal_message("hey what's up, no links here")
    assert result["urls"] == []

def test_parse_message_multiple_urls():
    result = parse_signal_message("two links: https://example.com and https://another.org/page")
    assert len(result["urls"]) == 2

def test_parse_message_none():
    result = parse_signal_message(None)
    assert result["urls"] == []


# --- _parse_json_output ---

SAMPLE_JSON_LINES = "\n".join([
    json.dumps({
        "envelope": {
            "sourceNumber": "+11234567890",
            "sourceName": "Bob",
            "timestamp": 1773529879987,
            "dataMessage": {
                "message": "check this out https://example.com",
                "timestamp": 1773529879987,
            }
        }
    }),
    json.dumps({
        "envelope": {
            "sourceNumber": "+19876543210",
            "sourceName": "Alice",
            "timestamp": 1773530810578,
            "dataMessage": {
                "message": "cool video https://youtu.be/abc123",
                "timestamp": 1773530810578,
            }
        }
    }),
])


def test_parse_json_output_text_messages():
    """JSON parser extracts sender, body, and timestamp from text messages."""
    messages = _parse_json_output(SAMPLE_JSON_LINES)
    assert len(messages) == 2
    assert messages[0]["sender_id"] == "+11234567890"
    assert messages[0]["sender_name"] == "Bob"
    assert "https://example.com" in messages[0]["message"]
    assert messages[0]["timestamp"] == 1773529879987
    assert messages[0]["attachments"] == []


def test_parse_json_output_with_attachments():
    """JSON parser extracts attachment metadata and constructs path."""
    line = json.dumps({
        "envelope": {
            "sourceNumber": "+11234567890",
            "sourceName": "Bob",
            "timestamp": 1773529879987,
            "dataMessage": {
                "message": "look at this",
                "timestamp": 1773529879987,
                "attachments": [
                    {
                        "contentType": "image/jpeg",
                        "id": "abc123.jpg",
                        "size": 50000,
                        "width": 1170,
                        "height": 2532,
                    }
                ],
            }
        }
    })
    messages = _parse_json_output(line)
    assert len(messages) == 1
    assert len(messages[0]["attachments"]) == 1
    assert messages[0]["attachments"][0]["content_type"] == "image/jpeg"
    assert messages[0]["attachments"][0]["id"] == "abc123.jpg"
    # Path must be constructed from the attachments directory
    att_path = messages[0]["attachments"][0]["path"]
    assert att_path.startswith(os.path.expanduser("~/.local/share/signal-cli/attachments"))
    assert att_path.endswith("abc123.jpg")


def test_parse_json_output_skips_receipts():
    """JSON parser ignores envelopes without dataMessage."""
    line = json.dumps({
        "envelope": {
            "sourceNumber": "+11234567890",
            "timestamp": 1773529879987,
            "receiptMessage": {"type": "DELIVERY"},
        }
    })
    messages = _parse_json_output(line)
    assert messages == []


def test_parse_json_output_group_message():
    """JSON parser extracts group name."""
    line = json.dumps({
        "envelope": {
            "sourceNumber": "+11234567890",
            "sourceName": "Bob",
            "timestamp": 1773529879987,
            "dataMessage": {
                "message": "hello group",
                "timestamp": 1773529879987,
                "groupInfo": {"groupName": "Family Chat"},
            }
        }
    })
    messages = _parse_json_output(line)
    assert messages[0]["group_name"] == "Family Chat"


def test_parse_json_output_image_only_message():
    """Image-only message (no body text) is still included."""
    line = json.dumps({
        "envelope": {
            "sourceNumber": "+11234567890",
            "sourceName": "Bob",
            "timestamp": 1773529879987,
            "dataMessage": {
                "message": None,
                "timestamp": 1773529879987,
                "attachments": [
                    {
                        "contentType": "image/png",
                        "id": "img456.png",
                        "size": 30000,
                        "width": 800,
                        "height": 600,
                    }
                ],
            }
        }
    })
    messages = _parse_json_output(line)
    assert len(messages) == 1
    assert messages[0]["message"] == ""
    assert len(messages[0]["attachments"]) == 1
    assert messages[0]["attachments"][0]["content_type"] == "image/png"


# --- _batch_image_messages ---

def test_batch_single_image():
    """Single image message becomes a batch of 1."""
    msgs = [
        {"sender_id": "+1111", "sender_name": "Bob", "message": "",
         "timestamp": 1000000, "group_name": "",
         "attachments": [{"content_type": "image/jpeg", "id": "a.jpg", "path": "/tmp/a.jpg", "width": 100, "height": 100}]},
    ]
    batches, non_image = _batch_image_messages(msgs)
    assert len(batches) == 1
    assert len(batches[0]["attachments"]) == 1
    assert non_image == []


def test_batch_multiple_images_same_sender_within_window():
    """Images from same sender within 3 min are batched together."""
    msgs = [
        {"sender_id": "+1111", "sender_name": "Bob", "message": "first",
         "timestamp": 1000000, "group_name": "",
         "attachments": [{"content_type": "image/jpeg", "id": "a.jpg", "path": "/tmp/a.jpg", "width": 100, "height": 100}]},
        {"sender_id": "+1111", "sender_name": "Bob", "message": "second",
         "timestamp": 1060000, "group_name": "",  # 60 sec later
         "attachments": [{"content_type": "image/jpeg", "id": "b.jpg", "path": "/tmp/b.jpg", "width": 100, "height": 100}]},
    ]
    batches, non_image = _batch_image_messages(msgs)
    assert len(batches) == 1
    assert len(batches[0]["attachments"]) == 2
    assert "first" in batches[0]["context"]
    assert "second" in batches[0]["context"]


def test_batch_breaks_on_time_gap():
    """Images >3 min apart become separate batches."""
    msgs = [
        {"sender_id": "+1111", "sender_name": "Bob", "message": "",
         "timestamp": 1000000, "group_name": "",
         "attachments": [{"content_type": "image/jpeg", "id": "a.jpg", "path": "/tmp/a.jpg", "width": 100, "height": 100}]},
        {"sender_id": "+1111", "sender_name": "Bob", "message": "",
         "timestamp": 1300000, "group_name": "",  # 5 min later
         "attachments": [{"content_type": "image/jpeg", "id": "b.jpg", "path": "/tmp/b.jpg", "width": 100, "height": 100}]},
    ]
    batches, non_image = _batch_image_messages(msgs)
    assert len(batches) == 2


def test_batch_breaks_on_different_sender():
    """Images from different senders are separate batches."""
    msgs = [
        {"sender_id": "+1111", "sender_name": "Bob", "message": "",
         "timestamp": 1000000, "group_name": "",
         "attachments": [{"content_type": "image/jpeg", "id": "a.jpg", "path": "/tmp/a.jpg", "width": 100, "height": 100}]},
        {"sender_id": "+2222", "sender_name": "Alice", "message": "",
         "timestamp": 1060000, "group_name": "",
         "attachments": [{"content_type": "image/jpeg", "id": "b.jpg", "path": "/tmp/b.jpg", "width": 100, "height": 100}]},
    ]
    batches, non_image = _batch_image_messages(msgs)
    assert len(batches) == 2


def test_mixed_messages_separated():
    """Text-only messages go to non_image list; image messages get batched."""
    msgs = [
        {"sender_id": "+1111", "sender_name": "Bob", "message": "https://example.com",
         "timestamp": 1000000, "group_name": "", "attachments": []},
        {"sender_id": "+1111", "sender_name": "Bob", "message": "screenshot",
         "timestamp": 1060000, "group_name": "",
         "attachments": [{"content_type": "image/jpeg", "id": "a.jpg", "path": "/tmp/a.jpg", "width": 100, "height": 100}]},
    ]
    batches, non_image = _batch_image_messages(msgs)
    assert len(batches) == 1
    assert len(non_image) == 1
    assert "https://example.com" in non_image[0]["message"]


# --- receive_messages: fatal error detection ---

@patch("signal_listener.subprocess.run")
def test_receive_returns_none_when_not_registered(mock_run):
    """signal-cli 'not registered' error returns None (fatal)."""
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="User +16124706803 is not registered.",
    )
    result = receive_messages()
    assert result is None


@patch("signal_listener.subprocess.run")
def test_receive_returns_none_on_other_fatal_error(mock_run):
    """Non-zero exit with stderr returns None."""
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="Some other fatal error from signal-cli",
    )
    result = receive_messages()
    assert result is None


@patch("signal_listener.subprocess.run")
def test_receive_populates_last_error_on_not_registered(mock_run):
    """not_registered failures leave structured error info for run()."""
    import signal_listener
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="User +16124706803 is not registered.",
    )
    result = receive_messages()
    assert result is None
    assert signal_listener._LAST_RECEIVE_ERROR["error"] == "not_registered"
    assert "register" in signal_listener._LAST_RECEIVE_ERROR["message"].lower()


@patch("signal_listener.subprocess.run")
def test_receive_populates_last_error_on_subprocess_exception(mock_run):
    """Generic subprocess exceptions are classified as subprocess_error."""
    import signal_listener
    mock_run.side_effect = RuntimeError("boom")
    result = receive_messages()
    assert result is None
    assert signal_listener._LAST_RECEIVE_ERROR["error"] == "subprocess_error"
    assert "boom" in signal_listener._LAST_RECEIVE_ERROR["message"]


@patch("signal_listener.subprocess.run")
def test_receive_resets_last_error_on_success(mock_run):
    """A success after a failure clears the error state dict."""
    import signal_listener
    signal_listener._LAST_RECEIVE_ERROR = {"error": "stale", "message": "old"}
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    receive_messages()
    assert signal_listener._LAST_RECEIVE_ERROR == {}


@patch("signal_listener.subprocess.run")
def test_receive_returns_list_on_success(mock_run):
    """Successful signal-cli invocation returns a list (possibly empty)."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="",
        stderr="",
    )
    result = receive_messages()
    assert result == []


# --- _write_health ---

def test_write_health_creates_file(tmp_path):
    """Health file is written with correct structure."""
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        _write_health("error", "not_registered", "account not registered")

    with open(health_file) as f:
        data = json.load(f)
    assert data["status"] == "error"
    assert data["error"] == "not_registered"
    assert "timestamp" in data


def test_write_health_ok(tmp_path):
    """OK health status has no error fields."""
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        _write_health("ok")

    with open(health_file) as f:
        data = json.load(f)
    assert data["status"] == "ok"
    assert "error" not in data


# --- health state machine: record_success / record_failure / preservation ---

def test_read_health_returns_empty_when_missing(tmp_path):
    """Missing health file yields an empty dict, not an exception."""
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        assert _read_health() == {}


def test_read_health_returns_empty_on_corrupt_file(tmp_path):
    """Corrupt JSON should degrade gracefully to an empty dict."""
    health_file = tmp_path / "signal-health.json"
    health_file.write_text("{not json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", str(health_file)):
        assert _read_health() == {}


def test_record_success_writes_ok_and_resets_streak(tmp_path):
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        # Pretend we had 2 failures already.
        _write_health(
            "error", "timeout", "prior timeout",
            consecutive_failures=2,
        )
        _record_success()

    with open(health_file) as f:
        data = json.load(f)
    assert data["status"] == "ok"
    assert data["consecutive_failures"] == 0
    assert "last_success_at" in data
    assert "error" not in data


def test_record_failure_increments_streak(tmp_path):
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        _record_failure("timeout", "first timeout")
        with open(health_file) as f:
            assert json.load(f)["consecutive_failures"] == 1

        _record_failure("timeout", "second timeout")
        with open(health_file) as f:
            assert json.load(f)["consecutive_failures"] == 2


def test_record_failure_escalates_to_degraded(tmp_path):
    """After DEGRADED_FAILURE_THRESHOLD failures the status becomes 'degraded'."""
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        for i in range(DEGRADED_FAILURE_THRESHOLD):
            _record_failure("not_registered", f"fail {i}")

    with open(health_file) as f:
        data = json.load(f)
    assert data["status"] == "degraded"
    assert data["consecutive_failures"] == DEGRADED_FAILURE_THRESHOLD
    assert data["error"] == "not_registered"


def test_record_failure_preserves_last_success_at(tmp_path):
    """The timestamp of the last healthy poll survives across failures."""
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        _record_success()
        with open(health_file) as f:
            last_ok = json.load(f)["last_success_at"]

        _record_failure("timeout", "boom")

    with open(health_file) as f:
        data = json.load(f)
    assert data["last_success_at"] == last_ok
    assert data["status"] == "error"


def test_record_success_after_failure_clears_error(tmp_path):
    """A successful poll after failures wipes error state and streak."""
    health_file = str(tmp_path / "signal-health.json")
    with patch("signal_listener.SIGNAL_HEALTH_FILE", health_file):
        _record_failure("timeout", "failed")
        _record_failure("timeout", "failed")
        _record_failure("timeout", "failed")  # now degraded
        _record_success()

    with open(health_file) as f:
        data = json.load(f)
    assert data["status"] == "ok"
    assert data["consecutive_failures"] == 0
    assert "error" not in data
