from __future__ import annotations

import math
import re
from datetime import datetime, timezone

STOP = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "are",
    "was", "have", "has", "not", "but", "about", "just", "into", "how", "what",
    "when", "who", "why", "lang", "since", "until",
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _tokens(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9#@]{3,}", query.lower())
    return [word.lstrip("#@") for word in words if word.lstrip("#@") not in STOP]


def relevance_score(text: str, query: str) -> float:
    tokens = list(dict.fromkeys(_tokens(query)))
    if not tokens:
        return 0.5
    haystack = text.lower()
    hits = sum(1 for token in tokens if token in haystack)
    return hits / len(tokens)


def engagement_score(likes: int | None, replies: int | None, reposts: int | None) -> float:
    total = 0.0
    if likes:
        total += likes
    if replies:
        total += replies * 2
    if reposts:
        total += reposts * 1.5
    if total <= 0:
        return 0.0
    return _clamp(math.log10(total + 1) / 3)


def views_score(views: int | None) -> float:
    if not views:
        return 0.0
    return _clamp(math.log10(views + 1) / 6)


def recency_score(created_at: str | None, now: datetime | None = None) -> float:
    if not created_at:
        return 0.4
    try:
        stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.4
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_hours = max(0.0, (current - stamp).total_seconds() / 3600)
    return _clamp(1 - age_hours / (24 * 14))


def combined_score(
    text: str,
    query: str,
    *,
    likes: int | None,
    replies: int | None,
    reposts: int | None,
    views: int | None,
    created_at: str | None,
) -> float:
    relevance = relevance_score(text, query)
    engagement = engagement_score(likes, replies, reposts)
    views_part = views_score(views)
    recency = recency_score(created_at)
    value = 0.35 * relevance + 0.25 * engagement + 0.20 * views_part + 0.20 * recency
    return round(value * 100, 1)


def guess_language(text: str) -> str:
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
        return "ja"
    lowered = f" {text.lower()} "
    french = (" le ", " la ", " les ", " des ", " une ", " est ", " pas ", " pour ", " avec ")
    if sum(1 for word in french if word in lowered) >= 2:
        return "fr"
    if re.search(r"[a-z]", text, re.I):
        return "en"
    return ""
