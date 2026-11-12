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


def comment_problems(text: str, *, product_name: str, product_url: str, allow_promotion: bool) -> list[str]:
    problems: list[str] = []
    body = " ".join(text.split())
    if not body:
        return ["Reply is empty."]
    if weighted_length(body) > 280:
        problems.append("Reply is over 280 characters.")
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
