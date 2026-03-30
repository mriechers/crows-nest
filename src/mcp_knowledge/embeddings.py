"""Lazy fastembed wrapper for generating text embeddings."""
from typing import Any


class EmbeddingProvider:
    """Thin wrapper around fastembed with lazy model loading."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model: Any = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name=self._model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a list of float vectors, one per input text."""
        self._ensure_model()
        return [vec.tolist() for vec in self._model.embed(texts)]
