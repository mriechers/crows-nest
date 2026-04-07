import os
from pipeline.config import OBSIDIAN_CLIPPINGS


def test_clippings_path_points_to_areas():
    """Clippings should write to AREAS, not INBOX."""
    assert "2 - AREAS" in OBSIDIAN_CLIPPINGS
    assert "INTERNET CLIPPINGS" in OBSIDIAN_CLIPPINGS
    assert "0 - INBOX" not in OBSIDIAN_CLIPPINGS


class TestTransportConfig:
    def test_default_transport_is_stdio(self):
        """Default transport should be stdio for backward compat."""
        from mcp_knowledge import config
        assert config.MCP_TRANSPORT == "stdio"

    def test_default_sse_port(self):
        from mcp_knowledge import config
        assert config.MCP_SSE_PORT == 27185

    def test_default_sse_host(self):
        from mcp_knowledge import config
        assert config.MCP_SSE_HOST == "127.0.0.1"
