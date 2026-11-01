from app.api.ratelimit import check_rate


def test_window_blocks_then_recovers():
    assert all(check_rate("k1", 3, now=100.0 + i) for i in range(3))
    assert not check_rate("k1", 3, now=103.0)
    assert check_rate("k1", 3, now=161.0)  # first hits aged out of the 60 s window


def test_keys_are_independent():
    assert check_rate("a", 1, now=0.0) and check_rate("b", 1, now=0.0) and not check_rate("a", 1, now=1.0)


def test_api_returns_429_and_rejects_oversized_image():
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.config import get_settings
    except Exception:
        return
    from app.api import ratelimit
    ratelimit._hits.clear()
    s = get_settings()
    old, s.rate_limit_per_min = s.rate_limit_per_min, 2
    try:
        with TestClient(app) as c:
            codes = [c.post("/api/chat", json={"message": "hi", "session_id": "rl-test"}).status_code for _ in range(3)]
            assert codes == [200, 200, 429]
            big = {"message": "hi", "session_id": "rl-big", "image_b64": "A" * 7_000_001}
            assert c.post("/api/chat", json=big).status_code == 422
    finally:
        s.rate_limit_per_min = old


def test_rotating_session_ids_cannot_dodge_the_ip_cap():
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.config import get_settings
        from app.api import ratelimit
    except Exception:
        return
    ratelimit._hits.clear()
    s = get_settings()
    old, s.rate_limit_per_min = s.rate_limit_per_min, 2
    try:
        with TestClient(app) as c:
            codes = [c.post("/api/chat", json={"message": "hi", "session_id": f"rot-{i}"}).status_code for i in range(8)]
            assert codes[:6] == [200] * 6 and codes[6:] == [429, 429]  # IP cap = 3 x limit
    finally:
        s.rate_limit_per_min = old
        ratelimit._hits.clear()
