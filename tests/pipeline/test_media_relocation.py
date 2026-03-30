import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../pipeline"))


def test_media_root_defaults_to_crows_nest_home_media(monkeypatch):
    """MEDIA_ROOT default must be {CROWS_NEST_HOME}/media, not ~/Media/crows-nest."""
    # Remove overrides so we exercise the defaults
    monkeypatch.delenv("MEDIA_ROOT", raising=False)
    monkeypatch.delenv("CROWS_NEST_HOME", raising=False)

    # Re-import config with clean env
    import importlib
    import pipeline.config as config_mod
    importlib.reload(config_mod)

    expected_home = os.path.expanduser("~/Developer/second-brain/crows-nest")
    expected_media = os.path.join(expected_home, "media")

    assert config_mod.MEDIA_ROOT == expected_media, (
        f"Expected MEDIA_ROOT={expected_media!r}, got {config_mod.MEDIA_ROOT!r}"
    )


def test_media_root_env_override(monkeypatch):
    """MEDIA_ROOT env var must override the default."""
    monkeypatch.setenv("MEDIA_ROOT", "/custom/media/path")

    import importlib
    import pipeline.config as config_mod
    importlib.reload(config_mod)

    assert config_mod.MEDIA_ROOT == "/custom/media/path"


def test_media_root_tracks_crows_nest_home(monkeypatch):
    """When CROWS_NEST_HOME is overridden, MEDIA_ROOT default must follow it."""
    monkeypatch.setenv("CROWS_NEST_HOME", "/opt/crows-nest")
    monkeypatch.delenv("MEDIA_ROOT", raising=False)

    import importlib
    import pipeline.config as config_mod
    importlib.reload(config_mod)

    assert config_mod.MEDIA_ROOT == "/opt/crows-nest/media"
