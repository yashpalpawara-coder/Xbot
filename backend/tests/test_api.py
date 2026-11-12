from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.state import reset_state


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATABASE_MODE", "local")
    monkeypatch.setenv("LOCAL_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("BROWSER_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    get_settings.cache_clear()
    reset_state()
    return TestClient(create_app())


def test_health(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == "local"


def test_search_requires_a_query(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/finder/search", json={})
    assert response.status_code == 422
    assert "topic" in response.json()["error"]["message"].lower() or response.json()["error"]["code"] == "invalid"


def test_settings_round_trip(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    saved = client.put(
        "/api/settings",
        json={
            "product_name": "Ted",
            "product_url": "https://tedcoachai.com",
            "product_pitch": "Three tasks.",
            "allow_promotion": False,
            "search_limit": 12,
            "min_views": None,
            "min_likes": 5,
        },
    )
    assert saved.status_code == 200
    assert client.get("/api/settings").json()["settings"]["search_limit"] == 12


def test_x_login_is_saved_without_returning_the_password(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    from app.state import get_state

    saved = client.put(
        "/api/settings",
        json={
            "product_name": "Ted",
            "product_url": "https://tedcoachai.com",
            "product_pitch": "Three tasks.",
            "allow_promotion": False,
            "search_limit": 12,
            "x_username": "tedcoachhq",
            "x_password": "secret-value",
        },
    )
    assert saved.status_code == 200
    body = saved.json()["settings"]
    assert body["x_username"] == "tedcoachhq"
    assert body["x_password_set"] is True
    assert "x_password" not in body
    assert "secret-value" not in saved.text
    again = client.put(
        "/api/settings",
        json={
            "product_name": "Ted",
            "product_url": "https://tedcoachai.com",
            "product_pitch": "Three tasks.",
            "allow_promotion": False,
            "search_limit": 12,
            "x_username": "tedcoachhq",
            "x_password": "",
        },
    )
    assert again.json()["settings"]["x_password_set"] is True
    state = get_state()
    assert state.service.settings.x_username == "tedcoachhq"
    assert state.service.settings.x_password == "secret-value"
    exported = client.get("/api/export").text
    assert "secret-value" not in exported


def test_verify_saves_the_product(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    (tmp_path / "daily_post.json").write_text(
        '{"posts":[],"details":{"writeup":"%s","name":"Ted","url":"https://tedcoachai.com","verified":false}}' % detail,
        encoding="utf-8",
    )
    saved = client.post(
        "/api/post/details/verify",
        json={"name": "Ted", "url": "https://tedcoachai.com", "detail": detail},
    )
    assert saved.status_code == 200
    product = saved.json()["product"]
    assert product["name"] == "Ted"
    assert product["detail"] == detail
    assert product["finder_verified"] is False
    assert client.get("/api/product").json()["product"]["url"] == "https://tedcoachai.com"
    blocked = client.post("/api/finder/grok")
    assert blocked.status_code == 409
    finder = client.post(
        "/api/post/details/verify",
        json={"name": "Ted", "url": "https://tedcoachai.com", "detail": detail, "for_finder": True},
    )
    assert finder.status_code == 200
    assert finder.json()["product"]["finder_verified"] is True
    assert client.get("/api/finder/coming").json()["posts"] == []
    waiting = client.post("/api/coming/comment")
    assert waiting.status_code == 409
    rejected = client.post("/api/post/details", json={"name": "", "url": "tedcoachai.com"})
    assert rejected.status_code == 422


def test_publish_requires_approval(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    missing = client.post("/api/comments/11111111-1111-1111-1111-111111111111/publish", json={"idempotency_key": "key-123456"})
    assert missing.status_code == 404
