import json
import threading

import pytest
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


def test_saving_a_second_product_keeps_both_in_the_menu(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    first = client.put("/api/settings", json=_settings_body("Ted", "https://tedcoachai.com"))
    assert first.status_code == 200
    second = client.put("/api/settings", json=_settings_body("Vimvo", "https://apps.apple.com/in/app/vimvo"))
    assert second.status_code == 200
    menu = client.get("/api/products").json()
    assert {item["name"] for item in menu["products"]} == {"Ted", "Vimvo"}
    assert menu["selected_id"]


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


def _verify(client: TestClient, tmp_path, name: str, url: str, detail: str, for_finder: bool = False) -> dict:
    (tmp_path / "daily_post.json").write_text(
        json.dumps({"posts": [], "details": {"writeup": detail, "name": name, "url": url, "verified": False}}),
        encoding="utf-8",
    )
    saved = client.post(
        "/api/post/details/verify",
        json={"name": name, "url": url, "detail": detail, "for_finder": for_finder},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["product"]


def _settings_body(name: str = "Ted", url: str = "https://tedcoachai.com") -> dict:
    return {
        "product_name": name,
        "product_url": url,
        "product_pitch": "Three tasks.",
        "allow_promotion": False,
        "search_limit": 12,
        "min_views": None,
        "min_likes": None,
    }


def test_each_product_has_its_own_coming_list_and_login(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    ted_detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    vim_detail = "Vimvo is a voice app. It turns a spoken note into a short written post."
    ted = _verify(client, tmp_path, "Ted", "https://tedcoachai.com", ted_detail, True)
    vim = _verify(client, tmp_path, "Vimvo", "https://vimvo.example", vim_detail, True)
    from app.state import get_state

    state = get_state()
    state.store.add_coming(
        [
            {"id": "11111111-1111-1111-1111-111111111111", "external_id": "101", "url": "https://x.com/a/status/101", "product_id": ted["id"], "status": "saved"},
            {"id": "22222222-2222-2222-2222-222222222222", "external_id": "202", "url": "https://x.com/b/status/202", "product_id": vim["id"], "status": "saved"},
            {"id": "33333333-3333-3333-3333-333333333333", "external_id": "303", "url": "https://x.com/c/status/303", "status": "saved"},
            {"id": "44444444-4444-4444-4444-444444444444", "external_id": "101", "url": "https://x.com/a/status/101", "product_id": vim["id"], "status": "saved"},
        ]
    )
    listed = client.get("/api/finder/coming").json()["posts"]
    assert {item["external_id"] for item in listed} == {"202", "303", "101"}
    raw = state.service._raw_settings()
    raw["selected_product_id"] = ted["id"]
    state.store.save_settings(raw)
    listed = client.get("/api/finder/coming").json()["posts"]
    assert [item["external_id"] for item in listed] == ["101"]

    saved = client.put("/api/settings", json={**_settings_body(), "x_username": "tedcoachhq", "x_password": "ted-secret"})
    assert saved.status_code == 200
    raw = state.service._raw_settings()
    raw["selected_product_id"] = vim["id"]
    state.store.save_settings(raw)
    again = client.put("/api/settings", json={**_settings_body("Vimvo", "https://vimvo.example"), "x_username": "VimVo60", "x_password": "vim-secret"})
    kept = client.put("/api/settings", json={**_settings_body("Vimvo", "https://vimvo.example"), "x_username": "VimVo60", "x_password": ""})
    assert kept.json()["settings"]["x_username"] == "VimVo60"
    assert kept.json()["settings"]["x_password_set"] is True
    assert "vim-secret" not in again.text
    assert state.store.get_product(vim["id"])["x_password"] == "vim-secret"
    assert state.store.get_product(ted["id"])["x_password"] == "ted-secret"
    exported = client.get("/api/export").text
    assert "ted-secret" not in exported
    assert "vim-secret" not in exported
    menu = client.get("/api/products").json()
    assert {item["name"] for item in menu["products"]} == {"Ted", "Vimvo"}
    assert menu["selected_id"] == vim["id"]


def test_comments_use_the_saved_analysis_without_a_new_chat(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    product = _verify(client, tmp_path, "Ted", "https://tedcoachai.com", detail, True)
    from app.state import get_state

    state = get_state()
    state.store.add_coming(
        [
            {
                "id": "55555555-5555-5555-5555-555555555555",
                "external_id": "909",
                "url": "https://x.com/a/status/909",
                "product_id": product["id"],
                "status": "saved",
            }
        ]
    )
    started: list[str] = []

    def fake_start(kind, work):
        started.append(kind)
        return {"id": "job-1", "type": kind, "status": "running", "progress": 0, "message": "Starting", "error": None, "results_count": 0, "logs": [], "result": None}

    monkeypatch.setattr(state.jobs, "start", fake_start)
    response = client.post("/api/coming/comment")
    assert response.status_code == 200
    assert started == ["coming_comments"]


def test_refresh_switches_account_before_it_reads(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Vimvo is a voice app. It turns a spoken note into a short written post."
    product = _verify(client, tmp_path, "Vimvo", "https://vimvo.example", detail, True)
    from app.browser_session import SessionState
    from app.state import get_state

    state = get_state()
    saved = state.store.get_product(product["id"])
    assert saved is not None
    saved["x_username"] = "VimVo60"
    saved["x_password"] = "vim-secret"
    saved["x_handle"] = "VimVo60"
    state.store.save_product(saved)
    calls: list[tuple] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def use_x_account(self, username, password, handle, wait, notice):
            calls.append(("account", username, handle))
            notice("Signing out of @tedcoachhq.")
            return SessionState("x", "signed_in", "VimVo60", "Signed in.")

        def ensure_x(self, *args, **kwargs):
            raise AssertionError("refresh must not keep the open session")

    def fake_collect(session, path, log, progress, stop):
        calls.append(("collect",))
        return {"handle": "VimVo60", "collected_at": "2026-11-15T00:00:00+00:00"}

    monkeypatch.setattr("app.services.BrowserSession", lambda settings: FakeSession())
    monkeypatch.setattr("app.services.collect_activity", fake_collect)
    monkeypatch.setattr(state.jobs, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(state.jobs, "update", lambda *args, **kwargs: None)
    result = state.service._scan_activity({"id": "job", "cancel": threading.Event()})
    assert calls == [("account", "VimVo60", "VimVo60"), ("collect",)]
    assert result["handle"] == "VimVo60"


def test_refresh_stops_when_the_selected_product_has_no_login(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Vimvo is a voice app. It turns a spoken note into a short written post."
    _verify(client, tmp_path, "Vimvo", "https://vimvo.example", detail, True)
    from app.browser_session import SessionState
    from app.state import get_state

    state = get_state()
    calls: list[str] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def use_x_account(self, username, password, handle, wait, notice):
            calls.append("account")
            return SessionState("x", "signed_out", "", "This product has no X login. Add the username and password in Settings, then select it again.")

        def ensure_x(self, *args, **kwargs):
            raise AssertionError("refresh must not keep the open session")

    monkeypatch.setattr("app.services.BrowserSession", lambda settings: FakeSession())
    monkeypatch.setattr("app.services.collect_activity", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not read")))
    monkeypatch.setattr(state.jobs, "log", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="no X login"):
        state.service._scan_activity({"id": "job", "cancel": threading.Event()})
    assert calls == ["account"]


def test_publish_requires_approval(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    missing = client.post("/api/comments/11111111-1111-1111-1111-111111111111/publish", json={"idempotency_key": "key-123456"})
    assert missing.status_code == 404
