from app.activity import cards_from_page, categorize_responses, classify_reply, incoming_replies, totals


def test_question_and_objection_are_separated() -> None:
    assert classify_reply("How does the morning list work?")[0] == "question"
    assert classify_reply("Please stop replying to me")[0] == "objection"


def test_counts_group_the_same_replies() -> None:
    summary, categories, rows = categorize_responses(
        [{"id": "1", "text": "Thanks, this is great"}, {"id": "2", "text": "This looks like spam"}]
    )
    assert "praise" in summary
    assert rows[0]["category"] == "praise"
    assert rows[1]["category"] == "negative"
    assert sum(item["count"] for item in categories) == 2


def test_card_keeps_the_status_id() -> None:
    cards = cards_from_page(
        [{"handle": "tedcoachhq", "text": "Three tasks at 9.", "href": "/tedcoachhq/status/55", "views": 1200, "likes": 4}]
    )
    assert cards[0]["id"] == "55"
    assert cards[0]["views"] == 1200
    assert cards[0]["url"] == "https://x.com/tedcoachhq/status/55"


def test_incoming_skips_the_account_and_the_focus_post() -> None:
    cards = [
        {"id": "1", "handle": "tedcoachhq", "text": "mine"},
        {"id": "2", "handle": "reader", "text": "back"},
        {"id": "3", "handle": "tedcoachhq", "text": "also mine"},
    ]
    incoming = incoming_replies(cards, "1", "tedcoachhq")
    assert [item["id"] for item in incoming] == ["2"]


def test_totals_add_posts_and_comments() -> None:
    snapshot = {"posts": [{"views": 10, "likes": 1}], "comments": [{"views": None, "likes": 2}]}
    totals(snapshot)
    assert snapshot["views"] == 10
    assert snapshot["likes"] == 3
