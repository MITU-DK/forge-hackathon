"""Seed knowledge into a *persistent* Qdrant (when QDRANT_URL is set).
Run from backend/:  python -m scripts.seed
(With the default in-memory store, the API seeds itself on startup.)
"""
from pathlib import Path

from app.memory.store import get_store

if __name__ == "__main__":
    n = get_store().seed_dir(Path(__file__).parent.parent / "data" / "knowledge")
    print(f"seeded {n} chunks")
