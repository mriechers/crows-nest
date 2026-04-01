import os
from pipeline.config import OBSIDIAN_CLIPPINGS


def test_clippings_path_points_to_areas():
    """Clippings should write to AREAS, not INBOX."""
    assert "2 - AREAS" in OBSIDIAN_CLIPPINGS
    assert "CLIPPINGS - Need Sorting" in OBSIDIAN_CLIPPINGS
    assert "0 - INBOX" not in OBSIDIAN_CLIPPINGS
