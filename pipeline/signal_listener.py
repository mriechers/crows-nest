"""
Signal listener for the Crow's Nest pipeline.

Polls signal-cli for incoming messages, extracts URLs, classifies content
type, saves to the database, and sends a confirmation reply.
"""

import json
import os
import secrets
import sqlite3
import subprocess
from datetime import datetime, timezone

from config import MESSAGE_LOG, SIGNAL_HEALTH_FILE
from content_types import classify_url
from db import init_db, add_link, get_connection
from keychain_secrets import get_secret
from utils import extract_urls, setup_logging

logger = setup_logging("signal_listener")

SIGNAL_CLI = "signal-cli"
SIGNAL_USER = get_secret("SIGNAL_USER") or ""
RECEIVE_TIMEOUT = 15
SIGNAL_ATTACHMENTS_DIR = os.path.expanduser("~/.local/share/signal-cli/attachments")


def resolve_sender(sender_id: str, sender_name: str | None) -> str:
    """Prefer Signal display name over raw phone number.

    Falls back to sender_id (phone number) when sender_name is empty,
    whitespace-only, or is just the phone number echoed back.
    """
    name = (sender_name or "").strip()
    if not name or name == sender_id:
        return sender_id
    return name


def parse_signal_message(message: str | None) -> dict:
    """Extract URLs and context from a Signal message body.

    Args:
        message: Raw message text, or None.

    Returns:
        dict with keys:
            "urls"    — list of extracted URL strings (may be empty)
            "context" — the original message text (empty string if None)
    """
    if not message:
        return {"urls": [], "context": ""}
    urls = extract_urls(message)
    return {"urls": urls, "context": message}


def _parse_json_output(output: str) -> list[dict]:
    """Parse the JSON output produced by `signal-cli --json receive`.

    Each line of output is a separate JSON envelope. Envelopes without a
    dataMessage (receipts, typing indicators) are skipped. Messages with
    no body AND no attachments are also skipped.

    Args:
        output: Raw stdout string from signal-cli in JSON mode.

    Returns:
        List of dicts, each with keys:
            "sender_id"   — phone number (sourceNumber)
            "sender_name" — display name (sourceName)
            "message"     — message body text (empty string if absent)
            "timestamp"   — envelope timestamp in milliseconds
            "group_name"  — group name, or empty string
            "attachments" — list of dicts with content_type, id, path, width, height
    """
    messages = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            envelope_wrapper = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Could not parse JSON line: %s", line[:100])
            continue

        envelope = envelope_wrapper.get("envelope", {})
        data_message = envelope.get("dataMessage")
        if not data_message:
            # Receipt, typing indicator, sync message, etc. — skip.
            continue

        message_body = data_message.get("message") or ""

        # Parse attachments
        raw_attachments = data_message.get("attachments") or []
        attachments = []
        for att in raw_attachments:
            att_id = att.get("id", "")
            attachments.append({
                "content_type": att.get("contentType", ""),
                "id": att_id,
                "path": os.path.join(SIGNAL_ATTACHMENTS_DIR, att_id),
                "width": att.get("width", 0),
                "height": att.get("height", 0),
            })

        # Skip messages with no body AND no attachments
        if not message_body and not attachments:
            continue

        group_info = data_message.get("groupInfo") or {}
        group_name = group_info.get("groupName", "") or ""

        messages.append({
            "sender_id": envelope.get("sourceNumber", ""),
            "sender_name": envelope.get("sourceName", ""),
            "message": message_body,
            "timestamp": envelope.get("timestamp", 0),
            "group_name": group_name,
            "attachments": attachments,
        })

    return messages


def _batch_image_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate messages into image batches and non-image messages.

    Image batching rules:
    - A message is an image message if it has at least one attachment whose
      content_type starts with "image/".
    - Images are batched when they share the same sender_id AND the timestamp
      is within 180,000 ms of the previous image message in the batch.
    - Non-image messages (no image attachments) go to the non_image list.

    Args:
        messages: List of parsed message dicts (from _parse_json_output).

    Returns:
        Tuple of (image_batches, non_image_messages):
            image_batches    — list of batch dicts, each with:
                "sender_id", "sender_name", "timestamp" (first msg),
                "context" (all message texts joined), "attachments" (all images),
                "group_name"
            non_image_messages — list of original message dicts with no image attachments
    """
    def _is_image_message(msg: dict) -> bool:
        return any(
            att["content_type"].startswith("image/")
            for att in msg.get("attachments", [])
        )

    def _image_attachments(msg: dict) -> list[dict]:
        return [
            att for att in msg.get("attachments", [])
            if att["content_type"].startswith("image/")
        ]

    image_batches: list[dict] = []
    non_image_messages: list[dict] = []

    for msg in messages:
        if not _is_image_message(msg):
            non_image_messages.append(msg)
            continue

        # Try to add to the most recent open batch
        if image_batches:
            last_batch = image_batches[-1]
            time_gap = msg["timestamp"] - last_batch["_last_timestamp"]
            same_sender = msg["sender_id"] == last_batch["sender_id"]
            within_window = time_gap <= 180_000

            if same_sender and within_window:
                # Extend the existing batch
                last_batch["attachments"].extend(_image_attachments(msg))
                last_batch["_last_timestamp"] = msg["timestamp"]
                if msg["message"]:
                    if last_batch["context"]:
                        last_batch["context"] += " " + msg["message"]
                    else:
                        last_batch["context"] = msg["message"]
                continue

        # Start a new batch
        image_batches.append({
            "sender_id": msg["sender_id"],
            "sender_name": msg["sender_name"],
            "timestamp": msg["timestamp"],
            "_last_timestamp": msg["timestamp"],
            "context": msg["message"] or "",
            "attachments": list(_image_attachments(msg)),
            "group_name": msg.get("group_name", ""),
        })

    # Strip internal tracking key before returning
    for batch in image_batches:
        batch.pop("_last_timestamp", None)

    return image_batches, non_image_messages


def receive_messages() -> list[dict] | None:
    """Invoke signal-cli in JSON mode and return parsed messages.

    Calls `signal-cli -u {SIGNAL_USER} receive --timeout {RECEIVE_TIMEOUT} --json`.
    The command returns all buffered messages in a single invocation.

    Returns:
        List of dicts with keys "sender_id", "sender_name", "message",
        "timestamp", "group_name", "attachments".
        Returns an empty list on any subprocess error.
        Returns None on fatal errors (e.g. account not registered).
    """
    cmd = [
        SIGNAL_CLI,
        "-u", SIGNAL_USER,
        "-o", "json",
        "receive",
        "--timeout", str(RECEIVE_TIMEOUT),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RECEIVE_TIMEOUT + 5,
        )
        stderr = result.stderr.strip() if result.stderr else ""
        if result.returncode != 0 and stderr:
            if "not registered" in stderr.lower():
                logger.critical(
                    "signal-cli: account is NOT REGISTERED. "
                    "Messages cannot be sent or received. "
                    "Re-register with: signal-cli -u %s register && signal-cli -u %s verify CODE",
                    SIGNAL_USER, SIGNAL_USER,
                )
                return None
            logger.error("signal-cli exited %d: %s", result.returncode, stderr)
            return None
        return _parse_json_output(result.stdout)
    except subprocess.TimeoutExpired as exc:
        # signal-cli writes messages to stdout as they arrive, so even
        # on a timeout we may have captured messages.  These have already
        # been consumed from the server — we MUST parse them or they're
        # lost forever.
        partial = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        if partial.strip():
            logger.warning("signal-cli timed out but captured partial output (%d bytes) — parsing", len(partial))
            return _parse_json_output(partial)
        logger.error("signal-cli timed out with no output")
        return []
    except Exception as exc:
        logger.error("Failed to receive Signal messages: %s", exc)
        return []


def send_confirmation(recipient: str, message: str) -> None:
    """Send a Signal direct message to recipient.

    Note: Always sends a DM directly to the phone number, even when the
    original message arrived in a group chat.

    Errors are caught and logged silently — confirmation is best-effort.
    """
    cmd = [
        SIGNAL_CLI,
        "-u", SIGNAL_USER,
        "send",
        recipient,
        "-m", message,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        logger.warning("Could not send confirmation to %s: %s", recipient, exc)


def _log_message(sender: str, body: str, group: str = "") -> None:
    """Append every incoming message to a plain text log file."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    group_label = f" [{group}]" if group else ""
    line = f"{ts}  {sender}{group_label}: {body}\n"
    try:
        with open(MESSAGE_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        logger.warning("Could not write to message log: %s", exc)


def _write_health(status: str, error: str = "", message: str = "") -> None:
    """Write a machine-readable health status file for the signal listener."""
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"status": status, "timestamp": ts}
    if error:
        payload["error"] = error
    if message:
        payload["message"] = message
    try:
        os.makedirs(os.path.dirname(SIGNAL_HEALTH_FILE), exist_ok=True)
        with open(SIGNAL_HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError as exc:
        logger.warning("Could not write health file: %s", exc)


def run(db_path: str) -> None:
    """Main entry point: receive messages, extract URLs, store, confirm.

    Requires SIGNAL_USER to be set in macOS Keychain
    (developer.workspace.SIGNAL_USER) or as an env var.

    Receives messages, batches image attachments, then:
    - Non-image messages: log, store in signal_messages, extract URLs, add_link
    - Image batches: generate synthetic URL, add_link with image metadata

    Args:
        db_path: Path to the SQLite database file.
    """
    if not SIGNAL_USER:
        logger.error("SIGNAL_USER not set (check Keychain or env var) — cannot receive messages")
        _write_health("error", "no_signal_user", "SIGNAL_USER not configured")
        return

    init_db(db_path)
    messages = receive_messages()

    if messages is None:
        _write_health("error", "not_registered", f"signal-cli account {SIGNAL_USER} is not registered")
        return

    _write_health("ok")

    image_batches, non_image_messages = _batch_image_messages(messages)

    # Process non-image messages (existing behaviour)
    for msg in non_image_messages:
        sender_id = msg["sender_id"]
        sender_name = msg.get("sender_name", "")
        sender_display = resolve_sender(sender_id, sender_name)
        body = msg["message"]
        group = msg.get("group_name", "")

        # Log every message for reference
        _log_message(sender_display, body, group)

        # Extract URLs for the pipeline
        parsed = parse_signal_message(body)
        has_urls = len(parsed["urls"]) > 0

        # Store in signal_messages table
        try:
            conn = get_connection(db_path)
            conn.execute(
                "INSERT INTO signal_messages (sender, body, group_name, has_urls) VALUES (?, ?, ?, ?)",
                (sender_display, body, group or None, has_urls),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Failed to log message to DB: %s", exc)

        for url in parsed["urls"]:
            content_type = classify_url(url)
            try:
                add_link(
                    url=url,
                    source_type="signal",
                    sender=sender_display,
                    context=parsed["context"],
                    content_type=content_type,
                    db_path=db_path,
                )
                logger.info("Saved %s (%s) from %s", url, content_type, sender_display)
                send_confirmation(sender_id, f"Saved: {url}")
            except sqlite3.IntegrityError:
                logger.info("Duplicate URL skipped: %s", url)

    # Process image batches
    for batch in image_batches:
        sender_id = batch["sender_id"]
        sender_name = batch.get("sender_name", "")
        sender_display = resolve_sender(sender_id, sender_name)
        group = batch.get("group_name", "")
        context = batch.get("context", "")
        timestamp = batch["timestamp"]
        attachments = batch["attachments"]

        # Log the batch as a message entry
        _log_message(sender_display, f"[image batch: {len(attachments)} image(s)]", group)

        # Store in signal_messages table
        try:
            conn = get_connection(db_path)
            conn.execute(
                "INSERT INTO signal_messages (sender, body, group_name, has_urls) VALUES (?, ?, ?, ?)",
                (sender_display, context or f"[image batch: {len(attachments)} image(s)]", group or None, 0),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Failed to log image batch message to DB: %s", exc)

        # Generate a synthetic URL with timestamp + 8 random hex chars
        random_suffix = secrets.token_hex(4)
        synthetic_url = f"signal-image://{timestamp}-{random_suffix}"

        metadata = {
            "attachment_paths": [att["path"] for att in attachments],
            "image_count": len(attachments),
            "dimensions": [{"width": att["width"], "height": att["height"]} for att in attachments],
            "batch_timestamps": [timestamp],
        }

        try:
            add_link(
                url=synthetic_url,
                source_type="signal",
                sender=sender_display,
                context=context,
                content_type="image",
                metadata=json.dumps(metadata),
                db_path=db_path,
            )
            logger.info(
                "Saved image batch (%d image(s)) from %s as %s",
                len(attachments), sender_display, synthetic_url,
            )
            send_confirmation(sender_id, f"Saved {len(attachments)} image(s)")
        except sqlite3.IntegrityError:
            logger.info("Duplicate image URL skipped: %s", synthetic_url)


if __name__ == "__main__":
    from db import DB_PATH
    run(DB_PATH)
