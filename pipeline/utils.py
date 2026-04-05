import os
import re
import logging
from datetime import datetime

from config import MEDIA_ROOT

URL_PATTERN = re.compile(r"https?://[^\s<>\"',;!)]+")


def _strip_trailing_punct(url: str) -> str:
    """Strip trailing sentence punctuation from a URL.

    Strips .,!?) but only when they're genuinely trailing — a ? followed
    by more URL characters (query params) is kept.
    """
    # Strip simple trailing punctuation
    url = url.rstrip(".,!)")
    # Strip trailing ? only if it's the very end (no query params after it)
    if url.endswith("?"):
        url = url[:-1]
    return url


def sanitize_title(title: str, max_length: int = 100) -> str:
    """Remove chars unsafe for filenames, collapse whitespace, truncate."""
    unsafe = r'[<>:"/\\|?*\[\]#]'
    result = re.sub(unsafe, "", title)
    result = re.sub(r"\s+", " ", result)
    result = result[:max_length]
    return result.strip()


def extract_urls(text: str) -> list[str]:
    """Find all http/https URLs in text."""
    return [_strip_trailing_punct(u) for u in URL_PATTERN.findall(text)]


def media_dir_for(title: str) -> str:
    """Return ~/Media/crows-nest/YYYY-MM/sanitized-title/, creating it if needed."""
    month = datetime.now().strftime("%Y-%m")
    safe_title = sanitize_title(title)
    path = os.path.join(MEDIA_ROOT, month, safe_title)
    os.makedirs(path, exist_ok=True)
    return path


def setup_logging(name: str) -> logging.Logger:
    """Configure and return a named logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
