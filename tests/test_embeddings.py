from mcp_knowledge.embeddings import EmbeddingProvider


def test_embed_returns_vectors():
    """EmbeddingProvider.embed should return float vectors."""
    provider = EmbeddingProvider()
    vectors = provider.embed(["hello world", "test query"])
    assert len(vectors) == 2
    assert len(vectors[0]) > 0
    assert all(isinstance(v, float) for v in vectors[0])


def test_embed_lazy_loads_model():
    """Model should not load until first embed call."""
    provider = EmbeddingProvider()
    assert provider._model is None
    provider.embed(["trigger load"])
    assert provider._model is not None
