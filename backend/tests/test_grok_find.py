import threading

from fastapi.testclient import TestClient

from app.browser_session import SessionState, _chatgpt_conversation, _chatgpt_cookie, _grok_conversation, chatgpt_should_switch
from app.config import get_settings
from app.grok_find import (
    CREATOR_COUNT,
    LIMIT_NOTICE,
    POSTS_PER_QUERY,
    QUERY_COUNT,
    QUERY_PAUSE_SECONDS,
    SEARCH_SCAN,
    creators_from_reply,
    find_message,
    limit_reached,
    limit_reply,
    more_message,
    posts_from_reply,
    product_message,
    queries_from_reply,
    search_queries_message,
)
from app.main import create_app
from app.state import get_state, reset_state


def test_a_different_chatgpt_email_replaces_the_open_session() -> None:
    assert chatgpt_should_switch("", "new@example.com", True)
    assert chatgpt_should_switch("old@example.com", "new@example.com", True)
    assert not chatgpt_should_switch("new@example.com", "new@example.com", True)
    assert not chatgpt_should_switch("old@example.com", "", True)
    assert _chatgpt_cookie({"domain": ".chatgpt.com"})
    assert _chatgpt_cookie({"domain": "auth.openai.com"})
    assert not _chatgpt_cookie({"domain": ".x.com"})


def test_chatgpt_comment_stays_on_one_chat_id() -> None:
    url = "https://chatgpt.com/c/6aeeefc5-0ba0-83e8-9737-3ed86bd713cb"
    assert _chatgpt_conversation(url) == "6aeeefc5-0ba0-83e8-9737-3ed86bd713cb"
    assert _chatgpt_conversation("https://chatgpt.com/") == ""


def test_x_grok_conversation_id_is_kept() -> None:
    url = "https://x.com/i/grok?conversation=9a6d2af3-78ee-41a5-93fd-ec5fb7c5f45e"
    assert _grok_conversation(url) == "9a6d2af3-78ee-41a5-93fd-ec5fb7c5f45e"
    assert _grok_conversation("https://x.com/i/grok") == ""


def test_grok_message_keeps_the_saved_detail() -> None:
    detail = (
        "Ted (Ted Coach AI) is a quiet Mac app that lives in the MacBook notch. "
        "At 9 it asks for three tasks. Words the audience uses stay inside this paragraph."
    )
    message = product_message("Ted", "https://www.tedcoachai.com/", detail)
    assert "Details: " + detail in message
    assert message.startswith("This is my product.")
    assert "Do not search for posts yet." in message
    assert find_message(20).count("20 posts") == 1
    assert "more than 500 views" in find_message(20)
    assert "content universe" in find_message(20)
    assert "content universe" in search_queries_message("Ted", "https://tedcoachai.com", "A quiet Mac app.")
    handles = creators_from_reply("1. @habitwriter\n2. FocusDaily\nnot-a-handle\nFocusDaily")
    assert handles == ["habitwriter", "FocusDaily"]
    assert len(creators_from_reply("\n".join(f"writer{index}" for index in range(1, 15)))) == CREATOR_COUNT
    assert more_message(20) == "20 more"
    assert more_message(10) == "10 more"
    assert more_message(0) == "1 more"


def test_posts_keep_only_real_status_urls_over_500() -> None:
    reply = """
https://x.com/Sana_ShahUk/status/2113285567358103711
(Meoz; 51,943 views)
https://x.com/low/status/199
(400 views)
https://x.com/Sana_ShahUk/status/2113285567358103711
https://x.com/plain/status/2118701105164976550
"""
    rows = posts_from_reply(reply)
    ids = [row["external_id"] for row in rows]
    assert ids == ["2113285567358103711", "2118701105164976550"]
    assert rows[0]["views"] == 51943
    assert rows[0]["url"] == "https://x.com/Sana_ShahUk/status/2113285567358103711"
    assert limit_reached("Free tier limit reached. Try again later or upgrade to Premium.")
    assert limit_reached("You've reached your limit for Grok.")
    assert not limit_reached("Here are 20 posts from people who use a Mac.")
    banner = (
        "Free tier limit reached\n"
        "Try again later or upgrade to Premium for much higher limits and premium features."
    )
    reply = "Say back what the product is, who it is for, and the situation it fits."
    noticed = limit_reply(reply, banner)
    assert limit_reached(noticed)
    assert reply in noticed
    assert LIMIT_NOTICE in noticed


def test_chatgpt_query_list_keeps_fifty_unique_lines() -> None:
    lines = [f"{index}. mac focus habit {index}" for index in range(1, 56)]
    lines[2] = "2) mac focus habit 2"
    lines.append("- mac focus habit 4")
    reply = "Here are 50 X search queries:\n```text\n" + "\n".join(lines) + "\n```"
    queries = queries_from_reply(reply)
    assert queries[0] == "mac focus habit 1"
    assert queries[1] == "mac focus habit 2"
    assert "mac focus habit 3" not in queries
    assert queries[2] == "mac focus habit 4"
    assert len(queries) == QUERY_COUNT
    assert len(set(queries)) == QUERY_COUNT
    message = search_queries_message("Ted", "https://tedcoachai.com", "A quiet Mac app.")
    assert f"write {QUERY_COUNT} X search queries" in message
    assert "Analysis:\nA quiet Mac app." in message
    assert limit_reached("Daily limit reached")
    assert limit_reached("Daily limit reach")


def test_grok_limit_saves_queries_then_takes_two_posts_from_each(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    product = _verify(client, tmp_path, "Ted", "https://tedcoachai.com", detail)
    from app.state import get_state as load_state

    state = load_state()
    saved = state.store.get_product(product["id"])
    assert saved is not None
    saved["chat_url"] = "https://chatgpt.com/c/6aeeefc5-0ba0-83e8-9737-3ed86bd713cb"
    state.store.save_product(saved)
    queries = [f"mac focus habit {index}" for index in range(1, QUERY_COUNT + 3)]
    session = _FinderSession(
        grok_replies=[
            "Ted is a quiet Mac app for people who work on a Mac.",
            "https://x.com/ada/status/2113285567358103711\n(2,400 views)\nYou've reached your limit. Try again later.",
        ],
        chatgpt_reply="\n".join(f"{index}. {query}" for index, query in enumerate(queries, start=1)),
    )
    slept: list[float] = []
    monkeypatch.setattr("app.services.BrowserSession", lambda settings: session)
    monkeypatch.setattr("app.services.time.sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(state.jobs, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(state.jobs, "update", lambda *args, **kwargs: None)

    result = state.service._grok_find({"id": "job", "cancel": threading.Event()}, state.store.get_product(product["id"]) or saved, 20)

    assert "Analyze this product." in session.chatgpt_prompts[0]
    assert "https://tedcoachai.com" in session.chatgpt_prompts[0]
    assert f"write {QUERY_COUNT} X search queries" in session.chatgpt_prompts[-1]
    assert session.chatgpt_chat == ["https://chatgpt.com/c/6aeeefc5-0ba0-83e8-9737-3ed86bd713cb"] * len(session.chatgpt_prompts)
    assert [item[1] for item in session.searches] == [SEARCH_SCAN] * (QUERY_COUNT + CREATOR_COUNT)
    assert [item[0] for item in session.searches[:QUERY_COUNT]] == queries[:QUERY_COUNT]
    assert [item[0] for item in session.searches[QUERY_COUNT:]] == [f"from:habitwriter{index}" for index in range(1, CREATOR_COUNT + 1)]
    assert len(slept) == (QUERY_COUNT + CREATOR_COUNT - 1) * QUERY_PAUSE_SECONDS
    assert set(slept) == {1}
    assert result["queries"] == queries[:QUERY_COUNT]
    assert result["creators"] == [f"habitwriter{index}" for index in range(1, CREATOR_COUNT + 1)]
    assert result["added"] == 1 + QUERY_COUNT + CREATOR_COUNT
    stored = state.store.get_product(product["id"])
    assert stored is not None
    assert stored["search_queries"] == queries[:QUERY_COUNT]
    assert stored["creators"] == result["creators"]
    coming = state.store.list_coming(product["id"])
    assert len(coming) == 1 + QUERY_COUNT + CREATOR_COUNT
    assert "2113285567358103711" in {item["external_id"] for item in coming}
    per_query: dict[str, int] = {}
    for external_id in {item["external_id"] for item in coming if item["external_id"] != "2113285567358103711"}:
        per_query[external_id[:-1]] = per_query.get(external_id[:-1], 0) + 1
    assert set(per_query.values()) == {POSTS_PER_QUERY}
    assert len(session.grok_prompts) == 2
    assert session.grok_prompts[1] == find_message(20)


def test_free_tier_banner_on_the_first_message_switches_to_chatgpt(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    product = _verify(client, tmp_path, "Ted", "https://tedcoachai.com", detail)
    state = get_state()
    session = _FinderSession(
        grok_replies=[
            "Free tier limit reached\nTry again later or upgrade to Premium for much higher limits and premium features.",
        ],
        chatgpt_reply="1. first query about focus\n2. second query about planning",
    )
    monkeypatch.setattr("app.services.BrowserSession", lambda settings: session)
    monkeypatch.setattr("app.services.time.sleep", lambda seconds: None)
    monkeypatch.setattr(state.jobs, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(state.jobs, "update", lambda *args, **kwargs: None)

    result = state.service._grok_find({"id": "job", "cancel": threading.Event()}, state.store.get_product(product["id"]) or {}, 20)

    assert len(session.grok_prompts) == 1
    assert "Do not search for posts yet." in session.grok_prompts[0]
    assert [item[0] for item in session.searches[:2]] == ["first query about focus", "second query about planning"]
    assert [item[0] for item in session.searches[2:]] == [f"from:habitwriter{index}" for index in range(1, CREATOR_COUNT + 1)]
    assert result["added"] == 2 + CREATOR_COUNT
    assert result["queries"] == ["first query about focus", "second query about planning"]
    assert result["creators"] == [f"habitwriter{index}" for index in range(1, CREATOR_COUNT + 1)]


def test_a_grok_reply_that_never_arrives_still_asks_chatgpt(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    product = _verify(client, tmp_path, "Ted", "https://tedcoachai.com", detail)
    state = get_state()
    session = _FinderSession(
        grok_replies=["Ted is a quiet Mac app.", "RAISE"],
        chatgpt_reply="1. first query about focus\n2. second query about planning",
    )
    monkeypatch.setattr("app.services.BrowserSession", lambda settings: session)
    monkeypatch.setattr("app.services.time.sleep", lambda seconds: None)
    monkeypatch.setattr(state.jobs, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(state.jobs, "update", lambda *args, **kwargs: None)

    result = state.service._grok_find({"id": "job", "cancel": threading.Event()}, state.store.get_product(product["id"]) or {}, 20)

    assert session.grok_prompts[1] == find_message(20)
    assert "Analyze this product." in session.chatgpt_prompts[0]
    assert result["queries"] == ["first query about focus", "second query about planning"]
    assert result["added"] == 2 + CREATOR_COUNT


def test_grok_without_a_limit_does_not_ask_chatgpt(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    product = _verify(client, tmp_path, "Ted", "https://tedcoachai.com", detail)
    state = get_state()
    session = _FinderSession(
        grok_replies=[
            "Ted is a quiet Mac app.",
            "https://x.com/ada/status/2113285567358103711\n(2,400 views)",
            "",
        ]
    )
    slept: list[float] = []
    monkeypatch.setattr("app.services.BrowserSession", lambda settings: session)
    monkeypatch.setattr("app.services.time.sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(state.jobs, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(state.jobs, "update", lambda *args, **kwargs: None)

    result = state.service._grok_find({"id": "job", "cancel": threading.Event()}, state.store.get_product(product["id"]) or {}, 20)

    assert session.chatgpt_prompts == []
    assert session.searches == []
    assert slept == []
    assert result["added"] == 1
    assert result["queries"] == []


def test_a_stopped_break_does_not_start_the_next_query(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = "Ted is a Mac app. It asks for three tasks and is for people who work on a Mac."
    product = _verify(client, tmp_path, "Ted", "https://tedcoachai.com", detail)
    state = get_state()
    session = _FinderSession(
        grok_replies=["Ted is a quiet Mac app.", "You've reached your limit."],
        chatgpt_reply="1. first query about focus\n2. second query about planning",
    )
    job = {"id": "job", "cancel": threading.Event()}

    def stop_after_first_search(seconds: float) -> None:
        job["cancel"].set()

    monkeypatch.setattr("app.services.BrowserSession", lambda settings: session)
    monkeypatch.setattr("app.services.time.sleep", stop_after_first_search)
    monkeypatch.setattr(state.jobs, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(state.jobs, "update", lambda *args, **kwargs: None)

    result = state.service._grok_find(job, state.store.get_product(product["id"]) or {}, 20)

    assert [item[0] for item in session.searches] == ["first query about focus"]
    assert result["added"] == POSTS_PER_QUERY
    assert result["queries"] == ["first query about focus", "second query about planning"]


class _FinderSession:
    def __init__(self, grok_replies: list[str], chatgpt_reply: str = "") -> None:
        self.grok_replies = list(grok_replies)
        self.chatgpt_reply = chatgpt_reply
        self.grok_prompts: list[str] = []
        self.chatgpt_prompts: list[str] = []
        self.chatgpt_chat: list[str] = []
        self.searches: list[tuple[str, int]] = []
        self.grok_url = "https://x.com/i/grok?conversation=9a6d2af3-78ee-41a5-93fd-ec5fb7c5f45e"
        self.chatgpt_url = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def use_x_account(self, username, password, handle, wait, notice):
        return SessionState("x", "signed_in", handle or "ted", "Signed in.")

    def ask_grok(self, prompt, cancelled, chat_url="", expect_posts=False):
        self.grok_prompts.append(prompt)
        if not self.grok_replies:
            return ""
        reply = self.grok_replies.pop(0)
        if reply == "RAISE":
            raise RuntimeError("Grok did not finish a reply.")
        return reply

    def ask_chatgpt(self, prompt, cancelled, chat_url="", settle=8):
        self.chatgpt_prompts.append(prompt)
        self.chatgpt_chat.append(chat_url)
        self.chatgpt_url = chat_url or "https://chatgpt.com/c/new-chat-id-0000"
        if "search quer" in prompt.lower():
            return self.chatgpt_reply
        if "creator" in prompt.lower():
            return "\n".join(f"{index}. habitwriter{index}" for index in range(1, CREATOR_COUNT + 1))
        url = "https://tedcoachai.com"
        for line in prompt.splitlines():
            if line.lower().startswith("url:"):
                url = line.split(":", 1)[1].strip()
        return (
            "What it is\nA focused product.\n"
            "Features\nThe saved features.\n"
            "Target users\nPeople with this problem.\n"
            "Audience type\nPeople already talking about it.\n"
            f"What to use when promoting it: {url}\n"
        )

    def search_posts(self, query, limit, cancelled):
        self.searches.append((query, limit))
        index = len(self.searches)
        views = {"a": 400, "b": 900, "c": 2400}
        return [
            {
                "external_id": f"{index}{slot}",
                "author": "Ada",
                "handle": "ada",
                "text": f"{query} from slot {slot}",
                "url": f"https://x.com/ada/status/{index}{slot}",
                "created_at": None,
                "views": views[slot],
                "likes": 1,
                "reposts": 0,
                "replies": 0,
            }
            for slot in ("a", "b", "c")
        ]


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATABASE_MODE", "local")
    monkeypatch.setenv("LOCAL_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("BROWSER_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    get_settings.cache_clear()
    reset_state()
    return TestClient(create_app())


def _verify(client: TestClient, tmp_path, name: str, url: str, detail: str) -> dict:
    (tmp_path / "daily_post.json").write_text(
        '{"posts":[],"details":{"writeup":"%s","name":"%s","url":"%s","verified":false}}' % (detail, name, url),
        encoding="utf-8",
    )
    saved = client.post(
        "/api/post/details/verify",
        json={"name": name, "url": url, "detail": detail, "for_finder": True},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["product"]
