"""Tests for vault-relative path helpers."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))


def test_to_vault_relative_strips_prefix(monkeypatch):
    """Absolute path under vault root becomes vault-relative."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_vault_relative

    result = to_vault_relative("/home/user/vault/2 - AREAS/INTERNET CLIPPINGS/note.md")
    assert result == os.path.join("2 - AREAS", "INTERNET CLIPPINGS", "note.md")


def test_to_vault_relative_with_trailing_slash(monkeypatch):
    """Works whether or not OBSIDIAN_VAULT has a trailing slash."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault/")
    from config import to_vault_relative

    result = to_vault_relative("/home/user/vault/2 - AREAS/note.md")
    assert result == os.path.join("2 - AREAS", "note.md")


def test_to_vault_relative_already_relative(monkeypatch):
    """If the path is already relative (no vault prefix), return it unchanged."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_vault_relative

    result = to_vault_relative("2 - AREAS/INTERNET CLIPPINGS/note.md")
    assert result == "2 - AREAS/INTERNET CLIPPINGS/note.md"


def test_to_abs_note_path_prepends_vault(monkeypatch):
    """Vault-relative path becomes absolute by prepending OBSIDIAN_VAULT."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_abs_note_path

    result = to_abs_note_path("2 - AREAS/INTERNET CLIPPINGS/note.md")
    assert result == "/home/user/vault/2 - AREAS/INTERNET CLIPPINGS/note.md"


def test_to_abs_note_path_empty_returns_empty(monkeypatch):
    """Empty or None input returns empty string (used for missing paths)."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_abs_note_path

    assert to_abs_note_path("") == ""
    assert to_abs_note_path(None) == ""


def test_roundtrip(monkeypatch):
    """to_abs(to_relative(abs_path)) returns the original absolute path."""
    import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT", "/home/user/vault")
    from config import to_abs_note_path, to_vault_relative

    original = "/home/user/vault/2 - AREAS/INTERNET CLIPPINGS/2026/04/12/note.md"
    assert to_abs_note_path(to_vault_relative(original)) == original
