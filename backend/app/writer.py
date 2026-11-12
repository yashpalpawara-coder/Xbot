"""Draft one original post from the saved scan. Nothing here publishes it."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from app.prompts import original_post_prompt, product_prompt, revise_analysis_prompt, rewrite_post_prompt
from app.validation import weighted_length

POST_LIMIT = 280
MIN_TAGS = 2
MAX_TAGS = 3
FALLBACK_TAGS = ["#macOS", "#productivity", "#buildinpublic", "#indiehackers", "#MacApps", "#focus", "#deepwork"]

_FENCE = re.compile(r"```[a-zA-Z0-9]*\n(.*?)```", re.S)
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_EMOJI_START = re.compile(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_URL = re.compile(r"https?://\S+")
_TAG = re.compile(r"#\w+")


def writing_style(text: str) -> str:
    """A short name for how a post is shaped, so view counts can be compared."""
    body = _TAG.sub("", _URL.sub("", text))
    body = " ".join(body.split())
    if not body:
        return "empty"
    if body.endswith("?"):
        shape = "a question"
    elif _EMOJI_START.match(body):
        shape = "an emoji first"
    elif body[0].isdigit():
        shape = "a number first"
    else:
        shape = "a plain statement"
    weight = weighted_length(body)
    if weight < 90:
        length = "short"
    elif weight < 180:
        length = "medium"
    else:
        length = "long"
    return f"{shape}, {length}"


def rank_styles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The writing shape whose posts have the highest median views."""
    grouped: dict[str, list[int]] = {}
    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        grouped.setdefault(writing_style(text), []).append(_views(row.get("views")))
    ranked: list[dict[str, Any]] = []
    for style, views in grouped.items():
        ordered = sorted(views)
        ranked.append(
            {
                "style": style,
                "posts": len(ordered),
                "median_views": ordered[len(ordered) // 2],
                "best_views": ordered[-1],
            }
        )
    ranked.sort(key=lambda item: (item["median_views"], item["best_views"], item["posts"]), reverse=True)
    return {"winner": str(ranked[0]["style"]) if ranked else "", "styles": ranked}


def preferred_style(own: dict[str, Any], related: dict[str, Any]) -> str:
    """Prefer the account's own high-view shape when those posts have views."""
    styles = own.get("styles") if isinstance(own.get("styles"), list) else []
    if own.get("winner") and any(int(row.get("best_views") or 0) > 0 for row in styles if isinstance(row, dict)):
        return str(own["winner"])
    return str(related.get("winner") or "")


def repeats_post(candidate: str, earlier: list[str]) -> bool:
    """True when the draft copies an earlier post closely enough to be the same post."""
    body = _body(candidate)
    if len(body) < 20:
        return True
    tokens = set(body.split())
    for item in earlier:
        other = _body(item)
        if not other:
            continue
        if body == other:
            return True
        shared = tokens & set(other.split())
        if shared and len(shared) / min(len(tokens), len(other.split())) >= 0.7:
            return True
    return False


def tags_from_posts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hashtags that showed up on the posts with the most views."""
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        views = _views(row.get("views"))
        for tag in _TAG.findall(str(row.get("text") or "")):
            slot = totals.setdefault(tag.lower(), {"tag": tag, "views": 0})
            slot["views"] = int(slot["views"]) + views
    ranked = sorted(totals.values(), key=lambda item: int(item["views"]), reverse=True)
    tags = [
        {"tag": str(item["tag"]), "why": f"Seen on posts with about {int(item['views'])} views.", "reach": "high"}
        for item in ranked[:8]
    ]
    for tag in FALLBACK_TAGS:
        if len(tags) >= MIN_TAGS:
            break
        if tag.lower() not in {str(item["tag"]).lower() for item in tags}:
            tags.append({"tag": tag, "why": "", "reach": "medium"})
    return tags


def finish(text: str, link: str, chosen: list[str], known: list[dict[str, Any]]) -> str:
    """Body, then hashtags, then one canonical URL on the final line."""
    text = _strip_code_label(text)
    body = _URL.sub("", text)
    body = _TAG.sub("", body)
    body = "\n".join(line.rstrip() for line in body.splitlines())
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not _EMOJI.search(body):
        body += " ✨"
    allowed = [str(row["tag"]) for row in known if row.get("tag")] or list(FALLBACK_TAGS)
    lowered = {tag.lower(): tag for tag in allowed}
    tags: list[str] = []
    for tag in chosen + _TAG.findall(text):
        canonical = lowered.get(tag.lower(), "")
        if canonical and canonical.lower() not in {item.lower() for item in tags}:
            tags.append(canonical)
        if len(tags) >= MAX_TAGS:
            break
    for tag in allowed + FALLBACK_TAGS:
        if len(tags) >= MIN_TAGS:
            break
        if tag.lower() not in {item.lower() for item in tags}:
            tags.append(tag)
    tail = f"\n{' '.join(tags)}\n{link}"
    room = POST_LIMIT - weighted_length(tail)
    if weighted_length(body) > room:
        body = _trim(body, room)
    if not _EMOJI.search(body):
        mark = " ✨"
        while weighted_length(body) + len(mark) > room and " " in body:
            body = body[: body.rstrip().rfind(" ")].rstrip(" ,;:-–—")
        body = body.rstrip() + mark
    return body + tail


def taken_comment(text: str) -> str:
    """The post inside the code block. ChatGPT's own labels stay outside it."""
    blocks = [block.strip() for block in _FENCE.findall(text) if block.strip()]
    if blocks:
        return _strip_code_label(blocks[-1])
    cleaned = text.strip()
    if "ChatGPT said:" in cleaned:
        cleaned = cleaned.split("ChatGPT said:")[-1].strip()
    return _strip_code_label(cleaned)


def post_problem(draft: str, url: str) -> str:
    """Why a ChatGPT reply is not a post yet. Empty means it can be finished."""
    text = draft.strip()
    if not text:
        return "the reply was empty"
    lowered = text.lower()
    if "do not write a social reply" in lowered or (
        "what it is" in lowered and "who it is for" in lowered and "how it works" in lowered
    ):
        return "that was a product analysis"
    if lowered.startswith(("here's", "here is", "sure,", "certainly")):
        return "that was wrapped in ChatGPT's labels"
    if url not in text:
        return "the product URL was missing"
    return ""


def post_note(user_input: str, snapshot: dict[str, Any], earlier: list[str]) -> str:
    """Optional note for the second message: the owner's words, the winning shape, and posts not to copy."""
    lines = []
    _own, _replies, tags, _style, used, _summary = _context(snapshot)
    if used:
        lines.append(f"A shape that has earned views here is {used}. Use it only when it still fits the note.")
    if earlier:
        lines.append("Do not copy these earlier posts: " + " | ".join(item[:80] for item in earlier[-5:]))
    if tags:
        lines.append("Hashtags to choose from: " + " ".join(str(row.get("tag") or "") for row in tags[:6]))
    if user_input.strip():
        lines.append(user_input.strip())
    return "\n".join(line for line in lines if line)


def is_preamble(text: str) -> bool:
    """ChatGPT's opening line. The product details come after it finishes reading."""
    body = " ".join(text.split())
    lowered = body.lower().replace("’", "'").replace("‘", "'")
    if len(body) > 800:
        return False
    return (
        "won't draft" in lowered
        or "will not draft" in lowered
        or lowered.startswith("i'll read")
        or lowered.startswith("i will read")
    )


def prepare_details(
    snapshot: dict[str, Any],
    *,
    name: str,
    url: str,
    pitch: str,
    complete: Callable[[str], str],
) -> dict[str, Any]:
    """First ChatGPT message. The product writeup stays in the chat. This does not write a post."""
    del snapshot
    asked = product_prompt(name, pitch, url)
    writeup = taken_comment(complete(asked)).strip()
    if len(writeup) < 20 or " ".join(writeup.split()) == " ".join(asked.split()) or is_preamble(writeup):
        raise RuntimeError("ChatGPT has not finished the product details.")
    return {
        "writeup": writeup[:8000],
        "summary": " ".join(writeup.split())[:500],
        "name": name.strip(),
        "url": url.strip(),
        "verified": False,
        "prepared_at": _now(),
    }


def revise_details(
    *,
    name: str,
    url: str,
    previous: str,
    change: str,
    complete: Callable[[str], str],
) -> dict[str, Any]:
    """Rewrite the full analysis from the owner's change note. This does not verify or publish it."""
    asked = revise_analysis_prompt(change, name=name, url=url, previous=previous)
    writeup = taken_comment(complete(asked)).strip()
    if len(writeup) < 20 or is_preamble(writeup):
        raise RuntimeError("ChatGPT has not finished the revised details.")
    return {
        "writeup": writeup[:8000],
        "summary": " ".join(writeup.split())[:500],
        "name": name.strip(),
        "url": url.strip(),
        "verified": False,
        "prepared_at": _now(),
    }


def compose_draft(
    snapshot: dict[str, Any],
    *,
    name: str,
    url: str,
    pitch: str,
    earlier: list[str],
    user_input: str,
    details: dict[str, Any],
    complete: Callable[[str], str],
) -> dict[str, Any]:
    """Second ChatGPT message in the same chat. One rewrite at most. Does not publish."""
    del pitch
    note_in = " ".join(user_input.split())
    if not note_in:
        raise RuntimeError("Type what the post should say first.")
    if not str(details.get("writeup") or details.get("summary") or details.get("angle") or "").strip():
        raise RuntimeError("Ask ChatGPT for the details first.")
    own, replies, tags, _style, used, summary = _context(snapshot)
    note = post_note(note_in, snapshot, earlier)
    raw = complete(original_post_prompt(name, url, note))
    text, problem = _accept_post(raw, url, tags)
    if problem:
        raw = complete(rewrite_post_prompt(name, url, problem))
        text, problem = _accept_post(raw, url, tags)
    elif repeats_post(text, earlier):
        raw = complete(rewrite_post_prompt(name, url, "it repeated an earlier post"))
        text, problem = _accept_post(raw, url, tags)
    if problem:
        raise RuntimeError(f"ChatGPT did not return a post: {problem}")
    if repeats_post(text, earlier):
        raise RuntimeError("The draft repeated an earlier post. Nothing was posted.")
    draft = {"hook": "", "why": summary, "best_time": "", "audience": ""}
    record = {
        "status": "draft",
        "posted": False,
        "posted_url": "",
        "date": date.today().isoformat(),
        "written_at": _now(),
        "text": text,
        "hashtags": _TAG.findall(text),
        "hook": draft.get("hook", ""),
        "why": draft.get("why", ""),
        "best_time": draft.get("best_time", ""),
        "audience": draft.get("audience", ""),
        "length": weighted_length(text),
        "intent_url": "https://x.com/intent/post?text=" + quote(text, safe=""),
        "input": note_in,
        "analysis": {
            "summary": summary,
            "used": used,
            "own_count": len(own),
            "reply_count": len(replies),
        },
    }
    return record


def public_state(path: Path) -> dict[str, Any]:
    saved = read_store(path)
    history = [row for row in saved.get("posts", []) if isinstance(row, dict)]
    latest = history[-1] if history else None
    pending = saved.get("pending") if isinstance(saved.get("pending"), dict) else None
    return {
        "post": latest,
        "is_today": bool(latest and latest.get("date") == date.today().isoformat()),
        "count": len(history),
        "pending": pending,
        "details": saved.get("details") if isinstance(saved.get("details"), dict) else None,
    }


def store_details(path: Path, details: dict[str, Any]) -> dict[str, Any]:
    saved = read_store(path)
    saved["details"] = details
    _write(path, saved)
    return details


def read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"posts": []}
    try:
        saved = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"posts": []}
    if not isinstance(saved, dict):
        return {"posts": []}
    saved.setdefault("posts", [])
    return saved


def store_draft(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    saved = read_store(path)
    history = [row for row in saved.get("posts", []) if isinstance(row, dict)]
    history.append({key: record.get(key) for key in ("date", "written_at", "text", "hashtags", "why", "length")})
    saved["posts"] = history[-60:]
    saved["pending"] = record
    _write(path, saved)
    return record


def earlier_texts(path: Path, snapshot: dict[str, Any]) -> list[str]:
    saved = read_store(path)
    texts = [str(row.get("text") or "") for row in saved.get("posts") or [] if isinstance(row, dict)]
    texts.extend(str(row.get("text") or "") for row in _rows(snapshot.get("posts")))
    return [text for text in texts if text.strip()]


def mark_posted(path: Path, text: str, url: str) -> dict[str, Any]:
    saved = read_store(path)
    pending = saved.get("pending") if isinstance(saved.get("pending"), dict) else None
    if pending is None:
        raise RuntimeError("There is no draft to approve.")
    saved_text = str(pending.get("text") or "")
    if text.strip() != saved_text.strip():
        raise RuntimeError("Only the drafted post can be approved.")
    if pending.get("posted"):
        return pending
    pending["posted"] = True
    pending["posted_url"] = url
    pending["posted_at"] = _now()
    pending["status"] = "posted"
    saved["pending"] = pending
    _write(path, saved)
    return pending


def _accept_post(raw: str, url: str, tags: list[dict[str, Any]]) -> tuple[str, str]:
    draft = taken_comment(raw)
    problem = post_problem(draft, url)
    if problem:
        return "", problem
    return finish(draft, url, _TAG.findall(draft), tags), ""


def _context(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str, str, str]:
    own = _rows(snapshot.get("posts"))
    replies = _rows(snapshot.get("comments"))
    own_rank = rank_styles(own)
    reply_rank = rank_styles(replies)
    used = preferred_style(own_rank, reply_rank)
    tags = tags_from_posts(own + replies)
    return own, replies, tags, _style_note(own_rank, reply_rank, used), used, _summary(own_rank, reply_rank, used)


def _style_note(own: dict[str, Any], replies: dict[str, Any], used: str) -> str:
    lines = [
        "Write one new post. It must not reuse the wording of any earlier post.",
        "Include one fitting emoji and end with the product URL.",
    ]
    if own.get("winner"):
        lines.append(f"On this account, the writing shape with the highest views is {own['winner']}.")
    if replies.get("winner"):
        lines.append(f"On this account's replies, the writing shape with the highest views is {replies['winner']}.")
    if used:
        lines.append(f"Use this shape: {used}. Do not copy any example.")
    return " ".join(lines)


def _summary(own: dict[str, Any], replies: dict[str, Any], used: str) -> str:
    parts: list[str] = []
    if own.get("winner"):
        best = own["styles"][0]
        parts.append(f"Your posts written as {best['style']} had the highest views, median {best['median_views']}.")
    else:
        parts.append("None of your saved posts had text to compare.")
    if replies.get("winner"):
        best = replies["styles"][0]
        parts.append(f"Your replies written as {best['style']} had the highest views, median {best['median_views']}.")
    if used:
        parts.append(f"The draft uses {used}.")
    return " ".join(parts)


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if text:
            rows.append({"text": text, "views": _views(item.get("views")), "url": str(item.get("url") or "")})
    return rows


def _strip_code_label(text: str) -> str:
    lines = text.splitlines()
    while lines and re.fullmatch(r"(plain text|text|markdown|code)", lines[0].strip(), re.I):
        lines.pop(0)
    return "\n".join(lines).strip()


def _trim(body: str, room: int) -> str:
    text = body
    while weighted_length(text) > room and " " in text:
        text = text[: text.rstrip().rfind(" ")].rstrip(" ,;:-–—")
    if text and not text.endswith(("…", ".", "!", "?")) and not _EMOJI.search(text[-2:]):
        text += "…"
    return text


def _body(text: str) -> str:
    body = _TAG.sub("", _URL.sub("", text.lower()))
    body = _EMOJI_START.sub("", body)
    return " ".join(body.split())


def _views(value: object) -> int:
    try:
        return max(int(str(value or 0).replace(",", "")), 0)
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write(path: Path, saved: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(saved, indent=2, ensure_ascii=False))
    temporary.replace(path)
