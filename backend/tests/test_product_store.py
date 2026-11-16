from app.config import Settings
from app.store import Store, product_from_settings


class _Remote:
    def __init__(self, payload: dict | None) -> None:
        self.payload = payload
        self.writes = 0

    def get_settings(self) -> dict | None:
        return self.payload

    def save_settings(self, payload: dict) -> dict:
        self.payload = payload
        self.writes += 1
        return payload


def _store(tmp_path) -> Store:
    settings = Settings(
        database_mode="local",
        supabase_url="",
        supabase_publishable_key="",
        local_store_path=str(tmp_path / "store.json"),
    )
    return Store(settings)


def test_product_from_settings_needs_a_saved_id() -> None:
    assert product_from_settings({"product_name": "Ted"}) is None
    row = product_from_settings(
        {
            "product_name": "Vimvo",
            "product_url": "https://apps.apple.com/in/app/vimvo",
            "product_pitch": "A 60 day reset.",
            "selected_product_id": "2d7f2c26-265b-49c4-ba3d-1529e8f05111",
            "x_username": "social.vimvo@teamxelron.in",
            "x_password": "secret",
        }
    )
    assert row is not None
    assert row["name"] == "Vimvo"
    assert row["x_handle"] == ""
    assert row["x_password"] == "secret"


def test_missing_local_product_is_rebuilt_from_settings(tmp_path) -> None:
    store = _store(tmp_path)
    remote = _Remote(
        {
            "product_name": "Vimvo",
            "product_url": "https://apps.apple.com/in/app/vimvo",
            "product_pitch": "A 60 day reset.",
            "selected_product_id": "2d7f2c26-265b-49c4-ba3d-1529e8f05111",
            "x_username": "social.vimvo@teamxelron.in",
            "x_password": "secret",
        }
    )
    store.remote = remote
    rows = store.list_products()
    assert [item["name"] for item in rows] == ["Vimvo"]
    assert store.get_product("2d7f2c26-265b-49c4-ba3d-1529e8f05111")["x_password"] == "secret"
    assert remote.payload["products"][0]["id"] == "2d7f2c26-265b-49c4-ba3d-1529e8f05111"
    assert store.local.list_products()[0]["name"] == "Vimvo"


def test_activity_scan_is_kept_with_the_product(tmp_path) -> None:
    store = _store(tmp_path)
    remote = _Remote({"product_name": "Vimvo", "products": [{"id": "abc", "name": "Vimvo"}]})
    store.remote = remote
    store.save_activity_snapshot("abc", {"collected_at": "2026-11-16T00:00:00+00:00", "handle": "VimVo60", "posts": [{"id": "1"}]})
    assert store.load_activity_snapshot("abc")["handle"] == "VimVo60"
    store.save_settings({"product_name": "Vimvo"})
    assert store.load_activity_snapshot("abc")["posts"][0]["id"] == "1"
    assert remote.payload["products"][0]["id"] == "abc"


def test_saving_settings_keeps_products_already_in_the_payload(tmp_path) -> None:
    store = _store(tmp_path)
    remote = _Remote({"product_name": "Vimvo", "products": [{"id": "abc", "name": "Vimvo"}]})
    store.remote = remote
    store.save_settings({"product_name": "Vimvo", "product_url": "https://apps.apple.com/in/app/vimvo"})
    assert remote.payload["products"][0]["id"] == "abc"
    assert remote.payload["product_url"] == "https://apps.apple.com/in/app/vimvo"
