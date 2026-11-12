from app.scoring import combined_score, engagement_score, guess_language, relevance_score


def test_relevance_uses_query_words() -> None:
    assert relevance_score("AI agents are shipping this week", "AI agents") > 0.6
    assert relevance_score("A recipe for bread", "AI agents") == 0


def test_missing_metrics_do_not_score_as_high_reach() -> None:
    quiet = engagement_score(None, None, None)
    popular = engagement_score(2000, 40, 10)
    assert quiet == 0
    assert popular > quiet


def test_combined_score_is_normalized() -> None:
    score = combined_score(
        "AI agents for startups",
        "AI agents",
        likes=100,
        replies=10,
        reposts=4,
        views=15000,
        created_at="2026-11-06T09:00:00+00:00",
    )
    assert 0 <= score <= 100


def test_language_guess() -> None:
    assert guess_language("C'est pour les gens qui veulent une liste") == "fr"
    assert guess_language("今日のタスクが多すぎる") == "ja"
    assert guess_language("Three tasks before noon") == "en"
