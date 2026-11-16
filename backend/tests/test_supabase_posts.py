from app.store import SupabaseStore


class _Response:
    def __init__(self, payload: object) -> None:
        self.status_code = 200
        self.content = b"{}"
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _Http:
    def __init__(self, existing: list[dict] | None) -> None:
        self.existing = existing
        self.bodies: list[dict] = []

    def request(self, method: str, url: str, params: dict | None = None, json: dict | None = None, headers: dict | None = None) -> _Response:
        del url, params, headers
        if method == "GET":
            return _Response(self.existing or [])
        self.bodies.append(dict(json or {}))
        return _Response([json])


def test_saving_a_known_post_keeps_the_id_comments_use() -> None:
    store = SupabaseStore("https://example.supabase.co", "key")
    store.http = _Http([{"id": "existing-post"}])

    saved = store.upsert_post({"id": "brand-new-post", "external_id": "2113285567358103711", "text": "again"})

    assert saved["id"] == "existing-post"
    assert store.http.bodies[0]["id"] == "existing-post"
    assert store.http.bodies[0]["text"] == "again"


def test_a_new_post_keeps_its_new_id() -> None:
    store = SupabaseStore("https://example.supabase.co", "key")
    store.http = _Http([])

    saved = store.upsert_post({"id": "brand-new-post", "external_id": "99", "text": "first"})

    assert saved["id"] == "brand-new-post"
