"""Vector memory on Qdrant. Two collections:

  knowledge  -> static domain facts (RAG)        : seeded from data/knowledge/*.md
  memory     -> per-session long-term memory     : written during chat, recalled by session_id

QDRANT_URL empty => in-process Qdrant (no Docker, no cloud, still the real client).
"""
from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from qdrant_client import QdrantClient, models

from app.core.config import get_settings
from app.memory.embeddings import make_embedder

KNOWLEDGE = "knowledge"
MEMORY = "memory"

LEXICAL_MIN_SHARE = 0.3  # a chunk with this share of the question's words is relevant even if its embedding is not
KNOWLEDGE_RERANK_POOL = 400  # the whole document set is a few hundred chunks at most: rerank all of it, not just the nearest few
LEXICAL_WEIGHT = 0.9  # how much the share of matching question words counts next to the embedding score
DOCUMENT_SUFFIXES = {".md", ".txt"}
FRONT_MATTER_KEYS = ("title", "url", "posted", "release_id", "fetched")
_STOP = {
    "the", "and", "for", "what", "how", "much", "does", "are", "was", "who", "when", "where", "which", "with", "this",
    "that", "from", "have", "can", "you", "your", "about", "pay", "get",
}


@dataclass
class Hit:
    text: str
    score: float
    source: str
    payload: dict


class VectorStore:
    def __init__(self):
        s = get_settings()
        self.s = s
        self.embedder = make_embedder()
        self.client = (
            QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None) if s.qdrant_url else QdrantClient(":memory:")
        )
        for name in (KNOWLEDGE, MEMORY):
            if not self.client.collection_exists(name):
                self.client.create_collection(
                    name, vectors_config=models.VectorParams(size=self.embedder.dim, distance=models.Distance.COSINE)
                )

    # ---------------------------------------------------------------- write
    def add(self, collection: str, texts: list[str], payloads: list[dict] | None = None) -> int:
        if not texts:
            return 0
        payloads = payloads or [{} for _ in texts]
        vecs = self.embedder.embed(texts)
        points = [
            models.PointStruct(id=str(uuid.uuid4()), vector=v, payload={**p, "text": t, "ts": time.time()})
            for t, v, p in zip(texts, vecs, payloads)
        ]
        self.client.upsert(collection, points=points)
        return len(points)

    def remember(self, session_id: str, text: str, kind: str = "turn") -> None:
        self.add(MEMORY, [text], [{"session_id": session_id, "kind": kind, "source": "memory"}])

    # ----------------------------------------------------------------- read
    def search(self, collection: str, query: str, k: int | None = None, session_id: str | None = None) -> list[Hit]:
        want = k or self.s.rag_top_k
        if collection == KNOWLEDGE:
            # The embedding is a hash of character n-grams: good at "same words", weak at "the right paragraph". Fetch a wider set and
            # rerank by how many of the question's words (crop, scheme, season, number) the chunk really contains.
            wide = self._vector_search(collection, query, max(want * 4, KNOWLEDGE_RERANK_POOL), session_id, floor=-1.0)  # no vector floor: a table of numbers scores ~0
            share = _idf_share(query, wide)
            ranked = sorted(wide, key=lambda h: -(h.score + LEXICAL_WEIGHT * share(h)))
            # relevant if the embedding likes it OR the chunk contains a fair (rarity-weighted) share of the question's words
            return [h for h in ranked if h.score >= self.s.rag_min_score or share(h) >= LEXICAL_MIN_SHARE][:want]
        return self._vector_search(collection, query, want, session_id)

    def _vector_search(self, collection: str, query: str, limit: int, session_id: str | None, floor: float | None = None) -> list[Hit]:
        flt = None
        if session_id:
            flt = models.Filter(must=[models.FieldCondition(key="session_id", match=models.MatchValue(value=session_id))])
        res = self.client.query_points(
            collection,
            query=self.embedder.embed([query])[0],
            limit=limit,
            query_filter=flt,
            with_payload=True,
        )
        min_score = self.s.rag_min_score if floor is None else floor
        return [
            Hit(p.payload.get("text", ""), float(p.score), p.payload.get("source", collection), p.payload)
            for p in res.points
            if p.score >= min_score
        ]

    def count(self, collection: str) -> int:
        return self.client.count(collection).count

    # ------------------------------------------------------------- seeding
    def seed_dir(self, path: Path) -> int:
        """Chunk every .md/.txt in `path` (subfolders included) by blank-line paragraphs (~500 chars)."""
        added = 0
        for f in sorted(path.glob("**/*")):
            if f.suffix.lower() not in DOCUMENT_SUFFIXES:
                continue
            meta, body = split_front_matter(f.read_text(encoding="utf-8"))
            chunks = chunk(body)
            # every chunk carries its document's title and date, so the answer can name the document it used
            date = " ".join(meta.get("posted", "").split()[:3])
            head = f"[{meta['title']} - {date}] " if meta.get("title") else ""
            keep = {k: v for k, v in meta.items() if k in FRONT_MATTER_KEYS}
            added += self.add(KNOWLEDGE, [head + c for c in chunks], [{"source": f.name, **keep} for _ in chunks])
        return added


def _terms(text: str) -> set[str]:
    """Words worth matching: Latin words of 3+ letters (not filler), numbers, Devanagari words of 2+ letters."""
    words = (m.lower() for m in re.findall(r"[A-Za-z]{3,}|\d[\d,.]*|[\u0900-\u097f]{2,}", text or ""))
    return {(w[:-1] if len(w) > 4 and w.endswith("s") and w.isascii() else w) for w in words if w not in _STOP}  # crop/crops, instalment/instalments


def _idf_share(query: str, pool: list) -> Callable[[Hit], float]:
    """h -> the share of the question's words found in chunk h, each word weighted by how rare it is across the pool (idf), so
    "wheat" counts for more than "minimum support price" when every price chunk has those. Words that occur in no chunk (a Hindi
    word against English documents) are left out of the denominator instead of dragging every chunk down."""
    docs = [_terms(h.text) for h in pool]
    n = max(len(docs), 1)
    weights = {}
    for t in _terms(query):
        df = sum(1 for d in docs if t in d)
        if df:
            weights[t] = math.log(1 + n / df)
    total = sum(weights.values()) or 1.0
    by_id = {id(h): d for h, d in zip(pool, docs)}
    return lambda h: sum(w for t, w in weights.items() if t in by_id.get(id(h), set())) / total


def split_front_matter(text: str) -> tuple[dict, str]:
    """'key: value' lines above a line of three dashes -> ({key: value}, the rest). No header -> ({}, text)."""
    head, sep, rest = text.partition("\n---\n")
    if not sep or not all(":" in ln for ln in head.splitlines() if ln.strip()):
        return {}, text
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in head.splitlines() if ln.strip())}, rest


def chunk(text: str, size: int = 500) -> list[str]:
    """Paragraphs joined greedily into chunks of about `size` characters; a paragraph is never split."""
    chunks, current = [], ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) > size:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
