from app.agents.evidence import MAX_ENTRIES, build_evidence
from app.tools import ToolResult
from tests.helpers import hit

SRC = [{"name": "Open-Meteo forecast", "url": "https://x", "fetched_at": "2026-11-01T10:42:00Z", "stale": False}]


def test_numbered_lines_carry_their_source_and_time():
    ev = build_evidence({"weather": ToolResult("WEATHER Nashik\nRAIN next 48h: 70.0 mm", sources=SRC)}, [])
    assert len(ev) == 1 and "70.0 mm" in ev[0]["text"] and ev[0]["sources"][0]["fetched_at"] == "2026-11-01T10:42:00Z"


def test_failed_tools_give_no_evidence_and_documents_name_their_file():
    out = build_evidence({"mandi": ToolResult("MANDI FAILED 123", ok=False)}, [hit("MSP of wheat is Rs 2425 per quintal for 2025-26.", source="PIB MSP")])
    assert [e["kind"] for e in out] == ["document"] and out[0]["sources"][0]["name"] == "PIB MSP"


def test_size_is_capped_and_live_lines_survive():
    big = ToolResult("\n".join(f"line {i}" for i in range(200)), sources=SRC)
    out = build_evidence({"w": big}, [hit("x 5 y z. " * 50)])
    assert len(out) <= MAX_ENTRIES and out[0]["kind"] == "live"
