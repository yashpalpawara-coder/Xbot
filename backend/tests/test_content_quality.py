from datetime import datetime, timezone

from app.grok_find import creators_message, over_view_threshold, posts_from_reply, search_queries_message
from app.prompts import comments_prompt, original_post_prompt, product_brief, revise_analysis_prompt
from app.reply_rules import choose_post, comment_notes, conversation_rank, engagement_count, reply_gate, reply_outcome, reply_problems, reply_prompt, reply_quality
from app.validation import parse_count
from app.writer import repeats_post

FACTS = (
    "What it is\nA habit app.\n"
    "Features\nVimVo confirms step and workout goals from Apple Health.\n"
    "Limitations\nIt does not track calories.\n"
)
HEALTH = "Does anyone know a habit tracker that automatically records steps from Apple Health?"
VIRAL = "Drake dropped the album and the timeline will not shut up about the halftime show."
SCROLL = "I spent three hours doomscrolling instead of the one thing I said I would do."
SENSITIVE = "I was diagnosed with depression last year and I do not know how to tell my family."


def test_a_relevant_question_with_no_replies_is_kept() -> None:
    assert "500" in reply_gate(80, 0, HEALTH, FACTS)
    assert reply_gate("1.2K", 0, HEALTH, FACTS) == ""


def test_an_unrelated_viral_post_is_skipped() -> None:
    reason = reply_gate(900_000, 4000, VIRAL, FACTS)
    assert reason.startswith("Skip:")


def test_a_doomscrolling_post_can_still_be_drafted() -> None:
    assert "500" in reply_gate(30, 1, SCROLL, FACTS)
    assert reply_gate(800, 1, SCROLL, FACTS) == ""


def test_a_personal_health_disclosure_is_skipped() -> None:
    assert "health disclosure" in reply_gate(20000, 80, SENSITIVE, FACTS)


def test_prompts_match_the_post_language_and_can_skip() -> None:
    french = "Est-ce que quelqu'un connaît une app qui reprend les pas d'Apple Health?"
    message = reply_prompt(french, "https://x.com/a/status/1", name="VimVo", url="https://example.com", analysis=FACTS)
    studio = comments_prompt(
        {"handle": "ada", "text": french},
        None,
        allow_promotion=True,
        product_name="VimVo",
        product_url="https://example.com",
        product_pitch=FACTS,
    )
    assert "same language" in message
    assert "same language" in studio
    assert "SKIP:" in message
    assert "should_reply" in studio
    assert "three possible replies" not in studio
    drafted = original_post_prompt("VimVo", "https://example.com", "a missed walk")
    assert "Do not default to a short poem" in drafted
    assert "a poet would keep" not in drafted
    assert "optional" in drafted


def test_a_repeated_opening_is_rejected() -> None:
    earlier = ["A fresh start doesn't need perfection. Just one habit worth keeping."]
    again = "A fresh start needs no perfect day. Just one small promise kept."
    assert repeats_post(again, earlier)


def test_a_natural_reply_that_misses_the_point_is_rejected() -> None:
    missed = "Monday mornings feel softer when the coffee is actually good."
    problems = reply_problems(missed, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS)
    assert any("address" in item for item in problems)


def test_an_unsupported_feature_is_rejected() -> None:
    claim = "VimVo can track calories from your meals while the walk is logged."
    problems = reply_problems(claim, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS)
    assert any("facts" in item for item in problems)


def test_a_relevant_reply_without_a_pitch_is_kept() -> None:
    reply = "If the step count is already in Apple Health, typing that walk in again is the part people drop."
    assert reply_problems(reply, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS) == []


def test_skip_is_a_real_outcome() -> None:
    reason, reply = reply_outcome("SKIP: the post is a meme about a halftime show")
    assert reply == ""
    assert "halftime" in reason


def test_a_long_analysis_is_not_cut() -> None:
    marker = "UNRESOLVED CLAIM: calorie counting is not supported"
    analysis = "What it is\nA habit app.\nFeatures\n" + ("A" * 27000) + "\nLimitations\n" + marker
    brief = product_brief(analysis)
    assert marker in brief
    assert "A" * 27000 in brief
    message = reply_prompt(HEALTH, "https://x.com/a/status/1", name="VimVo", url="https://example.com", analysis=analysis)
    revised = revise_analysis_prompt("keep the ending", name="VimVo", url="https://example.com", previous=analysis)
    creators = creators_message("VimVo", "https://example.com", analysis)
    queries = search_queries_message("VimVo", "https://example.com", analysis)
    assert marker in message
    assert marker in revised
    assert marker in creators
    assert marker in queries


def test_compact_counts_parse() -> None:
    assert parse_count("1.2K") == 1200
    assert parse_count("3M") == 3_000_000
    assert parse_count("51,943") == 51943
    assert engagement_count("1.2K", 2) == 1200
    assert over_view_threshold("1.2K")
    assert not over_view_threshold("0.4K")
    rows = posts_from_reply("https://x.com/ada/status/10\n(1.2K views)\nhttps://x.com/ada/status/11\n(400 views)")
    assert [row["external_id"] for row in rows] == ["10"]
    assert rows[0]["views"] == 1200


def test_comment_metrics_are_not_added_together() -> None:
    notes = comment_notes(
        [
            {"text": "A view-heavy comment that says little about the walk.", "likes": 0, "replies": 0, "views": "9.5K"},
            {"text": "Ask whether the step goal was already on the phone.", "likes": 5, "replies": 1, "views": 12},
        ]
    )
    assert "0 likes" in notes
    assert "5 likes" in notes
    assert "Do not imitate" in notes
    assert "Do not add them into one score." in notes


MONDAY = "Every Monday I start a new routine. By Thursday I've already stopped."
MISSED = "I missed three workouts this week and I do not know whether to restart or double the next one."


def test_a_missed_workout_post_can_be_drafted() -> None:
    assert "500" in reply_gate(40, 0, MISSED, FACTS)
    assert reply_gate(900, 0, MISSED, FACTS) == ""
    assert conversation_rank(MISSED) > conversation_rank("A huge account posted a vague habit quote with no question.")
    assert conversation_rank(HEALTH) > conversation_rank(MISSED)


def test_generic_motivation_scores_zero_even_when_it_sounds_fluent() -> None:
    generic = "Thursday is when it falls apart. Consistency is more important than motivation. Keep going!"
    problems = reply_problems(generic, name="VimVo", url="https://example.com", post_text=MONDAY, facts=FACTS)
    scored = reply_quality(generic, name="VimVo", url="https://example.com", post_text=MONDAY, facts=FACTS)
    assert any("generic" in item for item in problems)
    assert scored["score"] == 0
    assert scored["skipped"] is True
    specific = "Monday-you keeps writing a plan that Thursday-you cannot finish, so Thursday needs a 5-minute version of the routine instead."
    assert reply_problems(specific, name="VimVo", url="https://example.com", post_text=MONDAY, facts=FACTS) == []
    kept = reply_quality(specific, name="VimVo", url="https://example.com", post_text=MONDAY, facts=FACTS)
    assert kept["skipped"] is False
    assert int(kept["score"]) >= 70


def test_a_repeated_generic_reply_is_rejected() -> None:
    advice = "Monday-you keeps writing a plan that Thursday-you cannot finish, so Thursday needs a 5-minute version of the routine instead."
    problems = reply_problems(advice, name="VimVo", url="https://example.com", post_text=MONDAY, facts=FACTS, recent=[advice])
    assert any("repeats" in item for item in problems)
    assert reply_quality(advice, name="VimVo", url="https://example.com", post_text=MONDAY, facts=FACTS, recent=[advice])["score"] == 0


def test_a_supported_product_answer_is_kept() -> None:
    reply = "Having to tick off a walk your phone already recorded is pretty redundant. VimVo supports automatic completion of eligible step and workout goals through Apple Health."
    assert reply_problems(reply, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS) == []
    scored = reply_quality(reply, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS)
    assert scored["skipped"] is False
    assert int(scored["score"]) >= 70


def test_a_french_question_is_not_dropped_for_being_quiet() -> None:
    french = "Est-ce que quelqu'un connaît une app qui reprend les pas d'Apple Health?"
    assert "500" in reply_gate(3, 0, french, FACTS)
    assert reply_gate(900, 0, french, FACTS) == ""
    assert "same language" in reply_prompt(french, "https://x.com/a/status/3", name="VimVo", url="https://example.com", analysis=FACTS)


NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
RECENT = "2026-11-16T12:00:00+00:00"
OLD = "2024-06-01T12:00:00+00:00"


def test_the_view_floor_stays_and_age_does_not_decide() -> None:
    recent = {"text": HEALTH, "views": 100, "created_at": RECENT, "external_id": "new-low", "handle": "ada"}
    assert choose_post([recent], facts=FACTS, now=NOW) is None
    recent_ok = {**recent, "views": 800, "external_id": "new-ok"}
    viral = {"text": VIRAL, "views": 50_000, "created_at": OLD, "external_id": "old-viral", "handle": "ada"}
    assert choose_post([recent_ok, viral], facts=FACTS, now=NOW)["external_id"] == "new-ok"
    assert choose_post([viral], facts=FACTS, now=NOW) is None
    assert reply_gate(50_000, 400, VIRAL, FACTS).startswith("Skip:")
    assert "500" in reply_gate(100, 0, HEALTH, FACTS)

    old_quiet = {"text": HEALTH, "views": 8000, "created_at": OLD, "external_id": "old-quiet", "handle": "ada"}
    assert choose_post([old_quiet], facts=FACTS, now=NOW)["external_id"] == "old-quiet"
    assert choose_post([old_quiet, recent_ok], facts=FACTS, now=NOW)["external_id"] == "new-ok"

    old_active = {
        "text": HEALTH,
        "views": 8000,
        "created_at": OLD,
        "external_id": "old-active",
        "handle": "ada",
        "latest_reply_at": "2026-11-15T08:00:00+00:00",
        "comments": [{"handle": "ada", "text": "Still looking for something that reads Apple Health.", "created_at": "2026-11-15T08:00:00+00:00"}],
    }
    newer_note = {
        "text": "Apple Health steps are easy to ignore after the first week.",
        "views": 900,
        "created_at": RECENT,
        "external_id": "new-note",
        "handle": "ada",
    }
    assert choose_post([newer_note, old_active], facts=FACTS, now=NOW)["external_id"] == "old-active"
    mixed = choose_post([old_quiet, newer_note], facts=FACTS, older_kept=3, recent_kept=0, now=NOW)
    assert mixed["external_id"] == "new-note"

    replied = {**recent_ok, "comments": [{"handle": "VimVo60", "text": "Already answered.", "created_at": RECENT}]}
    assert choose_post([replied], facts=FACTS, account_handle="VimVo60", now=NOW) is None
    pizza = "What is the best pizza in Naples?"
    assert reply_gate(900, 20, pizza, FACTS).startswith("Skip:")


def test_keyword_overlap_a_number_or_a_contraction_cannot_pass_alone() -> None:
    echo = "Does anyone know a habit tracker that automatically records steps from Apple Health?"
    scored = reply_quality(echo, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS)
    assert int(scored["score"]) < 70
    numbered = "Try 5 Apple Health steps."
    assert int(reply_quality(numbered, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS)["score"]) < 70
    contracted = "I've seen those Apple Health steps."
    assert int(reply_quality(contracted, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS)["score"]) < 70
    question = "Have you tried Apple Health steps?"
    assert any("question" in item for item in reply_problems(question, name="VimVo", url="https://example.com", post_text=HEALTH, facts=FACTS))
