"""ChromaDB-backed semantic search index for media archive transcripts."""
from datetime import datetime, timezone

import chromadb

from .embeddings import EmbeddingProvider


class SemanticIndex:
    """Semantic search index backed by ChromaDB with cosine similarity."""

    COLLECTION_NAME = "crows_nest_media"

    def __init__(self, data_path: str, embedding_provider: EmbeddingProvider) -> None:
        self._provider = embedding_provider
        self._client = chromadb.PersistentClient(path=data_path)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_documents(self, docs: list[dict]) -> int:
        """Embed and upsert documents into ChromaDB.

        Each doc must have: title, text, path, metadata.
        metadata may contain: platform, creator, content_type, url.
        Returns number of documents indexed.
        """
        if not docs:
            return 0

        texts: list[str] = []
        ids: list[str] = []
        metadatas: list[dict] = []

        indexed_at = datetime.now(timezone.utc).isoformat()

        for doc in docs:
            meta = doc.get("metadata", {})
            creator = meta.get("creator", "")
            platform = meta.get("platform", "")

            embedding_text = (
                f"Title: {doc['title']}\n"
                f"Creator: {creator}\n"
                f"Platform: {platform}\n"
                f"{doc['text']}"
            )
            texts.append(embedding_text)
            ids.append(doc["path"])
            metadatas.append(
                {
                    "title": doc["title"],
                    "path": doc["path"],
                    "source": "crows-nest",
                    "indexed_at": indexed_at,
                    "platform": platform,
                    "creator": creator,
                    "content_type": meta.get("content_type", ""),
                    "url": meta.get("url", ""),
                }
            )

        vectors = self._provider.embed(texts)

        self._collection.upsert(
            ids=ids,
            embeddings=vectors,
            documents=[doc["text"] for doc in docs],
            metadatas=metadatas,
        )
        return len(docs)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        n_results: int = 10,
        platform: str | None = None,
    ) -> list[dict]:
        """Search for documents semantically similar to query.

        Returns list of dicts with: title, snippet, score, similarity,
        source, search_type, path, metadata.
        """
        if self._collection.count() == 0:
            return []

        where = {"platform": platform} if platform else None

        query_vector = self._provider.embed([query])[0]

        # Clamp n_results to available document count (respecting filter)
        available = self._collection.count()
        effective_n = min(n_results, available)

        kwargs: dict = {
            "query_embeddings": [query_vector],
            "n_results": effective_n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        output: list[dict] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas_list = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for doc_id, text, meta, distance in zip(ids, documents, metadatas_list, distances):
            score = 1.0 - distance
            snippet = (text or "")[:300]
            output.append(
                {
                    "title": meta.get("title", ""),
                    "snippet": snippet,
                    "score": score,
                    "similarity": score,
                    "source": "crows-nest",
                    "search_type": "semantic",
                    "path": meta.get("path", doc_id),
                    "metadata": {k: v for k, v in meta.items()},
                }
            )
        return output

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def document_count(self) -> int:
        """Return number of documents currently in the collection."""
        return self._collection.count()

    def get_status(self) -> dict:
        """Return collection name and document count."""
        return {
            "collection_name": self.COLLECTION_NAME,
            "document_count": self.document_count(),
        }

    def clear(self) -> None:
        """Delete and recreate the collection, wiping all documents."""
        self._client.delete_collection(self.COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
