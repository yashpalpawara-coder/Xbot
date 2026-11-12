import pytest

from app.validation import comment_problems, parse_json_object, unique_comments, weighted_length


def test_parse_json_from_fenced_text() -> None:
    parsed = parse_json_object('```json\n{"main_topic": "focus"}\n```')
    assert parsed["main_topic"] == "focus"


def test_url_counts_as_23_characters() -> None:
    assert weighted_length("Hi https://tedcoachai.com") == len("Hi ") + 23


def test_product_mention_is_rejected_when_promotion_is_off() -> None:
    problems = comment_problems(
        "Ted can ask for three tasks https://tedcoachai.com",
        product_name="Ted",
        product_url="https://tedcoachai.com",
        allow_promotion=False,
    )
    assert problems


def test_duplicate_replies_are_flagged() -> None:
    assert unique_comments({"natural": "Same reply", "insightful": "Same reply"})


def test_invalid_json_raises() -> None:
    with pytest.raises(ValueError):
        parse_json_object("no object here")
