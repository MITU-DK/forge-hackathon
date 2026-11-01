"""A place or crop written in the question beats the profile form for that turn ("price of tomato in Pune")."""
from app.agents.orchestrator import Orchestrator, _named_place
from app.domain.farm import question_overrides
from app.tools.mandi import crops_in_text
from tests.helpers import FakeLLM, StubStore
from tests.test_pipeline import domain, good


def orch(place="", tools=None, crop=""):
    seen = []

    def spy(text, profile):
        seen.append(dict(profile))
        return good(text, profile)

    d = domain(["spy"], tools={"spy": spy, **(tools or {})})
    d.question_overrides = question_overrides
    return Orchestrator(d, FakeLLM(["Max 33 C."], place=place, crop=crop), StubStore()), seen


def test_question_place_and_crop_override_the_profile_and_drop_the_old_coordinates():
    o, seen = orch(place="Pune")
    r = o.run("What is the price of tomato in Pune?", profile={"place": "Nashik, Maharashtra", "crop": "Onion", "lat": 20.0, "lon": 73.8, "state": "Maharashtra", "district": "Nashik"})
    assert seen[0] == {"place": "Pune", "crop": "Tomato"}
    assert any(t.action == "question overrides profile" and "Pune" in t.detail for t in r.trace)


def test_question_with_no_place_or_crop_leaves_the_profile_alone():
    o, seen = orch()
    o.run("Should I sell now?", profile={"place": "Nashik", "crop": "Onion"})
    assert seen[0] == {"place": "Nashik", "crop": "Onion"}


def test_override_works_when_the_profile_is_empty_hinglish_crop():
    o, seen = orch(place="Indore")
    o.run("pyaz ka bhav kya hai Indore mein?", profile={})
    assert seen[0] == {"place": "Indore", "crop": "Onion"}


def test_override_is_for_this_turn_only_and_never_remembered():
    o, seen = orch()
    o.run("will it rain?", session_id="s1", profile={"place": "Nashik", "crop": "Onion"})  # remembered
    o.llm.place = "Pune"
    o.run("tomato price in Pune?", session_id="s1", profile={})
    o.llm.place = ""
    o.run("and the weather?", session_id="s1", profile={})
    assert seen[1]["place"] == "Pune" and seen[1]["crop"] == "Tomato"
    assert seen[2]["place"] == "Nashik" and seen[2]["crop"] == "Onion"  # back to the profile
    assert o.profiles["s1"] == {"place": "Nashik", "crop": "Onion"}


def test_two_crops_in_one_question_are_ambiguous_so_the_profile_crop_stays():
    assert question_overrides({"crop": "Onion"}, {}, "Should I sell tomatoes or potatoes?") == {}


def test_a_place_the_router_invented_or_that_is_not_in_the_question_is_dropped():
    assert _named_place("Mumbai", "Will it rain tomorrow?") == ""  # injected/hallucinated
    assert _named_place("Pune", "price in pune?") == "Pune"
    assert _named_place(["Pune"], "pune") == "" and _named_place("P", "p") == ""
    assert question_overrides({}, {"place": "India"}, "x") == {} and question_overrides({"place": "Nashik, Maharashtra"}, {"place": "Nashik"}, "x") == {}


def test_crop_names_in_english_hinglish_hindi_and_inflected_marathi():
    assert crops_in_text("price of tomatoes") == ["tomato"] and crops_in_text("pyaz ka bhav") == ["onion"]
    assert crops_in_text("आज कांद्याचा भाव काय आहे?") == ["onion"] and crops_in_text("क्या मैं अपना गेहूं बेचूं?") == ["wheat"]
    assert crops_in_text("आमच्या गावात पाऊस") == [] and crops_in_text("Will it rain tomorrow in Nagpur?") == []


def test_a_crop_the_code_does_not_know_comes_from_the_router_not_from_the_profile():
    o, seen = orch(crop="dragon fruit")
    o.run("What is the price of dragon fruit?", profile={"place": "Pune", "crop": "Onion"})
    assert seen[0]["crop"] == "Dragon Fruit"
    o2, seen2 = orch(crop="dragon fruit")
    o2.run("What is the weather?", profile={"place": "Pune", "crop": "Onion"})  # the router named a crop the user never wrote: dropped
    assert seen2[0]["crop"] == "Onion"
    assert question_overrides({"crop": "Onion"}, {"crop": "okra"}, "price of okra") == {"crop": "Okra"}
