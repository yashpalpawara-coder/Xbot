"""Account scan used by the analysis page. It reads posts and replies. It does not post."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeout

# Safety stop if X never ends the timeline. The scan otherwise keeps every post and reply.
SCAN_CAP = 2000
THREAD_LIMIT = 16
PER_THREAD = 20
RESPONSE_LIMIT = 80

CATEGORY_LABELS = {
    "interested": "Interested",
    "question": "Question",
    "praise": "Praise",
    "skeptical": "Skeptical",
    "objection": "Objection",
    "neutral": "Neutral",
    "negative": "Negative",
    "unrelated": "Unrelated",
}

ACTIVITY_JS = r"""
(() => {
  function parseCount(raw) {
    const text = String(raw || "").replace(/,/g, "");
    const match = text.match(/(\d+(?:\.\d+)?)\s*([KMB])?/i);
    if (!match) return 0;
    let value = Number(match[1]);
    const unit = (match[2] || "").toUpperCase();
    if (unit === "K") value *= 1000;
    if (unit === "M") value *= 1000000;
    if (unit === "B") value *= 1000000000;
    if (!Number.isFinite(value)) return 0;
    return Math.round(value);
  }
  function metric(article, testid) {
    const node = article.querySelector(`[data-testid="${testid}"]`);
    if (!node) return 0;
    const label = node.getAttribute("aria-label") || "";
    const fromLabel = parseCount(label);
    if (fromLabel) return fromLabel;
    return parseCount(node.innerText || "");
  }
  function views(article) {
    const analytics = article.querySelector('a[href*="/analytics"]');
    if (analytics) {
      const fromLabel = parseCount(analytics.getAttribute("aria-label") || "");
      if (fromLabel) return fromLabel;
      const fromText = parseCount(analytics.innerText || "");
      if (fromText) return fromText;
    }
    const labelled = article.querySelector('[aria-label*="views" i]');
    if (!labelled) return 0;
    return parseCount(labelled.getAttribute("aria-label") || "") || parseCount(labelled.innerText || "");
  }
  const posts = [];
  for (const article of document.querySelectorAll('article[data-testid="tweet"]')) {
    if (article.parentElement && article.parentElement.closest('article[data-testid="tweet"]')) continue;
    const text = (article.querySelector('[data-testid="tweetText"]')?.innerText || "").trim();
    const userBlock = article.querySelector('[data-testid="User-Name"]');
    const handleHref = userBlock?.querySelector('a[href^="/"]')?.getAttribute("href") || "";
    const handle = handleHref.split("/").filter(Boolean)[0] || "";
    const hrefs = [...article.querySelectorAll('a[href*="/status/"]')].map((node) => node.getAttribute("href") || "");
    const href = hrefs.find((value) => value.includes("/" + handle + "/status/")) || hrefs[0] || "";
    const head = (article.innerText || "").slice(0, 500);
    const cell = article.closest('[data-testid="cellInnerDiv"]');
    const prevHref = cell?.previousElementSibling?.querySelector('[data-testid="User-Name"] a[href^="/"]')?.getAttribute("href") || "";
    const prevHandle = prevHref.split("/").filter(Boolean)[0] || "";
    const replyMatch = head.match(/Replying to\s+@([A-Za-z0-9_]{1,15})/);
    const replyTo = replyMatch
      ? replyMatch[1]
      : (prevHandle && prevHandle.toLowerCase() !== handle.toLowerCase() ? prevHandle : "");
    const time = article.querySelector("time")?.getAttribute("datetime") || "";
    if (!text || !href || !handle) continue;
    posts.push({
      handle,
      text,
      href,
      reply_to: replyTo,
      created_at: time,
      replies: metric(article, "reply"),
      reposts: metric(article, "retweet"),
      likes: metric(article, "like"),
      views: views(article)
    });
  }
  return posts;
})()
"""

PROFILE_FACTS_JS = r"""
(() => {
  const column = document.querySelector('[data-testid="primaryColumn"]') || document.body;
  const text = column.innerText || "";
  const head = text.split(/Posts\s*\n\s*Replies/)[0] || text;
  const match = head.match(/([\d,]+)\s+posts/i);
  const posts = match ? Number(match[1].replace(/,/g, "")) : null;
  const repliesEmpty = /haven’t replied yet|haven't replied yet/i.test(text);
  return { posts: Number.isFinite(posts) ? posts : null, replies_empty: repliesEmpty };
})()
"""


def empty_activity() -> dict[str, Any]:
    return {
        "handle": "",
        "collected_at": "",
        "posts_created": 0,
        "comments_dropped": 0,
        "views": 0,
        "likes": 0,
        "replies_received": 0,
        "posts": [],
        "comments": [],
        "responses": [],
        "categories": [],
        "summary": "",
        "summary_en": "",
        "note": "",
        "stopped": False,
    }


def load_activity(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_activity()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return empty_activity()
    if not isinstance(data, dict):
        return empty_activity()
    return data


def save_activity(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    temporary.replace(path)


def cards_from_page(raw: object) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return cards
    for item in raw:
        if not isinstance(item, dict):
            continue
        card = normalize_card(item)
        if card is None or card["id"] in seen:
            continue
        seen.add(card["id"])
        cards.append(card)
    return cards


def normalize_card(item: dict[str, Any]) -> dict[str, Any] | None:
    href = str(item.get("href") or "")
    match = re.search(r"/status/(\d+)", href)
    text = " ".join(str(item.get("text") or "").split())
    handle = str(item.get("handle") or "").lstrip("@")
    if match is None or not text or not handle:
        return None
    path = href.split("?")[0]
    url = path if path.startswith("http") else "https://x.com" + path
    return {
        "id": match.group(1),
        "handle": handle,
        "text": text,
        "url": url,
        "reply_to": str(item.get("reply_to") or "").lstrip("@"),
        "created_at": str(item.get("created_at") or ""),
        "replies": _as_int(item.get("replies")),
        "reposts": _as_int(item.get("reposts")),
        "likes": _as_int(item.get("likes")),
        "views": _as_int(item.get("views")),
    }


def categorize_responses(responses: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    annotated: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in responses:
        category, why = classify_reply(str(row.get("text") or ""))
        updated = dict(row)
        updated["category"] = category
        updated["why"] = why
        annotated.append(updated)
        counts[category] = counts.get(category, 0) + 1
    categories = [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    parts = [f"{count} {CATEGORY_LABELS.get(name, name).lower()}" for name, count in counts.items()]
    summary = "Replies in this scan: " + ", ".join(parts) + "." if parts else ""
    return summary, categories, annotated


def classify_reply(text: str) -> tuple[str, str]:
    lowered = f" {text.lower()} "
    if any(phrase in lowered for phrase in ("stop replying", "leave me alone", "not interested", "don't reply", "do not reply")):
        return "objection", "The reply asks not to be contacted."
    if any(word in lowered for word in (" scam", " spam", " hate ", " annoying", " terrible", " worst ")):
        return "negative", "The reply is critical."
    if "?" in text:
        if any(word in lowered for word in (" really", " doubt", " not sure", " skeptical")):
            return "skeptical", "The reply questions the claim."
        return "question", "The reply asks something."
    if any(word in lowered for word in (" doubt", " not sure", " skeptical", " unsure")):
        return "skeptical", "The reply is unsure."
    if any(word in lowered for word in (" love ", " great ", " thanks", " thank you", " nice ", " awesome", " helpful")):
        return "praise", "The reply is positive."
    if any(word in lowered for word in (" how do", " how can", " where ", " want ", " need ", " interested", " link")):
        return "interested", "The reply asks for more."
    if len(text.split()) < 3:
        return "unrelated", "The reply is too short to place."
    return "neutral", "The reply does not take a side."


def incoming_replies(cards: list[dict[str, Any]], focus_id: str, handle: str) -> list[dict[str, Any]]:
    index = next((place for place, card in enumerate(cards) if str(card.get("id") or "") == focus_id), None)
    later = cards if index is None else cards[index + 1 :]
    return [card for card in later if str(card.get("handle") or "").lower() != handle.lower()]


def totals(snapshot: dict[str, Any]) -> None:
    items = [*(snapshot.get("posts") or []), *(snapshot.get("comments") or [])]
    snapshot["views"] = sum(_as_int(item.get("views")) for item in items if isinstance(item, dict))
    snapshot["likes"] = sum(_as_int(item.get("likes")) for item in items if isinstance(item, dict))


def collect_activity(
    session: Any,
    path: Path,
    log: Callable[[str], None] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    write = log or (lambda _message: None)
    previous = load_activity(path)
    kept_drafts = {
        str(row.get("id") or ""): row.get("draft")
        for row in previous.get("responses") or []
        if isinstance(row, dict) and isinstance(row.get("draft"), dict)
    }
    snapshot = empty_activity()

    def publish() -> None:
        save_activity(path, snapshot)
        if progress is not None:
            progress(snapshot)

    page = session.open_x()
    if _halted(stop):
        return _stopped(snapshot, write, publish, "Stopped before the profile was read.")
    if not session.x_logged_in(page):
        page = session.sign_in_if_needed(page)
    handle = session.x_handle(page)
    if not handle:
        raise RuntimeError("Could not read your X handle. Sign in, then refresh activity.")
    snapshot["handle"] = handle
    snapshot["collected_at"] = _now()
    write(f"Reading every post and reply for @{handle}.")
    posts, comments, stated_posts, replies_empty = _gather_everything(session, page, handle, stop, write)
    snapshot["posts"] = posts
    snapshot["comments"] = comments
    snapshot["posts_created"] = len(posts)
    snapshot["comments_dropped"] = 0 if replies_empty and not comments else len(comments)
    totals(snapshot)
    publish()
    write(
        f"Counted {snapshot['posts_created']} posts and {snapshot['comments_dropped']} replies. "
        f"Views {snapshot['views']}. Likes {snapshot['likes']}."
    )
    if _halted(stop):
        return _stopped(snapshot, write, publish, "Stopped before replies were read.")
    responses = _read_responses(session, page, handle, posts, comments, stop, write)
    for row in responses:
        kept = kept_drafts.get(str(row.get("id") or ""))
        if isinstance(kept, dict):
            row["draft"] = kept
    summary, categories, annotated = categorize_responses(responses)
    snapshot["responses"] = annotated
    snapshot["categories"] = categories
    snapshot["replies_received"] = len(annotated)
    if annotated:
        snapshot["summary"] = summary
        write(summary[:220])
    elif snapshot["posts_created"] == 0 and snapshot["comments_dropped"] == 0:
        snapshot["summary"] = (
            "X shows no posts on this account, and no replies left yet. "
            "There is nothing to categorize until you post or reply."
        )
        write(str(snapshot["summary"]))
    else:
        snapshot["summary"] = "No replies showed up under the posts and comments in this scan."
        write(str(snapshot["summary"]))
    loaded = len(posts) + len(comments)
    short = isinstance(stated_posts, int) and stated_posts > loaded
    snapshot["note"] = (
        f"Views and likes add every post and every reply in this scan ({loaded}). "
        f"X still lists {stated_posts} on the profile, and the timeline stopped before that."
        if short
        else "Views and likes add every post and every reply X loaded on this profile."
    )
    snapshot["stopped"] = False
    snapshot["collected_at"] = _now()
    publish()
    write("Activity scan finished. Nothing was posted.")
    return snapshot


def store_reply_draft(path: Path, response_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    snapshot = load_activity(path)
    found = False
    for row in snapshot.get("responses") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == response_id:
            row["draft"] = draft
            found = True
            break
    if not found:
        raise KeyError(response_id)
    save_activity(path, snapshot)
    return draft


def _gather_everything(
    session: Any,
    page: Any,
    handle: str,
    stop: Callable[[], bool] | None,
    log: Callable[[str], None],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None, bool]:
    """Scroll posts and replies until both timelines stop, then keep each status once."""
    originals = _scroll_timeline(session, page, f"https://x.com/{handle}", handle, False, stop, log, "posts")
    mixed: list[dict[str, Any]] = []
    if not _halted(stop):
        mixed = _scroll_timeline(session, page, f"https://x.com/{handle}/with_replies", handle, None, stop, log, "replies")
    by_id: dict[str, dict[str, Any]] = {}
    for card in [*originals, *mixed]:
        by_id[str(card["id"])] = card
    posts = [card for card in by_id.values() if not card.get("reply_to")]
    replies = [card for card in by_id.values() if card.get("reply_to")]
    facts = _facts(page)
    stated = facts.get("posts")
    stated_posts = stated if isinstance(stated, int) else None
    return posts, replies, stated_posts, bool(facts.get("replies_empty"))


def _scroll_timeline(
    session: Any,
    page: Any,
    url: str,
    handle: str,
    replies_only: bool | None,
    stop: Callable[[], bool] | None,
    log: Callable[[str], None],
    label: str,
) -> list[dict[str, Any]]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except PlaywrightTimeout:
        return []
    if "/login" in (page.url or "") or "/i/flow/login" in (page.url or ""):
        raise RuntimeError("X signed out. Sign in again, then refresh activity.")
    page.wait_for_timeout(1600)
    session._dismiss_cookies(page)
    try:
        page.locator('article[data-testid="tweet"]').first.wait_for(state="visible", timeout=12_000)
    except PlaywrightTimeout:
        return []
    found: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    stale = 0
    while stale < 5 and len(order) < SCAN_CAP:
        if _halted(stop):
            break
        before = len(order)
        _take_cards(page, handle, replies_only, found, order)
        if len(order) == before:
            stale += 1
        else:
            stale = 0
            log(f"Reading {label}: {len(order)} so far.")
        if stale >= 5 or len(order) >= SCAN_CAP or _halted(stop):
            break
        page.mouse.wheel(0, 2400)
        page.evaluate(
            """() => {
              const column = document.querySelector('[data-testid="primaryColumn"]');
              if (column) column.scrollTop = column.scrollHeight;
              window.scrollBy(0, 2400);
            }"""
        )
        page.wait_for_timeout(1100)
    return [found[status_id] for status_id in order]


def _take_cards(
    page: Any,
    handle: str,
    replies_only: bool | None,
    found: dict[str, dict[str, Any]],
    order: list[str],
) -> None:
    for card in cards_from_page(page.evaluate(ACTIVITY_JS)):
        if str(card.get("handle") or "").lower() != handle.lower():
            continue
        is_reply = bool(card.get("reply_to"))
        if replies_only is not None and replies_only != is_reply:
            continue
        status_id = str(card["id"])
        if status_id in found:
            found[status_id] = card
            continue
        if len(order) >= SCAN_CAP:
            return
        found[status_id] = card
        order.append(status_id)


def _read_responses(
    session: Any,
    page: Any,
    handle: str,
    posts: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    stop: Callable[[], bool] | None,
    log: Callable[[str], None],
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        status_id = str(item.get("id") or "")
        if not status_id or status_id in seen:
            return
        seen.add(status_id)
        targets.append(item)

    for item in [*posts, *comments]:
        if _as_int(item.get("replies")) > 0:
            add(item)
    for item in posts[:6]:
        add(item)
    targets = targets[:THREAD_LIMIT]
    responses: list[dict[str, Any]] = []
    known: set[str] = set()
    for index, item in enumerate(targets, start=1):
        if _halted(stop) or len(responses) >= RESPONSE_LIMIT:
            break
        log(f"Reading replies {index}/{len(targets)}.")
        url = str(item.get("url") or "")
        if not url:
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            log(f"Could not open {url}.")
            continue
        if "/login" in (page.url or ""):
            raise RuntimeError("X signed out. Sign in again, then refresh activity.")
        page.wait_for_timeout(1200)
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(700)
        for card in incoming_replies(cards_from_page(page.evaluate(ACTIVITY_JS)), str(item.get("id") or ""), handle)[:PER_THREAD]:
            status_id = str(card.get("id") or "")
            if not status_id or status_id in known:
                continue
            known.add(status_id)
            reply = dict(card)
            reply["parent_id"] = item.get("id") or ""
            reply["parent_url"] = item.get("url") or ""
            reply["parent_text"] = str(item.get("text") or "")[:180]
            responses.append(reply)
            if len(responses) >= RESPONSE_LIMIT:
                break
    return responses


def _facts(page: Any) -> dict[str, Any]:
    try:
        raw = page.evaluate(PROFILE_FACTS_JS)
    except Exception:
        return {"posts": None, "replies_empty": False}
    if not isinstance(raw, dict):
        return {"posts": None, "replies_empty": False}
    posts = raw.get("posts")
    if isinstance(posts, float) and posts.is_integer():
        posts = int(posts)
    if isinstance(posts, bool) or not isinstance(posts, int):
        posts = None
    return {"posts": posts, "replies_empty": bool(raw.get("replies_empty"))}


def _stopped(snapshot: dict[str, Any], log: Callable[[str], None], publish: Callable[[], None], note: str) -> dict[str, Any]:
    snapshot["stopped"] = True
    snapshot["note"] = note
    snapshot["collected_at"] = _now()
    publish()
    log("Stopped.")
    return snapshot


def _halted(stop: Callable[[], bool] | None) -> bool:
    return bool(stop and stop())


def _as_int(value: object) -> int:
    try:
        return max(int(str(value or 0).replace(",", "")), 0)
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
