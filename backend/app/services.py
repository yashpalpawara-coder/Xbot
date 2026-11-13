from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import ValidationError

from app.activity import collect_activity, load_activity, store_reply_draft
from app.browser_session import BrowserSession, SessionState
from app.errors import AppError
from app.grok_find import batch_count, find_message, limit_reached, more_message, posts_from_reply, product_message
from app.jobs import JobManager
from app.prompts import analysis_prompt, comments_prompt, regenerate_prompt
from app.reply_rules import reply_problems, reply_prompt, rewrite_reply_prompt, taken_reply
from app.writer import compose_draft, earlier_texts, mark_posted, prepare_details, public_state, revise_details, store_details, store_draft
from app.schemas import AnalysisBody, CommentBody, SearchIn, SettingsIn
from app.scoring import combined_score, guess_language
from app.store import Store
from app.validation import comment_problems, parse_json_object, unique_comments, weighted_length

DEFAULT_SETTINGS: dict[str, Any] = {
    "product_name": "Ted",
    "product_url": "https://tedcoachai.com",
    "product_pitch": "",
    "allow_promotion": False,
    "search_limit": 20,
    "min_views": None,
    "min_likes": None,
    "x_username": "",
}


def _public_settings(raw: dict[str, Any]) -> dict[str, Any]:
    public = {key: raw.get(key) for key in DEFAULT_SETTINGS}
    public["x_password_set"] = bool(str(raw.get("x_password") or ""))
    return public


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cancelled(job: dict[str, Any]) -> Callable[[], bool]:
    return job["cancel"].is_set


def _product_fields(name: str, url: str) -> tuple[str, str]:
    product_name = " ".join(name.split())
    product_url = url.strip()
    if not product_name:
        raise AppError("Enter the product name.", 400, "missing_name")
    if not product_url.startswith(("http://", "https://")):
        raise AppError("Enter a URL that starts with https://.", 400, "bad_url")
    return product_name, product_url


class Service:
    def __init__(self, store: Store, jobs: JobManager, settings: Any) -> None:
        self.store = store
        self.jobs = jobs
        self.settings = settings
        self._apply_x_login()

    def _raw_settings(self) -> dict[str, Any]:
        saved = None
        try:
            saved = self.store.get_settings()
        except Exception:
            saved = None
        merged = dict(DEFAULT_SETTINGS)
        merged["x_password"] = ""
        if isinstance(saved, dict):
            for key in (*DEFAULT_SETTINGS, "x_password"):
                if key in saved and saved[key] is not None:
                    merged[key] = saved[key]
        return merged

    def current_settings(self) -> dict[str, Any]:
        return _public_settings(self._raw_settings())

    def _apply_x_login(self) -> None:
        raw = self._raw_settings()
        username = str(raw.get("x_username") or "").strip()
        password = str(raw.get("x_password") or "")
        if username:
            self.settings.x_username = username
        if password:
            self.settings.x_password = password

    def save_settings(self, body: SettingsIn) -> dict[str, Any]:
        payload = body.model_dump()
        existing = self._raw_settings()
        password = str(payload.get("x_password") or "")
        payload["x_password"] = password or str(existing.get("x_password") or "")
        payload["x_username"] = str(payload.get("x_username") or "").strip()
        saved = self.store.save_settings(payload)
        self.store.log("settings", "saved", payload["product_name"])
        self._apply_x_login()
        return _public_settings(saved)

    def session_status(self) -> dict[str, Any]:
        accounts = {item["platform"]: item for item in self.store.accounts()}
        def row(platform: str) -> dict[str, Any]:
            item = accounts.get(platform)
            if not item:
                return {"platform": platform, "status": "not_checked", "handle": "", "detail": "Not checked yet."}
            return {
                "platform": platform,
                "status": item.get("session_status") or "not_checked",
                "handle": item.get("handle") or "",
                "detail": item.get("detail") or "",
                "checked_at": item.get("checked_at"),
            }
        return {"x": row("x"), "chatgpt": row("chatgpt"), "database": self.store.mode, "database_detail": self.store.detail}

    def check_x(self) -> dict[str, Any]:
        return self.jobs.start("x_check", lambda job: self._check_platform(job, "x"))

    def check_chatgpt(self) -> dict[str, Any]:
        return self.jobs.start("chatgpt_check", lambda job: self._check_platform(job, "chatgpt"))

    def _check_platform(self, job: dict[str, Any], platform: str) -> dict[str, Any]:
        self.jobs.log(
            job["id"],
            "Starting the browser on the server."
            if self.settings.headless
            else "Chrome is opening. If X asks for a code or extra check, finish it in that window. The window stays open.",
            progress=20,
        )
        def notice(message: str) -> None:
            self.jobs.log(job["id"], message, progress=55)

        with BrowserSession(self.settings) as session:
            state = session.ensure_x(_cancelled(job), notice) if platform == "x" else session.ensure_chatgpt(_cancelled(job))
        return self._remember(state)

    def _remember(self, state: SessionState) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "platform": state.platform,
            "handle": state.handle,
            "session_status": state.status,
            "detail": state.detail,
            "checked_at": _now(),
        }
        try:
            existing = next((item for item in self.store.accounts() if item["platform"] == state.platform), None)
            if existing:
                row["id"] = existing["id"]
        except Exception:
            pass
        self.store.set_account(row)
        self.store.log(f"session.{state.platform}", state.status, state.detail)
        if state.status != "signed_in":
            raise RuntimeError(state.detail)
        return {"session": {"platform": state.platform, "status": state.status, "handle": state.handle, "detail": state.detail}}

    def start_search(self, body: SearchIn) -> dict[str, Any]:
        query = self._query(body)
        if not query and not body.url.strip():
            raise AppError("Add a topic, keyword, handle, or post URL.", 422, "invalid")
        return self.jobs.start("finder_search", lambda job: self._search(job, body, query))

    def list_coming(self) -> list[dict[str, Any]]:
        return self.store.list_coming()

    def start_grok_find(self, count: int = 20) -> dict[str, Any]:
        product = self.latest_product()
        if not isinstance(product, dict) or not product.get("finder_verified"):
            raise AppError("Verify the product on Finder first.", 409, "not_verified")
        current = public_state(self._post_path()).get("details")
        shown = str(current.get("writeup") or "").strip() if isinstance(current, dict) else ""
        if shown and shown != str(product.get("detail") or "").strip():
            raise AppError("Verify the product on Finder first.", 409, "not_verified")
        count = batch_count(count)
        return self.jobs.start("grok_find", lambda job: self._grok_find(job, product, count))

    def _grok_find(self, job: dict[str, Any], product: dict[str, Any], count: int = 20) -> dict[str, Any]:
        seen = {str(row.get("external_id") or "") for row in self.store.list_coming()}
        added = 0
        with BrowserSession(self.settings) as session:
            self.jobs.log(job["id"], "Telling Grok the product. No search yet.", progress=15)
            session.ask_grok(
                product_message(str(product.get("name") or ""), str(product.get("url") or ""), str(product.get("detail") or "")),
                _cancelled(job),
            )
            if _cancelled(job)():
                return {"added": added}
            chat = session.grok_url
            self.jobs.log(job["id"], f"Asking Grok for {count} posts.", progress=30)
            reply = session.ask_grok(find_message(count), _cancelled(job), chat_url=chat, expect_posts=True)
            added += self._save_grok_posts(reply, seen)
            for round_index in range(7):
                if _cancelled(job)() or limit_reached(reply):
                    break
                self.jobs.log(job["id"], f"Asking Grok for {count} more.", progress=40 + round_index * 8)
                reply = session.ask_grok(more_message(count), _cancelled(job), chat_url=session.grok_url or chat, expect_posts=True)
                fresh = self._save_grok_posts(reply, seen)
                added += fresh
                if fresh == 0 or limit_reached(reply):
                    break
        self.jobs.update(job["id"], message=f"{added} posts added to the coming list.", progress=100)
        return {"added": added}

    def _save_grok_posts(self, reply: str, seen: set[str]) -> int:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows: list[dict[str, Any]] = []
        for item in posts_from_reply(reply):
            key = str(item.get("external_id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            post = self.store.upsert_post(
                {
                    "id": str(uuid.uuid4()),
                    "external_id": key,
                    "author": "",
                    "handle": str(item.get("handle") or ""),
                    "text": str(item.get("note") or ""),
                    "url": str(item.get("url") or ""),
                    "language": "",
                    "relevance": None,
                    "search_id": None,
                    "created_at": None,
                    "collected_at": now,
                    "latest_views": item.get("views"),
                    "latest_likes": None,
                    "latest_reposts": None,
                    "latest_replies": None,
                }
            )
            rows.append(
                {
                    **item,
                    "id": str(uuid.uuid4()),
                    "post_id": post["id"],
                    "status": "saved",
                    "reply": "",
                    "comment_id": None,
                    "added_at": now,
                }
            )
        return len(self.store.add_coming(rows))

    def start_coming_comments(self) -> dict[str, Any]:
        pending = [
            row
            for row in self.store.list_coming()
            if str(row.get("status") or "saved") in {"saved", "drafted"}
        ]
        if not pending:
            raise AppError("The coming list is empty.", 409, "empty")
        product = self.latest_product()
        if not isinstance(product, dict) or not str(product.get("detail") or "").strip():
            raise AppError("Verify the product on Finder first.", 409, "not_verified")
        saved = public_state(self._post_path()).get("details")
        chat_url = str(saved.get("chat_url") or "") if isinstance(saved, dict) else ""
        needs_write = any(not str(row.get("reply") or "").strip() for row in pending)
        if needs_write and not chat_url.startswith("https://chatgpt.com/c/"):
            raise AppError("Analyze the product first. Comments use that same ChatGPT chat.", 409, "missing_chat")
        return self.jobs.start("coming_comments", lambda job: self._coming_comments(job, pending, product))

    def _coming_comments(self, job: dict[str, Any], pending: list[dict[str, Any]], product: dict[str, Any]) -> dict[str, Any]:
        """Write a reply, post it, like that post, then wait 30 seconds."""
        name = str(product.get("name") or "")
        url = str(product.get("url") or "")
        saved = public_state(self._post_path()).get("details")
        chat_url = str(saved.get("chat_url") or "") if isinstance(saved, dict) else ""
        if not chat_url.startswith("https://chatgpt.com/c/"):
            raise RuntimeError("The product chat is missing. Analyze the product first, then comment in that same chat.")
        posted = 0
        done = {
            str(row.get("external_id") or "")
            for row in self.store.list_coming()
            if str(row.get("status") or "") in {"published", "uncertain", "posting"}
        }
        with BrowserSession(self.settings) as session:
            total = len(pending)
            for index, row in enumerate(pending):
                if _cancelled(job)():
                    break
                status_key = str(row.get("external_id") or row.get("url") or "")
                if status_key and status_key in done:
                    self.jobs.log(job["id"], "Already replied to this post. Skipping.")
                    continue
                if status_key:
                    done.add(status_key)
                self.jobs.log(job["id"], f"Replying {index + 1} of {total}.", progress=10 + int(80 * index / max(total, 1)))
                stored_post = self.store.get_post(str(row.get("post_id") or "")) if row.get("post_id") else None
                text = str((stored_post or {}).get("text") or "").strip()
                handle = str((stored_post or {}).get("handle") or row.get("handle") or "")
                opened = None
                if text.startswith("http") or len(text) < 12:
                    opened = session.read_status(str(row.get("url") or ""))
                    text = str((opened or {}).get("text") or "").strip()
                    handle = str((opened or {}).get("handle") or handle)
                if len(text) < 12:
                    self.store.update_coming(str(row.get("external_id") or ""), {"status": "skipped", "reply": "Too little text to reply to."})
                    continue
                if opened:
                    stored_post = self.store.upsert_post(
                        {
                            "id": str(row.get("post_id") or uuid.uuid4()),
                            "external_id": str(row.get("external_id") or opened.get("external_id") or ""),
                            "author": opened.get("author") or "",
                            "handle": handle,
                            "text": text[:500],
                            "url": str(row.get("url") or opened.get("url") or ""),
                            "language": guess_language(text),
                            "relevance": None,
                            "search_id": None,
                            "created_at": opened.get("created_at"),
                            "collected_at": _now(),
                            "latest_views": opened.get("views") if opened.get("views") is not None else row.get("views"),
                            "latest_likes": opened.get("likes"),
                            "latest_reposts": opened.get("reposts"),
                            "latest_replies": opened.get("replies"),
                        }
                    )
                if stored_post is None:
                    continue
                reply = str(row.get("reply") or "").strip() if str(row.get("status") or "") == "drafted" else ""
                if reply:
                    self.jobs.log(job["id"], f"Posting the saved reply for @{handle or 'post'}.", progress=20 + int(70 * index / max(total, 1)))
                else:
                    self.jobs.log(job["id"], f"Writing a reply for @{handle or 'post'}.", progress=20 + int(70 * index / max(total, 1)))
                comment_id = str(row.get("comment_id") or "")
                if not reply:
                    raw = self._complete(
                        session,
                        reply_prompt(text, str(row.get("url") or ""), name=name, url=url),
                        _cancelled(job),
                        chat_url,
                        2,
                    )
                    reply = taken_reply(raw)
                    problems = reply_problems(reply, name=name, url=url, post_text=text)
                    if problems:
                        raw = self._complete(
                            session,
                            rewrite_reply_prompt(" ".join(problems), name=name, url=url),
                            _cancelled(job),
                            chat_url,
                            2,
                        )
                        reply = taken_reply(raw)
                        problems = reply_problems(reply, name=name, url=url, post_text=text)
                    if problems:
                        self.store.update_coming(str(row.get("external_id") or ""), {"status": "skipped", "reply": " ".join(problems)})
                        continue
                    comment = self.store.add_comment(
                        {
                            "id": str(uuid.uuid4()),
                            "post_id": stored_post["id"],
                            "text": reply,
                            "variant": "coming",
                            "version": 1,
                            "status": "draft",
                            "idempotency_key": None,
                            "created_at": _now(),
                            "updated_at": _now(),
                        }
                    )
                    comment_id = comment["id"]
                    self.store.update_coming(
                        str(row.get("external_id") or ""),
                        {"status": "drafted", "reply": reply, "comment_id": comment_id, "post_id": stored_post["id"]},
                    )
                status_url = str(row.get("url") or stored_post.get("url") or "")
                self.store.update_coming(str(row.get("external_id") or ""), {"status": "posting", "reply": reply})
                self.jobs.log(job["id"], "Posting one reply, then liking that post.")
                try:
                    outcome = session.drop_reply(status_url, reply)
                except Exception as exc:
                    if comment_id:
                        self.store.update_comment(comment_id, {"status": "approved"})
                    self.store.update_coming(str(row.get("external_id") or ""), {"status": "drafted", "reply": reply})
                    detail = str(exc).split("Call log:", 1)[0].strip()
                    if "Timeout" in detail or len(detail) > 180:
                        detail = "The reply button was covered, so this post was skipped."
                    self.jobs.log(job["id"], detail or "This post was skipped.")
                    continue
                comment_status = "published" if outcome == "published" else "uncertain"
                if comment_id:
                    self.store.update_comment(comment_id, {"status": comment_status})
                self.store.update_coming(str(row.get("external_id") or ""), {"status": comment_status, "reply": reply})
                posted += 1
                self.jobs.log(job["id"], "Reply posted and the post was liked. Waiting 30 seconds.")
                for _ in range(30):
                    if _cancelled(job)():
                        break
                    time.sleep(1)
        self.jobs.update(job["id"], message=f"Posted {posted} replies.", progress=100, results_count=posted)
        return {"posted": posted}

    def _query(self, body: SearchIn) -> str:
        parts: list[str] = []
        if body.topic.strip():
            parts.append(body.topic.strip())
        parts.extend(part.strip() for part in body.keywords.split(",") if part.strip())
        for tag in body.hashtags.split(","):
            cleaned = tag.strip().lstrip("#")
            if cleaned:
                parts.append(f"#{cleaned}")
        for handle in body.handles.split(","):
            cleaned = handle.strip().lstrip("@")
            if cleaned:
                parts.append(f"from:{cleaned}")
        if body.since.strip():
            parts.append(f"since:{body.since.strip()}")
        if body.until.strip():
            parts.append(f"until:{body.until.strip()}")
        language = body.language.strip().lower()
        if language in {"en", "fr", "ja"}:
            parts.append(f"lang:{language}")
        return " ".join(parts)

    def _search(self, job: dict[str, Any], body: SearchIn, query: str) -> dict[str, Any]:
        prefs = self.current_settings()
        limit = min(body.limit, int(prefs.get("search_limit") or 20))
        search_id = str(uuid.uuid4())
        self.store.upsert_search({"id": search_id, "query": query or body.url.strip(), "filters": body.model_dump(), "created_at": _now()})
        self.jobs.log(job["id"], "Opening X.", progress=10)
        with BrowserSession(self.settings) as session:
            if body.url.strip():
                self.jobs.log(job["id"], "Opening the post.", progress=40)
                post, _discussion = session.open_post(body.url.strip())
                raw_posts = [post] if post else []
            else:
                self.jobs.log(job["id"], "Collecting posts.", progress=35)
                raw_posts = session.search_posts(query, limit, _cancelled(job))
        if _cancelled(job)():
            self.jobs.update(job["id"], status="stopped", message="Stopped")
        saved = []
        for raw in raw_posts:
            if not self._passes(raw, body):
                continue
            language = guess_language(raw.get("text") or "")
            if body.language.strip().lower() in {"en", "fr", "ja"} and language and language != body.language.strip().lower():
                continue
            score = combined_score(
                raw.get("text") or "",
                query,
                likes=raw.get("likes"),
                replies=raw.get("replies"),
                reposts=raw.get("reposts"),
                views=raw.get("views"),
                created_at=raw.get("created_at"),
            )
            row = {
                "id": str(uuid.uuid4()),
                "external_id": str(raw["external_id"]),
                "author": raw.get("author") or "",
                "handle": raw.get("handle") or "",
                "text": raw.get("text") or "",
                "url": raw.get("url") or "",
                "language": language,
                "relevance": score,
                "search_id": search_id,
                "created_at": raw.get("created_at"),
                "collected_at": _now(),
                "latest_views": raw.get("views"),
                "latest_likes": raw.get("likes"),
                "latest_reposts": raw.get("reposts"),
                "latest_replies": raw.get("replies"),
            }
            stored = self.store.upsert_post(row)
            self.store.add_metric(
                {
                    "id": str(uuid.uuid4()),
                    "post_id": stored["id"],
                    "views": raw.get("views"),
                    "likes": raw.get("likes"),
                    "reposts": raw.get("reposts"),
                    "replies": raw.get("replies"),
                    "collected_at": _now(),
                }
            )
            saved.append(stored)
        saved.sort(key=lambda item: self._sort_key(item, body.sort), reverse=True)
        self.store.log("finder", "done", f"{len(saved)} posts")
        self.jobs.update(job["id"], progress=90, results_count=len(saved), message=f"Collected {len(saved)} posts")
        return {"search_id": search_id, "query": query or body.url.strip(), "posts": saved}

    def _passes(self, raw: dict[str, Any], body: SearchIn) -> bool:
        if body.min_views is not None and (raw.get("views") is None or raw["views"] < body.min_views):
            return False
        if body.min_likes is not None and (raw.get("likes") is None or raw["likes"] < body.min_likes):
            return False
        if body.min_replies is not None and (raw.get("replies") is None or raw["replies"] < body.min_replies):
            return False
        return True

    def _sort_key(self, post: dict[str, Any], sort: str) -> Any:
        if sort == "likes":
            return post.get("latest_likes") or -1
        if sort == "views":
            return post.get("latest_views") or -1
        if sort == "recent":
            return post.get("created_at") or ""
        return post.get("relevance") or 0

    def list_posts(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_posts(limit)

    def post_detail(self, post_id: str) -> dict[str, Any]:
        post = self.store.get_post(post_id)
        if post is None:
            raise AppError("Post not found.", 404, "not_found")
        return {
            "post": post,
            "metrics": self.store.metrics_for(post_id),
            "analysis": self.store.latest_analysis(post_id),
            "comments": self.store.list_comments(post_id),
        }

    def start_analysis(self, post_id: str) -> dict[str, Any]:
        if self.store.get_post(post_id) is None:
            raise AppError("Post not found.", 404, "not_found")
        return self.jobs.start("analyse", lambda job: self._analyse(job, post_id))

    def _analyse(self, job: dict[str, Any], post_id: str) -> dict[str, Any]:
        post = self.store.get_post(post_id)
        if post is None:
            raise RuntimeError("Post not found.")
        self.jobs.log(job["id"], "Reading the post.", progress=20)
        with BrowserSession(self.settings) as session:
            _fresh, discussion = session.open_post(post["url"])
            self.jobs.log(job["id"], "Asking ChatGPT.", progress=55)
            raw = self._complete(session, analysis_prompt(post, discussion), _cancelled(job))
            try:
                payload = AnalysisBody.model_validate(parse_json_object(raw)).model_dump()
            except (ValueError, ValidationError):
                self.jobs.log(job["id"], "Retrying for valid JSON.", progress=75)
                raw = self._complete(
                    session,
                    "Return the previous analysis again as one JSON object with the requested keys and no markdown.",
                    _cancelled(job),
                )
                payload = AnalysisBody.model_validate(parse_json_object(raw)).model_dump()
        payload["discussion"] = discussion
        record = {
            "id": str(uuid.uuid4()),
            "post_id": post_id,
            "payload": payload,
            "raw_response": raw,
            "created_at": _now(),
        }
        stored = self.store.save_analysis(record)
        self.store.log("analysis", "saved", post_id)
        return {"analysis": stored}

    def start_comments(self, post_id: str, allow_promotion: bool | None) -> dict[str, Any]:
        if self.store.get_post(post_id) is None:
            raise AppError("Post not found.", 404, "not_found")
        return self.jobs.start("comments", lambda job: self._comments(job, post_id, allow_promotion))

    def _comments(self, job: dict[str, Any], post_id: str, allow_promotion: bool | None) -> dict[str, Any]:
        post = self.store.get_post(post_id)
        if post is None:
            raise RuntimeError("Post not found.")
        prefs = self.current_settings()
        allowed = prefs["allow_promotion"] if allow_promotion is None else allow_promotion
        analysis = self.store.latest_analysis(post_id)
        payload = analysis.get("payload") if analysis else None
        self.jobs.log(job["id"], "Writing three replies.", progress=30)
        with BrowserSession(self.settings) as session:
            raw = self._complete(
                session,
                comments_prompt(
                    post,
                    payload,
                    allow_promotion=allowed,
                    product_name=prefs["product_name"],
                    product_url=prefs["product_url"],
                    product_pitch=str(prefs.get("product_pitch") or ""),
                ),
                _cancelled(job),
            )
            try:
                parsed = CommentBody.model_validate(parse_json_object(raw))
            except (ValueError, ValidationError):
                raw = self._complete(session, "Return only the JSON object with the three replies.", _cancelled(job))
                parsed = CommentBody.model_validate(parse_json_object(raw))
        if not parsed.should_reply:
            self.store.log("comments", "skipped", parsed.reason)
            return {"comments": [], "reason": parsed.reason or "This post is not a good fit for a reply."}
        options = {"natural": parsed.natural, "insightful": parsed.insightful, "question": parsed.question}
        problems = unique_comments(options)
        saved = []
        for variant, text in options.items():
            issues = comment_problems(
                text,
                product_name=prefs["product_name"],
                product_url=prefs["product_url"],
                allow_promotion=allowed,
            )
            if issues:
                problems.extend(f"{variant}: {issue}" for issue in issues)
                continue
            saved.append(
                self.store.add_comment(
                    {
                        "id": str(uuid.uuid4()),
                        "post_id": post_id,
                        "text": " ".join(text.split()),
                        "variant": variant,
                        "version": 1,
                        "status": "draft",
                        "idempotency_key": None,
                        "created_at": _now(),
                        "updated_at": _now(),
                    }
                )
            )
        if not saved:
            raise RuntimeError("None of the replies passed validation. " + " ".join(problems))
        self.jobs.update(job["id"], results_count=len(saved), message=f"Saved {len(saved)} drafts")
        self.store.log("comments", "drafted", str(len(saved)))
        return {"comments": saved, "warnings": problems}

    def regenerate(self, comment_id: str) -> dict[str, Any]:
        comment = self.store.get_comment(comment_id)
        if comment is None:
            raise AppError("Comment not found.", 404, "not_found")
        return self.jobs.start("regenerate", lambda job: self._regenerate(job, comment))

    def _regenerate(self, job: dict[str, Any], comment: dict[str, Any]) -> dict[str, Any]:
        post = self.store.get_post(comment["post_id"])
        if post is None:
            raise RuntimeError("Post not found.")
        prefs = self.current_settings()
        allowed = bool(prefs["allow_promotion"])
        self.jobs.log(job["id"], "Rewriting one reply.", progress=40)
        with BrowserSession(self.settings) as session:
            raw = self._complete(
                session,
                regenerate_prompt(post, comment["variant"], comment["text"], allow_promotion=allowed),
                _cancelled(job),
            )
            parsed = parse_json_object(raw)
        text = " ".join(str(parsed.get(comment["variant"]) or "").split())
        issues = comment_problems(
            text,
            product_name=prefs["product_name"],
            product_url=prefs["product_url"],
            allow_promotion=allowed,
        )
        if issues:
            raise RuntimeError(" ".join(issues))
        previous = comment["text"]
        updated = self.store.update_comment(
            comment["id"],
            {"text": text, "version": int(comment.get("version") or 1) + 1, "status": "draft"},
        )
        self.store.log("comments", "regenerated", previous[:180])
        return {"comment": updated}

    def edit_comment(self, comment_id: str, text: str) -> dict[str, Any]:
        comment = self.store.get_comment(comment_id)
        if comment is None:
            raise AppError("Comment not found.", 404, "not_found")
        prefs = self.current_settings()
        cleaned = " ".join(text.split())
        if comment.get("variant") == "coming":
            post = self.store.get_post(comment["post_id"])
            issues = reply_problems(
                cleaned,
                name=prefs["product_name"],
                url=prefs["product_url"],
                post_text=str((post or {}).get("text") or ""),
            )
        else:
            issues = comment_problems(
                cleaned,
                product_name=prefs["product_name"],
                product_url=prefs["product_url"],
                allow_promotion=bool(prefs["allow_promotion"]),
            )
            if weighted_length(cleaned) > 280:
                issues.append("Reply is over 280 characters.")
        if issues:
            raise AppError(" ".join(issues), 422, "invalid")
        updated = self.store.update_comment(
            comment_id,
            {"text": cleaned, "version": int(comment.get("version") or 1) + 1, "status": "draft"},
        )
        self.store.log("comments", "edited", comment_id)
        return updated

    def approve(self, comment_id: str, text: str | None) -> dict[str, Any]:
        if text is not None:
            self.edit_comment(comment_id, text)
        comment = self.store.get_comment(comment_id)
        if comment is None:
            raise AppError("Comment not found.", 404, "not_found")
        if comment.get("status") == "published":
            raise AppError("This reply is already published.", 409, "published")
        updated = self.store.update_comment(comment_id, {"status": "approved"})
        for row in self.store.list_coming():
            if row.get("comment_id") == comment_id:
                self.store.update_coming(str(row.get("external_id") or ""), {"status": "approved", "reply": updated["text"]})
        self.store.log("comments", "approved", comment_id)
        return updated

    def start_publish(self, comment_id: str, idempotency_key: str) -> dict[str, Any]:
        with self.jobs._reserve:
            comment = self.store.get_comment(comment_id)
            if comment is None:
                raise AppError("Comment not found.", 404, "not_found")
            existing = self.store.find_event(idempotency_key)
            if existing and existing.get("status") == "published":
                return {"already": True, "event": existing, "comment": comment}
            if comment.get("status") == "published":
                raise AppError("This reply is already published.", 409, "published")
            if comment.get("status") == "publishing":
                raise AppError("This reply is already being published.", 409, "publishing")
            verify_only = bool(existing and (existing.get("status") == "uncertain" or existing.get("detail") == "submitted"))
            if comment.get("status") not in {"approved", "uncertain"}:
                raise AppError("Approve the reply before publishing.", 409, "not_approved")
            previous = comment["status"]
            self.store.update_comment(comment_id, {"status": "publishing", "idempotency_key": idempotency_key})
        try:
            return self.jobs.start(
                "publish",
                lambda job: self._publish(job, comment_id, idempotency_key, verify_only),
            )
        except AppError:
            self.store.update_comment(comment_id, {"status": previous})
            raise

    def _publish(self, job: dict[str, Any], comment_id: str, key: str, verify_only: bool) -> dict[str, Any]:
        comment = self.store.get_comment(comment_id)
        post = self.store.get_post(comment["post_id"]) if comment else None
        if comment is None or post is None:
            raise RuntimeError("The reply or its post is missing.")
        event = self.store.find_event(key)
        if event is None:
            event = self.store.add_event(
                {
                    "id": str(uuid.uuid4()),
                    "comment_id": comment_id,
                    "status": "sending",
                    "idempotency_key": key,
                    "detail": "",
                    "published_at": None,
                }
            )
        self.jobs.log(job["id"], "Checking the destination post." if verify_only else "Opening the post.", progress=30)
        with BrowserSession(self.settings) as session:
            if verify_only:
                seen = session.confirm_reply(post["url"], comment["text"])
                outcome = "published" if seen == "published" else "uncertain"
            else:
                event = self.store.update_event(event["id"], {"status": "sending", "detail": "submitted"})
                try:
                    outcome = session.reply(post["url"], comment["text"])
                except RuntimeError as exc:
                    self.store.update_event(event["id"], {"status": "failed", "detail": str(exc)})
                    self.store.update_comment(comment_id, {"status": "approved"})
                    self.store.log("publish", "failed", str(exc))
                    raise
        status = "published" if outcome == "published" else "uncertain"
        detail = "Reply is visible on the post." if status == "published" else "The reply was not confirmed. It was not sent again."
        updated_event = self.store.update_event(
            event["id"],
            {"status": status, "detail": detail, "published_at": _now() if status == "published" else None},
        )
        updated_comment = self.store.update_comment(comment_id, {"status": status})
        for row in self.store.list_coming():
            if row.get("comment_id") == comment_id:
                self.store.update_coming(str(row.get("external_id") or ""), {"status": status, "reply": comment["text"]})
        self.store.log("publish", status, comment_id)
        self.jobs.update(job["id"], message=detail, progress=100)
        return {"event": updated_event, "comment": updated_comment}

    def confirm_publish(self, comment_id: str) -> dict[str, Any]:
        comment = self.store.get_comment(comment_id)
        if comment is None:
            raise AppError("Comment not found.", 404, "not_found")
        if comment.get("status") not in {"uncertain", "publishing"}:
            raise AppError("Only an unconfirmed reply can be checked.", 409, "not_uncertain")
        return self.jobs.start("publish_check", lambda job: self._confirm(job, comment))

    def _confirm(self, job: dict[str, Any], comment: dict[str, Any]) -> dict[str, Any]:
        post = self.store.get_post(comment["post_id"])
        if post is None:
            raise RuntimeError("Post not found.")
        self.jobs.log(job["id"], "Checking the post for the reply.", progress=40)
        with BrowserSession(self.settings) as session:
            outcome = session.confirm_reply(post["url"], comment["text"])
        status = "published" if outcome == "published" else "uncertain"
        updated = self.store.update_comment(comment["id"], {"status": status})
        self.store.log("publish", status, "checked again")
        return {"comment": updated}

    def _complete(
        self,
        session: BrowserSession,
        prompt: str,
        cancelled: Callable[[], bool],
        chat_url: str = "",
        settle: int = 8,
    ) -> str:
        raw = session.ask_chatgpt(prompt, cancelled, chat_url=chat_url, settle=settle)
        self.store.log("chatgpt", "response", raw[:240])
        return raw

    def save_item(self, item_type: str, item_id: str, tags: list[str]) -> dict[str, Any]:
        if item_type == "post" and self.store.get_post(item_id) is None:
            raise AppError("Post not found.", 404, "not_found")
        row = {
            "id": str(uuid.uuid4()),
            "item_type": item_type,
            "item_id": item_id,
            "tags": tags,
            "created_at": _now(),
        }
        return self.store.add_saved(row)

    def saved_library(self) -> list[dict[str, Any]]:
        items = self.store.list_saved()
        posts = {post["id"]: post for post in self.store.list_posts(200)}
        for item in items:
            if item["item_type"] == "post":
                item["post"] = posts.get(item["item_id"])
        return items

    def overview(self) -> dict[str, Any]:
        return {"counts": self.store.counts(), "logs": self.store.list_logs(20), "database": self.store.mode}

    def activity_snapshot(self) -> dict[str, Any]:
        return load_activity(self._activity_path())

    def start_activity(self) -> dict[str, Any]:
        return self.jobs.start("activity", self._scan_activity)

    def start_activity_draft(self, response_id: str) -> dict[str, Any]:
        snapshot = self.activity_snapshot()
        row = next(
            (item for item in snapshot.get("responses") or [] if isinstance(item, dict) and str(item.get("id") or "") == response_id),
            None,
        )
        if row is None:
            raise AppError("That reply is not in the latest scan.", 404, "not_found")
        return self.jobs.start("activity_draft", lambda job: self._draft_activity(job, row))

    def _activity_path(self) -> Any:
        return self.settings.store_path.parent / "activity.json"

    def _post_path(self) -> Any:
        return self.settings.store_path.parent / "daily_post.json"

    def daily_post_state(self) -> dict[str, Any]:
        return public_state(self._post_path())

    def start_post_details(self, name: str, url: str) -> dict[str, Any]:
        product_name, product_url = _product_fields(name, url)
        return self.jobs.start("post_details", lambda job: self._post_details(job, product_name, product_url))

    def start_revise_details(self, change: str) -> dict[str, Any]:
        note = " ".join(change.split())
        if not note:
            raise AppError("Say what should change.", 400, "missing_input")
        details = public_state(self._post_path()).get("details")
        if not isinstance(details, dict) or not str(details.get("writeup") or "").strip():
            raise AppError("Analyze the product first.", 409, "missing_details")
        return self.jobs.start("post_details", lambda job: self._revise_details(job, note, details))

    def verify_product(self, name: str, url: str, detail: str, for_finder: bool = False) -> dict[str, Any]:
        product_name, product_url = _product_fields(name, url)
        text = detail.strip()
        if len(text) < 20:
            raise AppError("There is no analysis to verify.", 400, "missing_detail")
        current = public_state(self._post_path()).get("details")
        shown = str(current.get("writeup") or "").strip() if isinstance(current, dict) else ""
        if shown and text != shown:
            raise AppError("Verify the analysis that is on the page.", 409, "detail_mismatch")
        row = {
            "id": str(uuid.uuid4()),
            "name": product_name,
            "url": product_url,
            "detail": text,
            "finder_verified": for_finder,
            "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        self.store.save_product(row)
        prefs = self._raw_settings()
        prefs["product_name"] = product_name
        prefs["product_url"] = product_url
        prefs["product_pitch"] = text[:2000]
        self.store.save_settings(prefs)
        self._apply_x_login()
        saved = dict(current) if isinstance(current, dict) else {}
        saved.update(
            {
                "writeup": text,
                "summary": " ".join(text.split())[:500],
                "name": product_name,
                "url": product_url,
                "verified": True,
                "verified_at": row["verified_at"],
            }
        )
        store_details(self._post_path(), saved)
        return row

    def latest_product(self) -> dict[str, Any] | None:
        return self.store.latest_product()

    def start_daily_post(self, user_input: str) -> dict[str, Any]:
        note = " ".join(user_input.split())
        if not note:
            raise AppError("Type what the post should say.", 400, "missing_input")
        details = public_state(self._post_path()).get("details")
        if not isinstance(details, dict) or not details.get("verified"):
            raise AppError("Verify the product details first.", 409, "missing_details")
        return self.jobs.start("daily_post", lambda job: self._write_daily(job, note, details))

    def start_publish_daily(self, text: str) -> dict[str, Any]:
        return self.jobs.start("publish_post", lambda job: self._publish_daily(job, text))

    def _post_details(self, job: dict[str, Any], name: str, url: str) -> dict[str, Any]:
        snapshot = load_activity(self._activity_path())
        self.jobs.log(job["id"], "Typing the product into ChatGPT.", progress=30)
        with BrowserSession(self.settings) as session:
            details = prepare_details(
                snapshot,
                name=name,
                url=url,
                pitch="",
                complete=lambda prompt: self._complete(session, prompt, _cancelled(job)),
            )
            if session.chatgpt_url:
                details["chat_url"] = session.chatgpt_url
        store_details(self._post_path(), details)
        self.jobs.update(job["id"], message="Analysis ready. Change it, or verify it.", progress=100)
        return {"details": details}

    def _revise_details(self, job: dict[str, Any], change: str, details: dict[str, Any]) -> dict[str, Any]:
        self.jobs.log(job["id"], "Asking ChatGPT to rewrite the analysis.", progress=30)
        chat_url = str(details.get("chat_url") or "")
        with BrowserSession(self.settings) as session:
            revised = revise_details(
                name=str(details.get("name") or ""),
                url=str(details.get("url") or ""),
                previous=str(details.get("writeup") or ""),
                change=change,
                complete=lambda prompt: self._complete(session, prompt, _cancelled(job), chat_url),
            )
            if session.chatgpt_url:
                revised["chat_url"] = session.chatgpt_url
            elif chat_url:
                revised["chat_url"] = chat_url
        store_details(self._post_path(), revised)
        self.jobs.update(job["id"], message="Updated analysis ready. Change it, or verify it.", progress=100)
        return {"details": revised}

    def _write_daily(self, job: dict[str, Any], user_input: str, details: dict[str, Any]) -> dict[str, Any]:
        path = self._post_path()
        prefs = self.current_settings()
        snapshot = load_activity(self._activity_path())
        self.jobs.log(job["id"], "Typing your note into the same ChatGPT chat.", progress=40)
        chat_url = str(details.get("chat_url") or "")
        with BrowserSession(self.settings) as session:
            record = compose_draft(
                snapshot,
                name=str(prefs["product_name"]),
                url=str(prefs["product_url"]),
                pitch=str(prefs["product_pitch"]),
                earlier=earlier_texts(path, snapshot),
                user_input=user_input,
                details=details,
                complete=lambda prompt: self._complete(session, prompt, _cancelled(job), chat_url),
            )
        store_draft(path, record)
        self.jobs.update(job["id"], message="Draft saved. Nothing was posted.", progress=100)
        return record

    def _publish_daily(self, job: dict[str, Any], text: str) -> dict[str, Any]:
        path = self._post_path()
        pending = public_state(path).get("pending")
        if not isinstance(pending, dict) or not pending.get("text"):
            raise RuntimeError("There is no draft to approve.")
        if text.strip() != str(pending.get("text") or "").strip():
            raise RuntimeError("Only the drafted post can be approved.")
        if pending.get("posted"):
            self.jobs.log(job["id"], "That draft was already posted.")
            return pending
        self.jobs.log(job["id"], "Posting the approved draft.", progress=40)
        with BrowserSession(self.settings) as session:
            url = session.publish_post(text.strip())
        saved = mark_posted(path, text, url)
        self.jobs.update(job["id"], message="Posted.", progress=100)
        return saved

    def _scan_activity(self, job: dict[str, Any]) -> dict[str, Any]:
        path = self._activity_path()

        def log(message: str) -> None:
            self.jobs.log(job["id"], message)

        def progress(snapshot: dict[str, Any]) -> None:
            self.jobs.update(job["id"], result=snapshot, results_count=int(snapshot.get("replies_received") or 0))

        self.jobs.log(job["id"], "Opening X.", progress=10)
        with BrowserSession(self.settings) as session:
            return collect_activity(session, path, log, progress, _cancelled(job))

    def _draft_activity(self, job: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        prefs = self.current_settings()
        post = {
            "handle": row.get("handle") or "",
            "text": row.get("text") or "",
            "latest_views": row.get("views"),
            "latest_likes": row.get("likes"),
            "latest_replies": row.get("replies"),
            "latest_reposts": row.get("reposts"),
        }
        self.jobs.log(job["id"], "Drafting a reply. Nothing will be posted.", progress=40)
        with BrowserSession(self.settings) as session:
            raw = self._complete(
                session,
                comments_prompt(
                    post,
                    None,
                    allow_promotion=False,
                    product_name=prefs["product_name"],
                    product_url=prefs["product_url"],
                ),
                _cancelled(job),
            )
            parsed = CommentBody.model_validate(parse_json_object(raw))
        if not parsed.should_reply:
            draft = {"status": "skipped", "reason": parsed.reason or "This reply is not a good fit.", "text": "", "posted": False}
        else:
            text = " ".join((parsed.natural or parsed.insightful or parsed.question).split())
            issues = comment_problems(
                text,
                product_name=prefs["product_name"],
                product_url=prefs["product_url"],
                allow_promotion=False,
            )
            if issues or not text:
                draft = {"status": "skipped", "reason": " ".join(issues) or "No reply was kept.", "text": "", "posted": False}
            else:
                draft = {"status": "draft", "reason": "", "text": text, "posted": False}
        try:
            store_reply_draft(self._activity_path(), str(row.get("id") or ""), draft)
        except KeyError as exc:
            raise RuntimeError("That reply is no longer in the scan.") from exc
        self.jobs.update(job["id"], message="Draft saved. Nothing was posted.")
        return {"draft": draft, "activity": self.activity_snapshot()}

    def export(self) -> dict[str, Any]:
        data = self.store.export()
        if not isinstance(data, dict):
            return data
        copied = dict(data)
        settings = copied.get("settings")
        if isinstance(settings, dict):
            safe = dict(settings)
            safe.pop("x_password", None)
            copied["settings"] = safe
        return copied
