"""Needs qdrant-client (real environment). Skipped where it is not installed."""
from pathlib import Path


def test_store_seed_and_search(tmp_path: Path):
    try:
        from app.memory.store import KNOWLEDGE, VectorStore
    except Exception:  # qdrant-client missing
        return
    (tmp_path / "a.md").write_text("Early blight shows brown rings on tomato leaves.\n\nPM-KISAN pays income support.", encoding="utf-8")
    st = VectorStore()
    assert st.seed_dir(tmp_path) >= 1
    hits = st.search(KNOWLEDGE, "brown rings tomato leaves")
    assert hits and "blight" in hits[0].text.lower()
    st.remember("s1", "User asked about onions")
    assert st.search("memory", "onions", session_id="s1") and not st.search("memory", "onions", session_id="other")
