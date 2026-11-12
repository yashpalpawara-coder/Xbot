"""The Grok chat used by Finder. The saved detail is pasted in, not rewritten."""

from __future__ import annotations

import re

_STATUS = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/(?P<handle>[A-Za-z0-9_]{1,30})/status/(?P<id>\d+)",
    re.I,
)
_COUNT = re.compile(r"([\d,]+)\s*(views|impressions)", re.I)


def product_message(name: str, url: str, detail: str) -> str:
    """The first Grok message. Details is the verified text, unchanged."""
    return (
        "This is my product. Emphasize these details and keep them for this chat.\n"
        f"Name: {name.strip()}\n"
        f"Product URL: {url.strip()}\n"
        f"Details: {detail.strip()}\n"
        "Say back what the product is, who it is for, and the situation it fits. "
        "Do not search for posts yet. Do not write a reply."
    )


def batch_count(value: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 20
    return min(100, max(1, count))


def find_message(count: int = 20) -> str:
    count = batch_count(count)
    return (
        "Using the product you already have in this chat, find related posts or audience posts of this product.\n"
        f"Return {count} posts.\n"
        "Give the URL of each post.\n"
        "Keep a post only when it shows more than 500 views or more than 500 impressions.\n"
        "Use posts you actually found on X."
    )


def more_message(count: int = 20) -> str:
    return f"{batch_count(count)} more"


def limit_reached(text: str) -> bool:
    lowered = text.lower()
    return "limit reached" in lowered or "try again later" in lowered or "upgrade to premium" in lowered


def posts_from_reply(text: str) -> list[dict[str, object]]:
    """Status URLs Grok actually wrote. Drop a post when its own count is 500 or under."""
    found: list[dict[str, object]] = []
    seen: set[str] = set()
    for match in _STATUS.finditer(text):
        status_id = match.group("id")
        if status_id in seen:
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        next_end = text.find("\n", line_end + 1) if line_end >= 0 else -1
        line = text[line_start : next_end if next_end >= 0 else len(text)]
        counts = [int(item.group(1).replace(",", "")) for item in _COUNT.finditer(line)]
        if counts and max(counts) <= 500:
            continue
        seen.add(status_id)
        found.append(
            {
                "external_id": status_id,
                "handle": match.group("handle"),
                "url": f"https://x.com/{match.group('handle')}/status/{status_id}",
                "note": " ".join(line.split())[:300],
                "views": max(counts) if counts else None,
            }
        )
    return found
