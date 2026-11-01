"""Official documents: fetch/parse, ingest with title and date, retrieval only where relevant, and numbers from tables."""
from pathlib import Path

from app.agents.orchestrator import Orchestrator
from app.domain.farm import DOMAIN
from app.guardrails.validators import grounded_units, run_all
from app.memory.store import split_front_matter
from scripts.fetch_knowledge import parse_release, render
from tests.helpers import FakeLLM, StubStore, hit
from tests.test_pipeline import domain

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"

PAGE = """<html><body><div>Press Release Page | Press Information Bureau</div><div>Ministry of Agriculture &amp; Farmers Welfare</div>
<h2>Cabinet approves Minimum Support Prices (MSP) for Rabi Crops</h2><div>Posted On:</div><div>30 SEP 2026 3:06PM by PIB Delhi</div>
<p>The Cabinet has approved the increase in MSP for all mandated Rabi crops for the marketing season. %s</p>
<table><tr><th>Crops</th><th>MSP</th><th>Cost</th></tr><tr><td>Wheat</td><td>2610</td><td>1264</td></tr><tr><td>Gram</td><td>5958</td><td>3751</td></tr></table>
<table><tr><th>Crops</th><th>MSP</th><th>Cost</th></tr><tr><td>Wheat</td><td>2610</td><td>1264</td></tr><tr><td>Gram</td><td>5958</td><td>3751</td></tr></table>
<div>-- words | -- min read</div><div>***</div><div>MJPS</div><div>(Release ID: 2316941)</div><script>var x = 1;</script></body></html>""" % ("The increase follows the budget commitment. " * 10)


def test_a_release_page_becomes_title_date_and_body_with_table_rows_intact_and_duplicates_dropped():
    d = parse_release(PAGE)
    assert d["title"] == "Cabinet approves Minimum Support Prices (MSP) for Rabi Crops" and d["posted"] == "30 SEP 2026 3:06PM" and d["release_id"] == "2316941"
    assert d["body"].count("Wheat | 2610 | 1264") == 1  # one price line per crop, and the repeated table is gone
    assert "Crops | MSP | Cost" in d["body"] and "min read" not in d["body"] and "MJPS" not in d["body"] and "var x" not in d["body"]


def test_a_page_that_is_not_a_release_is_refused_not_saved():
    import pytest

    with pytest.raises(ValueError):
        parse_release("<html><body><p>Nothing here</p></body></html>")


def test_front_matter_is_split_from_the_text_and_chunks_carry_title_and_date(tmp_path):
    import datetime as dt

    from app.memory.store import VectorStore

    d = parse_release(PAGE)
    (tmp_path / "msp.md").write_text(render(d, 2316941, "test", dt.date(2026, 10, 31)), encoding="utf-8")
    meta, body = split_front_matter((tmp_path / "msp.md").read_text(encoding="utf-8"))
    assert meta["title"].startswith("Cabinet approves") and meta["url"].endswith("PRID=2316941") and meta["fetched"] == "2026-10-31" and body.startswith("The Cabinet")
    assert split_front_matter("plain text\nwith: a colon but no header") == ({}, "plain text\nwith: a colon but no header")
    store = VectorStore()
    assert store.seed_dir(tmp_path) >= 1
    hits = store.search("knowledge", "minimum support price wheat")
    assert hits and hits[0].text.startswith("[Cabinet approves Minimum Support Prices (MSP) for Rabi Crops - 30 SEP 2026]") and hits[0].source == "msp.md"


def test_documents_are_retrieved_only_for_intents_that_use_them():
    for route, expect in (("weather_advice", 0), ("sell_or_hold", 1), ("general", 1)):
        d = domain(["good"])
        d.knowledge_intents = DOMAIN.knowledge_intents
        d.intents = DOMAIN.intents
        o = Orchestrator(d, FakeLLM(["ok"], route=route if route != "sell_or_hold" else "sell_or_hold"), StubStore([hit("[MSP doc - 30 SEP 2026] Wheat | 2610", "msp.md")]))
        r = o.run("what is the support price?", profile={"place": "Nashik"})
        assert len([t for t in r.trace if t.agent == "Retriever" and t.action.startswith(f"{expect} documents")]) == 1, (route, [t.action for t in r.trace])


def test_a_table_number_quoted_with_rupees_is_not_a_wrong_unit():
    ctx = "[MSP RMS 2027-28 - 30 SEP 2026] Wheat | 2610 | 1264 | 106 | 2585 | 25"
    assert grounded_units()("The MSP for wheat is Rs 2610 per quintal.", ctx) == []
    assert grounded_units()("The margin is 106 %.", ctx) == []  # a bare table figure may carry whichever unit the table's header gives it
    assert grounded_units()("The price is Rs 3.7 per quintal.", "SIGNAL 3.7 % change") != []  # the rule still bites where there is no document


def test_scheme_questions_route_to_scheme_info_by_keyword_in_three_languages():
    o = Orchestrator(domain(["good"]), FakeLLM(off=True), StubStore())
    o.domain.intents = DOMAIN.intents
    for text in ("What is the MSP of wheat?", "पीएम किसान की किस्त कब आती है?", "पीक विमा योजना काय आहे?", "Is PMFBY crop insurance available?"):
        assert o._route(text, None)[0].name == "scheme_info", text


def test_the_committed_documents_are_unedited_copies_with_a_source_url_and_date():
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    assert len(files) >= 5
    for f in files:
        meta, body = split_front_matter(f.read_text(encoding="utf-8"))
        assert meta["url"].startswith("https://www.pib.gov.in/PressReleasePage.aspx?PRID=") and meta["posted"] and meta["fetched"] and len(body) > 1500, f.name
    kharif = (KNOWLEDGE_DIR / "msp_kharif_2026_27.md").read_text(encoding="utf-8")
    assert "1. | Paddy | Common | 2441 |" in kharif and "Reading guide added by KisaanMitra" in kharif


def test_a_release_whose_date_sits_in_the_same_block_as_posted_on_keeps_its_first_paragraph():
    page = PAGE.replace("<div>Posted On:</div><div>30 SEP 2026 3:06PM by PIB Delhi</div>", "<div>Posted On: 30 SEP 2026 3:06PM by PIB Delhi</div>")
    d = parse_release(page)
    assert d["posted"] == "30 SEP 2026 3:06PM" and d["body"].startswith("The Cabinet has approved")  # the first paragraph was lost before 31 Oct 2026


# (question, profile crop, text the retrieved chunks must contain): the committed official documents, the offline hash embedder.
RETRIEVAL_CASES = [
    ("What is the MSP of wheat for 2027-28?", "Wheat", "Wheat | 2610 | 1264"),
    ("What is the kharif MSP of cotton?", "Cotton", "Cotton | (Medium Staple) | 8267"),
    ("खरीफ में धान का एमएसपी कितना है?", "Paddy", "Paddy | Common | 2441"),
    ("गेहूं का एमएसपी कितना है?", "Wheat", "Wheat | 2610 | 1264"),
    ("What premium do I pay for crop insurance in rabi?", "", "1.5% of sum insured for Rabi"),
    ("पीक विमा योजनेत शेतकऱ्याने किती प्रीमियम भरावा?", "", "1.5% of sum insured for Rabi"),
    ("How much does PM-KISAN pay and how often?", "", "Rs. 6,000/- to each eligible farmer family"),
    ("पीएम किसान की किस्त कितनी मिलती है?", "", "Rs. 6,000/- to each eligible farmer family"),
]


def test_retrieval_finds_the_right_chunk_for_prices_premiums_and_payments_in_three_languages():
    from app.domain.farm import retrieval_query
    from app.memory.store import VectorStore

    store = VectorStore()
    store.seed_dir(KNOWLEDGE_DIR)
    missed = []
    for q, crop, must in RETRIEVAL_CASES:
        hits = store.search("knowledge", retrieval_query(q, {"crop": crop} if crop else {}))
        if not any(must in h.text for h in hits):
            missed.append(q)
    assert not missed, missed


def test_every_price_chunk_carries_its_column_guide_or_header_so_a_number_cannot_lose_its_meaning():
    from app.memory.store import VectorStore

    store = VectorStore()
    store.seed_dir(KNOWLEDGE_DIR)
    hits = store.search("knowledge", "Paddy Common MSP kharif 2441")
    table = next(h for h in hits if "Paddy | Common | 2441" in h.text)
    assert "Reading guide added by KisaanMitra" in table.text and "Soybean (Yellow) | 5708" in table.text  # the whole table is one chunk
