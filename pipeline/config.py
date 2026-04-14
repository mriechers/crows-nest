"""
Centralized path configuration for the Crow's Nest pipeline.

All paths are derived from environment variables with sensible defaults
for the macOS development environment. On Proxmox/Linux, set these env
vars to override:

    CROWS_NEST_HOME          — repo root (default: ~/Developer/second-brain/crows-nest)
    OBSIDIAN_VAULT           — vault root (default: ~/Developer/obsidian/MarkBrain)
    MEDIA_ROOT               — media storage (default: {CROWS_NEST_HOME}/media)
    OBSIDIAN_CLIPPINGS_SUBDIR — subfolder under vault for clippings (default: 2 - AREAS/INTERNET CLIPPINGS)

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
LAUNCHD_LOG_DIR = os.path.expanduser("~/.local/share/crows-nest/logs")
SCRIPTS_DIR = os.path.join(CROWS_NEST_HOME, "scripts")
PIPELINE_DIR = os.path.join(CROWS_NEST_HOME, "pipeline")

DB_PATH = os.path.join(DATA_DIR, "crows-nest.db")
WHISPER_SCRIPT = os.path.join(SCRIPTS_DIR, "whisper-transcribe.sh")

OBSIDIAN_CLIPPINGS = os.path.join(
    OBSIDIAN_VAULT,
    os.environ.get("OBSIDIAN_CLIPPINGS_SUBDIR", os.path.join("2 - AREAS", "INTERNET CLIPPINGS")),
)
OBSIDIAN_ARCHIVE = os.path.join(OBSIDIAN_VAULT, "4 - ARCHIVE")


# ---------------------------------------------------------------------------
# Vault-relative path helpers
# ---------------------------------------------------------------------------

def to_vault_relative(abs_path: str) -> str:
    """Strip OBSIDIAN_VAULT prefix to get a vault-relative path for DB storage.

    If the path doesn't start with OBSIDIAN_VAULT (already relative, or from
    a different mount point), returns it unchanged.
    """
    vault = OBSIDIAN_VAULT.rstrip(os.sep) + os.sep
    if abs_path.startswith(vault):
        return abs_path[len(vault):]
    return abs_path


def to_abs_note_path(vault_relative: str) -> str:
    """Reconstruct absolute path from a vault-relative DB value.

    Returns empty string for empty/None input (common for links without notes).
    """
    if not vault_relative:
        return ""
    return os.path.join(OBSIDIAN_VAULT, vault_relative)


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
