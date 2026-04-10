"""
Centralized path configuration for the Crow's Nest pipeline.

All paths are derived from environment variables with sensible defaults
for the macOS development environment. On Proxmox/Linux, set these env
vars to override:

    CROWS_NEST_HOME     — repo root (default: ~/Developer/second-brain/crows-nest)
    OBSIDIAN_VAULT      — vault root (default: ~/Developer/obsidian/MarkBrain)
    MEDIA_ROOT          — media storage (default: {CROWS_NEST_HOME}/media)

Example systemd override:
    Environment=CROWS_NEST_HOME=/opt/crows-nest
    Environment=OBSIDIAN_VAULT=/data/obsidian/MarkBrain
"""

import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Base directories
# ---------------------------------------------------------------------------

CROWS_NEST_HOME = os.environ.get(
    "CROWS_NEST_HOME",
    os.path.expanduser("~/Developer/second-brain/crows-nest"),
)

OBSIDIAN_VAULT = os.environ.get(
    "OBSIDIAN_VAULT",
    os.path.expanduser("~/Developer/second-brain/obsidian/MarkBrain"),
)

MEDIA_ROOT = os.environ.get(
    "MEDIA_ROOT",
    os.path.join(CROWS_NEST_HOME, "media"),
)

# ---------------------------------------------------------------------------
# Derived paths
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(CROWS_NEST_HOME, "data")
LOG_DIR = os.path.join(CROWS_NEST_HOME, "logs")
SCRIPTS_DIR = os.path.join(CROWS_NEST_HOME, "scripts")
PIPELINE_DIR = os.path.join(CROWS_NEST_HOME, "pipeline")

DB_PATH = os.path.join(DATA_DIR, "crows-nest.db")
MESSAGE_LOG = os.path.join(LOG_DIR, "signal-messages.log")
SIGNAL_HEALTH_FILE = os.path.join(LOG_DIR, "signal-health.json")
WHISPER_SCRIPT = os.path.join(SCRIPTS_DIR, "whisper-transcribe.sh")

OBSIDIAN_CLIPPINGS = os.path.join(OBSIDIAN_VAULT, "2 - AREAS", "INTERNET CLIPPINGS")
OBSIDIAN_ARCHIVE = os.path.join(OBSIDIAN_VAULT, "4 - ARCHIVE")


# ---------------------------------------------------------------------------
# Ingest API (Cloudflare Worker + D1 queue)
# ---------------------------------------------------------------------------

INGEST_API_URL = os.environ.get(
    "CROWS_NEST_INGEST_API_URL",
    "https://share.bymarkriechers.com/api",
)


# ---------------------------------------------------------------------------
# Image processing — platform-adaptive
# ---------------------------------------------------------------------------

def has_command(name: str) -> bool:
    """Check if a command is available on PATH."""
    return shutil.which(name) is not None


IS_MACOS = sys.platform == "darwin"
HAS_SIPS = IS_MACOS and has_command("sips")
HAS_MAGICK = has_command("magick") or has_command("convert")


def convert_heic_to_jpeg(src: str, dst: str) -> None:
    """Convert HEIC to JPEG using the best available tool."""
    if HAS_SIPS:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", src, "--out", dst],
            capture_output=True, text=True, timeout=30,
        )
    elif HAS_MAGICK:
        cmd = "magick" if has_command("magick") else "convert"
        subprocess.run(
            [cmd, src, dst],
            capture_output=True, text=True, timeout=30,
        )
    else:
        raise RuntimeError(
            "No image converter available. Install ImageMagick: "
            "apt install imagemagick (Linux) or brew install imagemagick (macOS)"
        )


def resize_image(path: str, max_dim: int = 1568) -> None:
    """Resize image so longest edge is max_dim pixels. Modifies in place."""
    if HAS_SIPS:
        subprocess.run(
            ["sips", "--resampleHeightWidthMax", str(max_dim), path],
            capture_output=True, text=True, timeout=30,
        )
    elif HAS_MAGICK:
        cmd = "magick" if has_command("magick") else "convert"
        subprocess.run(
            [cmd, path, "-resize", f"{max_dim}x{max_dim}>", path],
            capture_output=True, text=True, timeout=30,
        )
    # If neither is available, skip resizing silently — full-res is fine
