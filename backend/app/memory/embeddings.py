"""Embedders. Default is a dependency-free hashing embedder so the project runs
offline on any laptop with zero model downloads. Switch to fastembed (set
EMBEDDINGS=fastembed + `pip install fastembed`) when you want real semantic quality.
"""
from __future__ import annotations

import hashlib
import re

import numpy as np

from app.core.config import get_settings

_TOKEN = re.compile(r"[\wऀ-෿]+", re.UNICODE)  # keeps Indic scripts


class HashEmbedder:
    def __init__(self, dim: int = 256):
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]  # words and word pairs
        for feature in features:
            h = int(hashlib.md5(feature.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if (h >> 20) & 1 else -1.0
        norm = np.linalg.norm(vec)
        return (vec / norm if norm else vec).tolist()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class FastEmbedder:
    def __init__(self):
        from fastembed import TextEmbedding  # lazy import

        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")
        self.dim = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.embed(texts)]


def make_embedder():
    s = get_settings()
    if s.embeddings == "fastembed":
        return FastEmbedder()
    return HashEmbedder(s.embed_dim)
