from pathlib import Path

from app.validation import weighted_length
from app.writer import compose_draft, finish, mark_posted, preferred_style, prepare_details, public_state, rank_styles, repeats_post, store_details, store_draft, writing_style


def test_style_names_the_shape() -> None:
    assert writing_style("47 tasks and one quiet list.") == "a number first, short"
    assert writing_style("What did you finish?") == "a question, short"


def test_rank_uses_the_higher_median() -> None:
    ranked = rank_styles(
        [
            {"text": "What did you finish?", "views": 10},
            {"text": "What slipped?", "views": 30},
            {"text": "The list is still there.", "views": 4},
        ]
    )
    assert ranked["winner"] == "a question, short"
    assert ranked["styles"][0]["median_views"] == 30


def test_own_shape_wins_when_it_has_views() -> None:
    own = {"winner": "a number first, short", "styles": [{"best_views": 12}]}
    related = {"winner": "a question, short", "styles": [{"best_views": 90}]}
    assert preferred_style(own, related) == "a number first, short"
    own["styles"][0]["best_views"] = 0
    assert preferred_style(own, related) == "a question, short"


def test_close_copies_count_as_the_same_post() -> None:
    earlier = ["Your backlog has 47 tasks and Ted asks for three of them."]
    assert repeats_post(earlier[0], earlier)
    assert not repeats_post("A quiet notch list for the one thing still open tonight.", earlier)


def test_opening_line_is_not_the_product_details() -> None:
    from app.writer import is_preamble

    opening = "I’ll read Ted’s website and separate verified product features from marketing claims. I won’t draft a reply yet."
    assert is_preamble(opening)
    assert not is_preamble("Ted is a Mac app. " * 80)


def test_finish_keeps_the_link_last_and_the_length_limit() -> None:
    text = finish("A quiet morning list " * 40, "https://tedcoachai.com", ["#nope"], [{"tag": "#macOS"}, {"tag": "#productivity"}])
    assert text.endswith("https://tedcoachai.com")
    assert "#macOS #productivity" in text
    assert "✨" in text
    assert weighted_length(text) <= 280


def test_compose_uses_the_saved_scan_and_stores_a_draft(tmp_path: Path) -> None:
    snapshot = {
        "posts": [{"text": "47 tasks. Ted asks for 3. #macOS", "views": 80}],
        "comments": [{"text": "What did you finish?", "views": 20}],
    }

    def details_complete(prompt: str) -> str:
        assert "What it is" in prompt
        assert "Features" in prompt
        assert "Do not write an X post." in prompt
        assert "https://tedcoachai.com" in prompt
        assert "Name: Ted" in prompt
        return "Ted is a Mac notch app. It asks for up to three tasks and is spoken by Ted, not by a customer."

    details = prepare_details(snapshot, name="Ted", url="https://tedcoachai.com", pitch="A Mac notch app.", complete=details_complete)
    assert "three tasks" in details["writeup"]

    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            assert "the quiet list at 9" in prompt
            assert "one code block" in prompt
            assert "a number first, short" in prompt
            return "Here is a product analysis of what it is, who it is for, and how it works."
        assert "That was not a post" in prompt
        return "```\nThree tasks sit in the notch before the day gets loud.\n#macOS #productivity\nhttps://tedcoachai.com\n```"

    record = compose_draft(
        snapshot,
        name="Ted",
        url="https://tedcoachai.com",
        pitch="A Mac notch app.",
        earlier=[],
        user_input="the quiet list at 9",
        details=details,
        complete=complete,
    )
    assert len(calls) == 2
    assert record["posted"] is False
    assert record["text"].endswith("https://tedcoachai.com")
    assert "#macOS" in record["text"]
    path = tmp_path / "daily_post.json"
    store_details(path, details)
    store_draft(path, record)
    assert "three tasks" in public_state(path)["details"]["writeup"]
    assert public_state(path)["pending"]["text"] == record["text"]
    posted = mark_posted(path, record["text"], "https://x.com/tedcoachhq/status/1")
    assert posted["posted"] is True
    assert posted["posted_url"].endswith("/1")
