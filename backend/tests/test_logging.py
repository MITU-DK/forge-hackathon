"""5.6: logs must never contain API keys or user text."""
import logging

import httpx


def test_httpx_request_urls_are_not_logged_so_query_keys_cannot_leak(caplog):
    import app.main  # noqa: F401  (configures logging)

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    with caplog.at_level(logging.INFO):
        c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        c.get("https://api.data.gov.in/resource/x", params={"api-key": "SECRET-KEY-123"})
    assert "SECRET-KEY-123" not in caplog.text


def test_app_code_has_no_print_calls():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    assert [str(p) for p in root.rglob("*.py") if "print(" in p.read_text(encoding="utf-8")] == []
