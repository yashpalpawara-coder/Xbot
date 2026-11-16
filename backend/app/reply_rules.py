"""One reply for a post the owner already saved. Nothing here publishes it."""

from __future__ import annotations

import re

from app.grok_find import over_view_threshold
from app.validation import weighted_length

MIN_COMMENTS = 5

_FENCE = re.compile(r"```[^\n`]*\n?([\s\S]*?)```", re.I)
_HASHTAG = re.compile(r"(^|\s)#\w+")
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "\u231A\u231B"
    "\u23E9-\u23F3"
    "\u23F8-\u23FA"
    "\u2B05-\u2B55"
    "]"
)
_LABEL = re.compile(r"^(plain text|text|markdown|code)\b[:\s-]*", re.I)
_GENERIC = ("great post", "nice work", "love this", "so true", "well said", "amazing post")
_URL = re.compile(r"https?://\S+")
_PITCH = ("download ", "download now", "link in bio", "sign up", "check it out")
_STIFF = (
    "it's worth noting",
    "it is worth noting",
    "here's the thing",
    "here is the thing",
    "the real issue",
    "the key takeaway",
    "at the end of the day",
    "in conclusion",
    "hope this helps",
    "let's dive",
    "let's unpack",
    "game changer",
    "game-changer",
)
_NOT_JUST = re.compile(r"\bnot just\b.+\bbut\b", re.I)


def _count(value: object) -> int:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0


def engagement_count(metric: object, loaded: int = 0) -> int:
    """The post's reply count, or the number of comments actually loaded when that is higher."""
    return max(_count(metric), loaded)


def reply_gate(views: object, replies: object) -> str:
    """Empty when this post is worth a reply: more than 500 views and at least 5 comments."""
    if views is None or views == "" or not over_view_threshold(views):
        return "This post is under 500 views."
    if _count(replies) < MIN_COMMENTS:
        return "This post has fewer than 5 comments."
    return ""


def comment_notes(comments: list[dict]) -> str:
    """Every loaded comment, with the likes, replies, views, and length used to choose a style."""
    ranked = sorted(
        comments,
        key=lambda item: _count(item.get("likes")) + _count(item.get("replies")) + _count(item.get("views")),
        reverse=True,
    )
    lines = [
        "These are the comments already on that post. Study every one before you write.",
        "Find which kind of comment has high views, good likes, and good replies, and how long those comments are.",
        "Then write one reply in that same kind, so people are likely to like it.",
        "An emoji is optional. Use one only when the comments people liked use one.",
    ]
    shown = 0
    for item in ranked:
        body = " ".join(str(item.get("text") or "").split())
        if not body:
            continue
        shown += 1
        lines.append(
            f"- {_count(item.get('likes'))} likes, {_count(item.get('replies'))} replies, "
            f"{_count(item.get('views'))} views, {len(body)} characters: {body[:400]}"
        )
    if shown == 0:
        return ""
    return "\n".join(lines) + "\n"


def reply_prompt(post_text: str, post_url: str, *, name: str, url: str, analysis: str = "", comments: list[dict] | None = None) -> str:
    """The comment message. A saved analysis is pasted only when this product has no chat yet."""
    who = name.strip() or "the product"
    link = url.strip()
    saved = analysis.strip()
    if saved:
        context = (
            "Do not analyze the product again. The product analysis is already saved. Use it as the product details.\n"
            f"{saved[:6000]}\n"
        )
    else:
        context = "Do not analyze the product again. Use the product details you already extracted in this same chat.\n"
    notes = comment_notes(comments or [])
    return (
        f"Now analyze this X post: {post_url.strip()}\n"
        f"The post says: {post_text.strip()}\n"
        f"{notes}"
        f"{context}"
        f"Write one reply that {who} can post under that same post.\n"
        "Write it the way a person types a reply on X. Spoken, a little uneven, with contractions.\n"
        "One thought only. Tie it to something specific the author said. Do not turn it into a lesson or an advertisement.\n"
        "Do not use an em dash. Do not use phrases like \"it's worth noting\", \"here's the thing\", \"the real issue\", or \"not just X, but Y\".\n"
        "The reply must still be interesting if the account name is hidden.\n"
        "Talk about the problem the author is in, not a feature.\n"
        "Do not use generic praise such as \"Great post\" or \"Amazing\".\n"
        f"Mention {who} only when one saved fact answers the author's actual point. "
        f"If you mention {who}, you are {who}. \"I\" means {who}. "
        f"Never write as a user of {who}. Do not say \"I use {who}\", \"I've been using {who}\", "
        f"\"I found {who}\", or \"I love {who}\".\n"
        "Most replies should not mention the product at all.\n"
        f"Do not add a link, including {link}. Do not say download. Do not add a hashtag.\n"
        "Put only the finished reply in one code block, so the copy box contains the reply and nothing else."
    )


def rewrite_reply_prompt(problem: str, *, name: str, url: str) -> str:
    who = name.strip() or "the product"
    return (
        "Write that reply again in the same chat. Do not explain it.\n"
        f"Still needed: {problem.strip()}\n"
        f"You are {who}. \"I\" means {who}. Never write as a user of {who}.\n"
        "Write it the way a person types on X. One spoken thought, with contractions. No em dash and no essay phrases.\n"
        "An emoji is optional. Match the length and the kind of comment that already has likes on this post.\n"
        f"Mention {who} only when one saved fact answers the author's point. Most replies should not mention it.\n"
        f"Do not add a link, including {url.strip()}. Do not say download. Do not add a hashtag.\n"
        "Put only the finished reply in one code block."
    )


def taken_reply(raw: str) -> str:
    matches = _FENCE.findall(raw or "")
    text = matches[-1] if matches else (raw or "")
    text = _LABEL.sub("", text.strip())
    return " ".join(text.split())


def reply_problems(text: str, *, name: str, url: str, post_text: str = "") -> list[str]:
    body = " ".join((text or "").split())
    problems: list[str] = []
    if not body:
        return ["Reply is empty."]
    if _HASHTAG.search(body):
        problems.append("Reply has a hashtag.")
    lowered = body.lower()
    if any(phrase in lowered for phrase in _GENERIC):
        problems.append("Reply is generic praise.")
    if "—" in body or any(phrase in lowered for phrase in _STIFF) or _NOT_JUST.search(lowered):
        problems.append("Reply sounds written, not spoken.")
    if any(phrase in lowered for phrase in _PITCH) or _URL.search(body):
        problems.append("Reply is a download pitch.")
    product = name.strip()
    if product and re.search(
        rf"\bi (?:use|used|found|love|am using|have been using|'ve been using|’ve been using) {re.escape(product)}\b",
        body,
        re.I,
    ):
        problems.append("Reply speaks as a user of the product.")
    del url
    substance = _URL.sub("", body)
    substance = _EMOJI.sub("", substance)
    if len("".join(ch for ch in substance if ch.isalnum())) < 20:
        problems.append("Reply is too thin.")
    if weighted_length(body) > 260:
        problems.append("Reply is over 260 characters.")
    return problems
