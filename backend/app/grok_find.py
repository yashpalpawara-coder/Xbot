"""The Grok chat used by Finder. The saved detail is pasted in, not rewritten."""

from __future__ import annotations

import re

from app.prompts import product_brief
from app.validation import parse_count

_STATUS = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/(?P<handle>[A-Za-z0-9_]{1,30})/status/(?P<id>\d+)",
    re.I,
)
_COUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?\s*[kmb]?)\s*(views|impressions)", re.I)


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
        "Using the product you already have in this chat, find posts where that audience is already talking.\n"
        "Search the content universe around the product: the problems, habits, and topics those people discuss. "
        "Do not search for the product name, a download, or an advertisement.\n"
        "Keep a post when a thoughtful reply could add one insight. Prefer a question or a specific situation. A high view count on its own is not a reason to choose a post. A smaller creator with the right audience is better than a huge unrelated post.\n"
        "Skip posts that are only a link, a giveaway, generic praise, or an unrelated trend.\n"
        f"Return {count} posts.\n"
        "Give the URL of each post.\n"
        f"Keep a post only when it shows more than {VIEW_MIN} views or more than {VIEW_MIN} impressions.\n"
        "Include recent conversations that already clear that line, and older conversations when people are still replying. Do not return only the oldest or the highest-view posts.\n"
        "Use posts you actually found on X."
    )


def more_message(count: int = 20) -> str:
    return f"{batch_count(count)} more"


VIEW_MIN = 500
CREATOR_COUNT = 10
QUERY_COUNT = 30
POSTS_PER_QUERY = 5
SEARCH_SCAN = 15
QUERY_PAUSE_SECONDS = 30

_FENCED = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_LINE_PREFIX = re.compile(r"^(?:[-*•]+|\d{1,3}[.)\]:-])\s*")
_SKIP_LINE = re.compile(
    r"^(?:here (?:are|is)|sure[,.!]?|these are|below are|search queries|i can |i'll |i will )\b",
    re.I,
)
_LIMIT_PHRASES = (
    "limit reached",
    "limit reach",
    "daily limit",
    "free tier limit",
    "reached your limit",
    "reached the limit",
    "rate limit",
    "try again later",
    "upgrade to premium",
)


LIMIT_NOTICE = "Free tier limit reached. Try again later or upgrade to Premium."


def limit_reached(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _LIMIT_PHRASES)


def limit_reply(reply: str, page_text: str) -> str:
    """The yellow limit banner is outside the reply. Keep the reply and record the banner."""
    if limit_reached(reply):
        return reply
    if limit_reached(page_text):
        return f"{reply}\n{LIMIT_NOTICE}".strip()
    return reply


def creators_message(name: str, url: str, analysis: str) -> str:
    """Creators whose audience already talks about this product's problems."""
    return (
        f"Using the full product analysis already in this chat, name {CREATOR_COUNT} X creators "
        "whose audience already discusses this product's problems.\n"
        f"Name: {name.strip()}\n"
        f"Product URL: {url.strip()}\n"
        f"Analysis:\n{product_brief(analysis)}\n"
        "Mix smaller and medium creators. Skip huge unrelated accounts.\n"
        f"Return exactly {CREATOR_COUNT} lines. One handle per line, without the @ sign.\n"
        "No numbering, no explanation, and no URL."
    )


def creators_from_reply(text: str, limit: int = CREATOR_COUNT) -> list[str]:
    """Handles only. Numbering, @ signs, and a code fence are ignored."""
    blocks = _FENCED.findall(text or "")
    body = "\n".join(blocks) if blocks else (text or "")
    found: list[str] = []
    seen: set[str] = set()
    for raw in body.replace("\r\n", "\n").split("\n"):
        line = _LINE_PREFIX.sub("", raw.strip().strip("`")).strip().strip("\"'")
        handle = line.split()[0].lstrip("@") if line else ""
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
            continue
        key = handle.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(handle)
        if len(found) >= limit:
            break
    return found


def over_view_threshold(views: object, minimum: int = VIEW_MIN) -> bool:
    """Same rule as Grok: a missing count stays, a count of 500 or under is dropped."""
    if views is None or views == "":
        return True
    count = parse_count(views)
    if count is None:
        return True
    return count > minimum


def search_queries_message(name: str, url: str, analysis: str) -> str:
    """Second ChatGPT message. The full analysis is already in the chat."""
    return (
        f"Using the full product analysis already in this chat, write {QUERY_COUNT} X search queries "
        "that find conversations this product can usefully join.\n"
        f"Name: {name.strip()}\n"
        f"Product URL: {url.strip()}\n"
        f"Analysis:\n{product_brief(analysis)}\n"
        "Search the content universe: the audience's problems, habits, and the words they already use. "
        "Do not search for the product name, the word download, or an advertisement.\n"
        "Each query should find posts where one thoughtful reply can add an insight. "
        "Include smaller creators whose audience is the right people, not only the biggest accounts.\n"
        "Mix queries that can match a conversation from this week with queries that can still find an older thread people reply to. "
        "Do not add since: or until:.\n"
        "Do not use unrelated trends, giveaways, or posts that are only a link.\n"
        f"Return exactly {QUERY_COUNT} lines. One search query per line.\n"
        "No numbering, no quotes, and no explanation.\n"
        "Do not repeat the analysis. Do not include a URL in a query."
    )


def queries_from_reply(text: str, limit: int = QUERY_COUNT) -> list[str]:
    """One query per line. Numbering, bullets, and a code fence are ignored."""
    blocks = _FENCED.findall(text or "")
    body = "\n".join(blocks) if blocks else (text or "")
    found: list[str] = []
    seen: set[str] = set()
    for raw in body.replace("\r\n", "\n").split("\n"):
        line = _LINE_PREFIX.sub("", raw.strip().strip("`")).strip().strip("\"'")
        line = " ".join(line.split())
        if not line or _SKIP_LINE.match(line):
            continue
        if len(line) < 3 or len(line) > 180 or (line.endswith(":") and len(line.split()) < 8):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(line)
        if len(found) >= limit:
            break
    return found


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
        counts = [count for count in (parse_count(item.group(1)) for item in _COUNT.finditer(line)) if count is not None]
        if counts and not over_view_threshold(max(counts)):
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
