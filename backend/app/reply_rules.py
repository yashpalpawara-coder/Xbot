"""One reply for a post the owner already saved. Nothing here publishes it."""

from __future__ import annotations

import re

from app.validation import weighted_length

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
_GENERIC = ("great post", "nice work", "love this", "so true", "well said")
_URL = re.compile(r"https?://\S+")


def reply_prompt(post_text: str, post_url: str, *, name: str, url: str) -> str:
    """The comment message. The product writeup is already in this ChatGPT chat."""
    who = name.strip() or "the product"
    link = url.strip()
    return (
        f"Now analyze this X post: {post_url.strip()}\n"
        f"The post says: {post_text.strip()}\n"
        "Use the product details you already extracted in this same chat.\n"
        f"Write one reply that {who} can post under that same post.\n"
        f"You are {who}. The account is {who}, so speak as {who} in first person. \"I\" means {who}. "
        f"Never write as a user of {who}. Do not say \"I use {who}\", \"I've been using {who}\", "
        f"\"I found {who}\", or \"I love {who}\".\n"
        "Use a natural tone and mention one concrete detail from the post.\n"
        f"Say what {who} does, once, as {who} talking about itself.\n"
        "Use only the product in this chat. Do not invent a platform, a feature, or another product.\n"
        f"Use one emoji. End with this URL and put the URL nowhere else: {link}\n"
        "Put only the finished reply in one code block, so the copy box contains the reply and nothing else."
    )


def rewrite_reply_prompt(problem: str, *, name: str, url: str) -> str:
    who = name.strip() or "the product"
    return (
        "Write that reply again in the same chat. Do not explain it.\n"
        f"Still needed: {problem.strip()}\n"
        f"You are {who}. \"I\" means {who}. Never write as a user of {who}.\n"
        "Mention one concrete detail from the post. Say what you do, once.\n"
        "Use only the product in this chat. Do not invent a platform, a feature, or another product.\n"
        f"Use one emoji. End with this URL and put the URL nowhere else: {url.strip()}\n"
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
    if any(phrase in body.lower() for phrase in _GENERIC):
        problems.append("Reply is generic praise.")
    product = name.strip()
    if product and product.lower() not in body.lower():
        problems.append("Reply does not name the product.")
    if re.search(rf"\bi (?:use|used|found|love|am using|have been using|'ve been using|’ve been using) {re.escape(product)}\b", body, re.I):
        problems.append("Reply speaks as a user of the product.")
    link = url.strip().rstrip("/")
    if link and body.lower().count(link.lower()) != 1:
        problems.append("Reply must contain the product URL exactly once.")
    if not _EMOJI.search(body):
        problems.append("Reply has no emoji.")
    substance = _URL.sub("", body)
    substance = _EMOJI.sub("", substance)
    if len("".join(ch for ch in substance if ch.isalnum())) < 20:
        problems.append("Reply is too thin.")
    if weighted_length(body) > 260:
        problems.append("Reply is over 260 characters.")
    return problems
