from app.browser_session import _chatgpt_conversation, _grok_conversation
from app.grok_find import find_message, limit_reached, more_message, posts_from_reply, product_message


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
