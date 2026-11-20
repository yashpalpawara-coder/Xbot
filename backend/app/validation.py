from __future__ import annotations

import json
import re

URL_RE = re.compile(r"https?://\S+")
SPAM_PHRASES = (
    "check out my",
    "follow me",
    "link in bio",
    "dm me",
    "buy now",
    "limited offer",
)
UNSUPPORTED = (
    "studies show",
    "research proves",
    "guaranteed",
    "i personally",
    "as someone who",
    "i used this",
    "i built",
)


def weighted_length(text: str) -> int:
    urls = URL_RE.findall(text)
    remaining = URL_RE.sub("", text)
    return len(remaining) + (len(urls) * 23)


_COUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kmb])?", re.I)
_WORD = re.compile(r"[a-z0-9']{4,}")
_STOP = {
    "about", "after", "again", "also", "and", "any", "are", "because", "been", "before",
    "being", "but", "can", "could", "does", "doing", "from", "have", "here", "into",
    "just", "more", "only", "over",     "really", "should", "than", "that", "their", "them",
    "then", "there", "they", "this", "very", "what", "when", "where", "which", "with",
    "would", "your", "every", "make", "makes", "keep", "going", "thing", "things",
}


def parse_count(value: object) -> int | None:
    """Compact counts such as 1.2K and 3M. None when the text has no number."""
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    match = _COUNT.search(text)
    if match is None:
        return None
    number = float(match.group(1).replace(",", ""))
    scale = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[(match.group(2) or "").lower()]
    return int(number * scale)


def content_words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOP}


def shares_topic(left: str, right: str) -> bool:
    """True when the two texts share a word, including a short stem such as step and steps."""
    words = content_words(left)
    other = content_words(right)
    for word in words:
        for candidate in other:
            if word == candidate:
                return True
            if len(word) >= 4 and len(candidate) >= 4 and (word.startswith(candidate) or candidate.startswith(word)):
                return True
    return False


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The model response did not contain a JSON object.")
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("The model response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("The model response was not a JSON object.")
    return parsed


def comment_problems(text: str, *, product_name: str, product_url: str, allow_promotion: bool, post_text: str = "") -> list[str]:
    problems: list[str] = []
    body = " ".join(text.split())
    if not body:
        return ["Reply is empty."]
    if weighted_length(body) > 280:
        problems.append("Reply is over 280 characters.")
    if post_text.strip() and content_words(post_text) and not shares_topic(body, post_text):
        problems.append("Reply does not address the post.")
    lowered = body.lower()
    if any(phrase in lowered for phrase in SPAM_PHRASES):
        problems.append("Reply reads like a promotion or a solicitation.")
    if any(phrase in lowered for phrase in UNSUPPORTED):
        problems.append("Reply includes an unsupported personal or research claim.")
    if not allow_promotion:
        url = product_url.lower().rstrip("/")
        if url and url in lowered:
            problems.append("Reply includes the product link, and product mentions are off.")
        name = product_name.strip().lower()
        if name and re.search(rf"\b{re.escape(name)}\b", lowered):
            problems.append("Reply names the product, and product mentions are off.")
    return problems


def unique_comments(options: dict[str, str]) -> list[str]:
    seen: dict[str, str] = {}
    problems: list[str] = []
    for label, text in options.items():
        key = " ".join(text.lower().split())
        if not key:
            continue
        if key in seen:
            problems.append(f"{label} repeats {seen[key]}.")
        else:
            seen[key] = label
    return problems
