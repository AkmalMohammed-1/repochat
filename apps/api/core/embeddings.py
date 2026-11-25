"""Embedding providers. All return L2-normalised vectors of settings.embedding_dim (384) dimensions.

- local:  BAAI/bge-small-en-v1.5 via sentence-transformers (free, runs on CPU)
- openai: text-embedding-3-small, truncated to 384 dims via the API's `dimensions` parameter
- hash:   deterministic bag-of-words hashing; no model download. For tests/CI only - retrieval quality is poor.
"""

import asyncio
import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from api.core.config import get_settings

# bge models are trained with this instruction prefix on the *query* side only.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    def __init__(self, model_name: str, dim: int):
        from sentence_transformers import SentenceTransformer  # heavy import, keep lazy

        self.model = SentenceTransformer(model_name, device="cpu")
        self.dim = dim
        self.query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, texts)

    async def embed_query(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._encode, [self.query_prefix + text]))[0]


class OpenAIEmbedder:
    def __init__(self, api_key: str, dim: int, model: str = "text-embedding-3-small"):
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.dim = dim

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):
            resp = await self.client.embeddings.create(model=self.model, input=texts[i:i + 256],
                                                       dimensions=self.dim)
            out.extend(d.embedding for d in resp.data)
        return out

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class HashEmbedder:
    def __init__(self, dim: int):
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN.findall(text.lower()):
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "little")
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._one(text)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "openai":
        return OpenAIEmbedder(s.openai_api_key, s.embedding_dim)
    if s.embedding_provider == "hash":
        return HashEmbedder(s.embedding_dim)
    return LocalEmbedder(s.embedding_model, s.embedding_dim)
