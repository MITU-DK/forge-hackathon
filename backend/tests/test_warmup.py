from app.core.warmup import parse_places, warm
from app.tools import ToolResult


def test_parse_places():
    assert parse_places("Nashik, Maharashtra|Onion; Pune, Maharashtra ;; |x") == [{"place": "Nashik, Maharashtra", "crop": "Onion"}, {"place": "Pune, Maharashtra"}]
    assert parse_places("") == []


def test_warm_counts_successes_and_never_raises():
    def tool(text, profile):
        if profile["place"] == "Boom":
            raise RuntimeError("x")
        return ToolResult("", ok=profile["place"] != "Down")

    assert warm([{"place": "A"}, {"place": "Boom"}, {"place": "Down"}], tool) == 1
