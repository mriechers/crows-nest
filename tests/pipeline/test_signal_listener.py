"""Tests for signal_listener.py — JSON-mode parsing, image detection, batching."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))

from signal_listener import (
    parse_signal_message,
    _parse_json_output,
    _batch_image_messages,
)


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
