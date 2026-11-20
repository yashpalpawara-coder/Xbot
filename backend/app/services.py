from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import ValidationError

from app.activity import collect_activity, load_activity, save_activity, store_reply_draft
from app.browser_session import BrowserSession, SessionState
from app.errors import AppError
from app.country import country_from_place
from app.grok_find import (
    CREATOR_COUNT,
    LIMIT_NOTICE,
    POSTS_PER_QUERY,
    QUERY_COUNT,
    QUERY_PAUSE_SECONDS,
    SEARCH_SCAN,
    VIEW_MIN,
    batch_count,
    creators_from_reply,
    creators_message,
    find_message,
    limit_reached,
    more_message,
    posts_from_reply,
    product_message,
    queries_from_reply,
    search_queries_message,
)
from app.jobs import JobManager
from app.prompts import analysis_prompt, comments_prompt, regenerate_prompt
from app.reply_rules import choose_post, engagement_count, post_age_band, published_time, replied_by, reply_gate, reply_outcome, reply_problems, reply_prompt, reply_quality, rewrite_reply_prompt
from app.writer import compose_draft, earlier_texts, mark_posted, prepare_details, public_state, revise_details, store_details, store_draft
from app.x_account import same_account
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
    "selected_product_id": "",
}


def _public_settings(raw: dict[str, Any]) -> dict[str, Any]:
    public = {key: raw.get(key) for key in DEFAULT_SETTINGS}
    public["x_password_set"] = bool(str(raw.get("x_password") or ""))
    return public


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cancelled(job: dict[str, Any]) -> Callable[[], bool]:
    return job["cancel"].is_set


def _query_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
        raw = self._raw_settings()
        product = self._selected_raw()
        public = _public_settings(raw)
        public["selected_product_id"] = str((product or {}).get("id") or raw.get("selected_product_id") or "")
        public["selected_product_name"] = str((product or {}).get("name") or "")
        if product is not None:
            public["x_username"] = str(product.get("x_username") or "")
            public["x_password_set"] = bool(str(product.get("x_password") or ""))
        return public

    def _apply_x_login(self) -> None:
        product = self._selected_raw()
        if product is not None and (str(product.get("x_username") or "").strip() or str(product.get("x_password") or "")):
            username = str(product.get("x_username") or "").strip()
            self.settings.x_username = username
            self.settings.x_password = str(product.get("x_password") or "")
            handle = str(product.get("x_handle") or "").strip().lstrip("@")
            if not handle and username and "@" not in username:
                handle = username.lstrip("@")
            self.settings.x_handle = handle
            return
        raw = self._raw_settings()
        username = str(raw.get("x_username") or "").strip()
        password = str(raw.get("x_password") or "")
        if username:
            self.settings.x_username = username
        if password:
            self.settings.x_password = password
        if username and "@" not in username:
            self.settings.x_handle = username.lstrip("@")

    def _selected_raw(self) -> dict[str, Any] | None:
        selected = str(self._raw_settings().get("selected_product_id") or "")
        if selected:
            row = self.store.get_product(selected)
            if row is not None:
                return row
        return self.store.latest_product()

    def _saved_analysis(self) -> str:
        product = self._selected_raw()
        if isinstance(product, dict) and str(product.get("detail") or "").strip():
            return str(product.get("detail") or "")
        return str(self.current_settings().get("product_pitch") or "")

    def _coming_rows(self, product: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(product, dict) or not product.get("id"):
            return []
        return self.store.list_coming(str(product["id"]))

    def _product_post_ids(self, product_id: str) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for row in self.store.list_coming(product_id):
            post_id = str(row.get("post_id") or "")
            if post_id and post_id not in seen:
                seen.add(post_id)
                ids.append(post_id)
        for post_id in self.store.post_ids_for(product_id):
            if post_id not in seen:
                seen.add(post_id)
                ids.append(post_id)
        return ids

    def _public_product(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        return {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "url": row.get("url") or "",
            "detail": row.get("detail") or "",
            "finder_verified": bool(row.get("finder_verified")),
            "verified_at": row.get("verified_at") or "",
            "x_username": row.get("x_username") or "",
            "x_handle": row.get("x_handle") or "",
            "x_password_set": bool(str(row.get("x_password") or "")),
            "chat_url": row.get("chat_url") or "",
            "search_queries": _query_list(row.get("search_queries")),
            "creators": _query_list(row.get("creators")),
        }

    def list_products(self) -> dict[str, Any]:
        selected = self._selected_raw()
        rows = self.store.list_products()
        rows.sort(key=lambda item: str(item.get("name") or "").casefold())
        return {
            "products": [self._public_product(row) for row in rows],
            "selected_id": str((selected or {}).get("id") or ""),
        }

    def select_product(self, product_id: str) -> dict[str, Any]:
        product = self.store.get_product(product_id)
        if product is None:
            raise AppError("That product is not saved.", 404, "not_found")
        raw = self._raw_settings()
        raw["selected_product_id"] = str(product["id"])
        raw["product_name"] = str(product.get("name") or raw.get("product_name") or "")
        raw["product_url"] = str(product.get("url") or raw.get("product_url") or "")
        detail = str(product.get("detail") or "")
        if detail:
            raw["product_pitch"] = detail[:2000]
        self.store.save_settings(raw)
        self._apply_x_login()
        self._show_product(product)
        fresh = self.store.get_product(str(product["id"])) or product
        return self.jobs.start("x_switch", lambda job: self._switch_account(job, fresh))

    def _show_product(self, product: dict[str, Any]) -> None:
        current = public_state(self._post_path()).get("details")
        saved = dict(current) if isinstance(current, dict) else {}
        text = str(product.get("detail") or "")
        saved.update(
            {
                "writeup": text,
                "summary": " ".join(text.split())[:500],
                "name": str(product.get("name") or ""),
                "url": str(product.get("url") or ""),
                "verified": bool(text),
                "chat_url": str(product.get("chat_url") or ""),
            }
        )
        store_details(self._post_path(), saved)

    def _switch_account(self, job: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
        fresh = self.store.get_product(str(product.get("id") or "")) or product
        with BrowserSession(self.settings) as session:
            self._require_account(session, job, fresh)
        saved = self.store.get_product(str(fresh.get("id") or "")) or fresh
        handle = str(saved.get("x_handle") or "").lstrip("@")
        name = str(saved.get("name") or "this product")
        message = f"Signed in as @{handle} for {name}." if handle else f"Signed in for {name}."
        self.jobs.update(job["id"], message=message, progress=100)
        return {"product": self._public_product(saved)}

    def _require_account(self, session: BrowserSession, job: dict[str, Any], product: dict[str, Any]) -> None:
        """Use the selected product's X login. An open session for another account is signed out first."""
        username = str(product.get("x_username") or "").strip()
        password = str(product.get("x_password") or "")
        handle = str(product.get("x_handle") or "")
        self.settings.x_username = username
        self.settings.x_password = password
        self.settings.x_handle = handle.strip().lstrip("@") or (username if username and "@" not in username else "")

        def notice(message: str) -> None:
            self.jobs.log(job["id"], message, progress=15)

        state = session.use_x_account(username, password, handle, _cancelled(job), notice)
        if state.status != "signed_in":
            raise RuntimeError(state.detail)
        if state.handle:
            current = self.store.get_product(str(product.get("id") or "")) or product
            if str(current.get("x_handle") or "") != state.handle:
                updated = dict(current)
                updated["x_handle"] = state.handle
                self.store.save_product(updated)
                product["x_handle"] = state.handle
        self._remember(state)

    def _keep_chat(self, session: BrowserSession, product: dict[str, Any], chat_url: str, fresh_chat: bool) -> tuple[str, bool]:
        fresh = str(session.chatgpt_url or "")
        if fresh.startswith("https://chatgpt.com/c/") and fresh != chat_url:
            chat_url = fresh
            fresh_chat = False
            updated = dict(self.store.get_product(str(product.get("id") or "")) or product)
            updated["chat_url"] = chat_url
            self.store.save_product(updated)
            product["chat_url"] = chat_url
        elif chat_url.startswith("https://chatgpt.com/c/"):
            fresh_chat = False
        return chat_url, fresh_chat

    def save_settings(self, body: SettingsIn) -> dict[str, Any]:
        payload = body.model_dump()
        existing = self._raw_settings()
        typed = str(payload.get("x_password") or "")
        payload["x_username"] = str(payload.get("x_username") or "").strip()
        name = " ".join(str(payload.get("product_name") or "").split())
        url = str(payload.get("product_url") or "").strip()
        payload["product_name"] = name
        payload["product_url"] = url
        named = self.store.find_product(name, "") if name else None
        if named is not None and " ".join(str(named.get("name") or "").split()).casefold() != name.casefold():
            named = None
        product = dict(named or {})
        if name:
            password = typed or str(product.get("x_password") or "")
            if not product.get("id"):
                password = typed
            username = payload["x_username"] or str(product.get("x_username") or "")
            handle = str(product.get("x_handle") or "")
            if username and "@" not in username:
                handle = username.lstrip("@")
            row = {
                "id": str(product.get("id") or uuid.uuid4()),
                "name": name,
                "url": url or str(product.get("url") or ""),
                "detail": str(product.get("detail") or ""),
                "finder_verified": bool(product.get("finder_verified")),
                "verified_at": str(product.get("verified_at") or ""),
                "x_username": username,
                "x_password": password,
                "x_handle": handle,
                "chat_url": str(product.get("chat_url") or ""),
                "search_queries": _query_list(product.get("search_queries")),
                "creators": _query_list(product.get("creators")),
            }
            self.store.save_product(row)
            payload["selected_product_id"] = row["id"]
            payload["x_username"] = username
            payload["x_password"] = password
        else:
            payload["selected_product_id"] = str(existing.get("selected_product_id") or "")
            payload["x_password"] = typed or str(existing.get("x_password") or "")
        self.store.save_settings(payload)
        self.store.log("settings", "saved", name)
        self._apply_x_login()
        return self.current_settings()

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
            "Starting the browser on the server. The first time downloads Chromium and can take a few minutes."
            if os.environ.get("RENDER") == "true" or self.settings.headless
            else "Chrome is opening. If X asks for a code or extra check, finish it in that window. The window stays open.",
            progress=20,
        )
        def notice(message: str) -> None:
            self.jobs.log(job["id"], message, progress=55)

        with BrowserSession(self.settings) as session:
            if platform == "x":
                self._apply_x_login()
                state = session.use_x_account(
                    self.settings.x_username,
                    self.settings.x_password,
                    self.settings.x_handle,
                    _cancelled(job),
                    notice,
                )
            else:
                state = session.ensure_chatgpt(_cancelled(job))
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

    def list_coming(self) -> dict[str, Any]:
        product = self._selected_raw()
        posts = []
        for row in self._coming_rows(product):
            item = dict(row)
            item["country"] = str(item.get("country") or "").strip() or "Unknown"
            posts.append(item)
        return {
            "posts": posts,
            "product_id": str((product or {}).get("id") or ""),
            "product_name": str((product or {}).get("name") or ""),
        }

    def start_grok_find(self, count: int = 20) -> dict[str, Any]:
        product = self._selected_raw()
        if not isinstance(product, dict) or not product.get("finder_verified"):
            raise AppError("Verify the product on Finder first.", 409, "not_verified")
        if not str(product.get("detail") or "").strip():
            raise AppError("Verify the product on Finder first.", 409, "not_verified")
        count = batch_count(count)
        return self.jobs.start("grok_find", lambda job: self._grok_find(job, product, count))

    def _grok_find(self, job: dict[str, Any], product: dict[str, Any], count: int = 20) -> dict[str, Any]:
        seen = {str(row.get("external_id") or "") for row in self._coming_rows(product)}
        added = 0
        queries: list[str] = []
        creators: list[str] = []
        countries: dict[str, str] = {}
        with BrowserSession(self.settings) as session:
            self._require_account(session, job, product)
            self.jobs.log(job["id"], "Telling Grok the product. No search yet.", progress=15)
            intro, limited = self._grok_reply(
                session,
                job,
                product_message(str(product.get("name") or ""), str(product.get("url") or ""), str(product.get("detail") or "")),
            )
            if _cancelled(job)():
                return {"added": added, "queries": queries, "creators": creators}
            chat = session.grok_url
            reply = intro
            if not limited:
                self.jobs.log(job["id"], f"Asking Grok for {count} posts.", progress=30)
                reply, limited = self._grok_reply(session, job, find_message(count), chat_url=chat, expect_posts=True)
                added += self._save_grok_posts(reply, seen, str(product.get("id") or ""), session, job, countries)
                limited = limited or limit_reached(reply)
            for round_index in range(7):
                if _cancelled(job)() or limited:
                    break
                self.jobs.log(job["id"], f"Asking Grok for {count} more.", progress=40 + round_index * 8)
                reply, hit_limit = self._grok_reply(
                    session,
                    job,
                    more_message(count),
                    chat_url=session.grok_url or chat,
                    expect_posts=True,
                )
                fresh = self._save_grok_posts(reply, seen, str(product.get("id") or ""), session, job, countries)
                added += fresh
                if hit_limit or limit_reached(reply):
                    limited = True
                    break
                if fresh == 0:
                    break
            if limited and not _cancelled(job)():
                found, queries, creators = self._find_with_search_queries(session, job, product, seen, countries)
                added += found
        message = f"{added} posts added to the coming list."
        if queries:
            message = f"{added} posts added from {len(queries)} searches and {len(creators)} creators."
        self.jobs.update(job["id"], message=message, progress=100, results_count=added)
        return {"added": added, "queries": queries, "creators": creators}

    def _find_with_search_queries(
        self,
        session: BrowserSession,
        job: dict[str, Any],
        product: dict[str, Any],
        seen: set[str],
        countries: dict[str, str] | None = None,
    ) -> tuple[int, list[str], list[str]]:
        """Grok is out of searches. ChatGPT analyses the product, then writes the queries."""
        self.jobs.log(job["id"], "Grok limit reached. Asking ChatGPT for the full product analysis.", progress=70)
        name = str(product.get("name") or "")
        url = str(product.get("url") or "")
        detail = str(product.get("detail") or "")
        chat_url = str(product.get("chat_url") or "")
        if not chat_url.startswith("https://chatgpt.com/c/"):
            saved = public_state(self._post_path()).get("details")
            if isinstance(saved, dict) and str(saved.get("name") or "").strip().casefold() == name.casefold():
                maybe = str(saved.get("chat_url") or "")
                if maybe.startswith("https://chatgpt.com/c/"):
                    chat_url = maybe
        fresh_chat = not chat_url.startswith("https://chatgpt.com/c/")

        def complete(prompt: str) -> str:
            nonlocal chat_url, fresh_chat
            raw_reply = self._complete(session, prompt, _cancelled(job), "" if fresh_chat else chat_url, 8)
            chat_url, fresh_chat = self._keep_chat(session, product, chat_url, fresh_chat)
            return raw_reply

        analysis = prepare_details({}, name=name, url=url, pitch=detail, complete=complete)
        writeup = str(analysis.get("writeup") or detail)
        self.jobs.log(job["id"], f"Asking ChatGPT for {CREATOR_COUNT} creators.", progress=72)
        creators = creators_from_reply(complete(creators_message(name, url, writeup)), CREATOR_COUNT)
        self.jobs.log(job["id"], f"Asking ChatGPT for {QUERY_COUNT} search queries.", progress=74)
        raw = complete(search_queries_message(name, url, writeup))
        queries = queries_from_reply(raw, QUERY_COUNT)
        if not queries and not creators:
            self.jobs.log(job["id"], "ChatGPT did not return search queries.")
            return 0, [], []
        self._remember_queries(product, queries, creators)
        self.jobs.log(job["id"], f"Saved {len(creators)} creators and {len(queries)} search queries.", progress=75)
        targets = [(query, "query") for query in queries]
        targets.extend((f"from:{handle}", "creator") for handle in creators)
        added = 0
        older_kept = 0
        recent_kept = 0
        product_id = str(product.get("id") or "")
        total = len(targets)
        for index, (query, kind) in enumerate(targets):
            if _cancelled(job)():
                break
            label = f"Query {index + 1} of {len(queries)}" if kind == "query" else f"Creator {index - len(queries) + 1} of {len(creators)}"
            self.jobs.log(job["id"], f"{label}: {query}", progress=75 + int(20 * index / max(total, 1)))
            search_id = str(uuid.uuid4())
            self.store.upsert_search(
                {
                    "id": search_id,
                    "query": query,
                    "filters": {"topic": query, "limit": POSTS_PER_QUERY, "source": kind, "min_views": VIEW_MIN},
                    "created_at": _now(),
                }
            )
            try:
                raw_posts = session.search_posts(query, SEARCH_SCAN, _cancelled(job))
            except RuntimeError as exc:
                self.jobs.log(job["id"], str(exc))
                raw_posts = []
            picked = choose_post(
                raw_posts,
                facts=detail,
                query=query,
                older_kept=older_kept,
                recent_kept=recent_kept,
                account_handle=str(product.get("x_handle") or ""),
            )
            kept = [picked] if picked else []
            if picked is not None:
                self._attach_country(session, job, picked, countries or {})
                band = post_age_band(picked)
                if band == "recent":
                    recent_kept += 1
                elif band == "older":
                    older_kept += 1
            fresh = self._save_search_posts(kept, seen, product_id, search_id)
            added += fresh
            self.jobs.log(job["id"], f"Added {fresh} post from this search.")
            if index < total - 1 and self._wait_before_next_query(job):
                break
        return added, queries, creators

    def _grok_reply(
        self,
        session: BrowserSession,
        job: dict[str, Any],
        prompt: str,
        chat_url: str = "",
        expect_posts: bool = False,
    ) -> tuple[str, bool]:
        """A Grok reply. The daily-limit banner, or a reply that never arrives, switches to ChatGPT."""
        try:
            reply = session.ask_grok(prompt, _cancelled(job), chat_url=chat_url, expect_posts=expect_posts)
        except RuntimeError as exc:
            text = str(exc)
            if limit_reached(text) or "did not finish a reply" in text.lower():
                self.jobs.log(job["id"], "Grok limit reached.")
                return LIMIT_NOTICE, True
            raise
        return reply, limit_reached(reply)

    def _remember_queries(self, product: dict[str, Any], queries: list[str], creators: list[str]) -> None:
        current = dict(self.store.get_product(str(product.get("id") or "")) or product)
        current["search_queries"] = queries
        current["creators"] = creators
        self.store.save_product(current)
        product["search_queries"] = queries
        product["creators"] = creators
        self.store.log("finder", "queries", "\n".join([*creators, *queries])[:4000])

    def _wait_before_next_query(self, job: dict[str, Any]) -> bool:
        """True when the job was stopped during the break between queries."""
        return self._pause(job, QUERY_PAUSE_SECONDS, "Waiting 30 seconds before the next search.")

    def _pause(self, job: dict[str, Any], seconds: int, message: str) -> bool:
        """True when Stop was used during the wait."""
        if seconds <= 0:
            return False
        self.jobs.log(job["id"], message)
        for _ in range(seconds):
            if _cancelled(job)():
                return True
            time.sleep(1)
        return _cancelled(job)()

    def _save_search_posts(self, raw_posts: list[dict[str, Any]], seen: set[str], product_id: str, search_id: str) -> int:
        now = _now()
        rows: list[dict[str, Any]] = []
        for raw in raw_posts:
            key = str(raw.get("external_id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            text = str(raw.get("text") or "")
            stored = self.store.upsert_post(
                {
                    "id": str(uuid.uuid4()),
                    "external_id": key,
                    "author": raw.get("author") or "",
                    "handle": raw.get("handle") or "",
                    "text": text,
                    "url": raw.get("url") or "",
                    "language": guess_language(text),
                    "relevance": None,
                    "search_id": search_id,
                    "created_at": published_time(key, raw.get("created_at")) or None,
                    "collected_at": now,
                    "latest_views": raw.get("views"),
                    "latest_likes": raw.get("likes"),
                    "latest_reposts": raw.get("reposts"),
                    "latest_replies": raw.get("replies"),
                }
            )
            if product_id:
                self.store.remember_post(str(stored.get("id") or ""), product_id)
            self.store.add_metric(
                {
                    "id": str(uuid.uuid4()),
                    "post_id": stored["id"],
                    "views": raw.get("views"),
                    "likes": raw.get("likes"),
                    "reposts": raw.get("reposts"),
                    "replies": raw.get("replies"),
                    "collected_at": now,
                }
            )
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "external_id": key,
                    "handle": str(raw.get("handle") or ""),
                    "url": str(raw.get("url") or ""),
                    "note": " ".join(text.split())[:300],
                    "views": raw.get("views"),
                    "post_id": stored["id"],
                    "status": "saved",
                    "reply": "",
                    "comment_id": None,
                    "country": str(raw.get("country") or "Unknown"),
                    "product_id": product_id,
                    "added_at": now,
                }
            )
        return len(self.store.add_coming(rows))

    def _attach_country(
        self,
        session: BrowserSession,
        job: dict[str, Any],
        post: dict[str, Any],
        countries: dict[str, str],
    ) -> None:
        """Store a country on the post. The same handle is read once during this find."""
        place = country_from_place(str(post.get("place") or ""))
        if place:
            post["country"] = place
            return
        handle = str(post.get("handle") or "").strip().lstrip("@")
        if handle and handle.casefold() in countries:
            post["country"] = countries[handle.casefold()]
            return
        country = "Unknown"
        reader = getattr(session, "account_country", None)
        if handle and reader is not None and not _cancelled(job)():
            if self._pause(job, QUERY_PAUSE_SECONDS, "Waiting 30 seconds before the account check."):
                post["country"] = "Unknown"
                return
            self.jobs.log(job["id"], f"Reading the country for @{handle}.")
            try:
                country = reader(handle, _cancelled(job)) or "Unknown"
            except Exception:
                country = "Unknown"
        if handle:
            countries[handle.casefold()] = country
        post["country"] = country

    def _save_grok_posts(
        self,
        reply: str,
        seen: set[str],
        product_id: str = "",
        session: BrowserSession | None = None,
        job: dict[str, Any] | None = None,
        countries: dict[str, str] | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows: list[dict[str, Any]] = []
        for item in posts_from_reply(reply):
            if session is not None and job is not None:
                self._attach_country(session, job, item, countries or {})
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
                    "created_at": published_time(key) or None,
                    "collected_at": now,
                    "latest_views": item.get("views"),
                    "latest_likes": None,
                    "latest_reposts": None,
                    "latest_replies": None,
                }
            )
            if product_id:
                self.store.remember_post(str(post.get("id") or ""), product_id)
            rows.append(
                {
                    **item,
                    "id": str(uuid.uuid4()),
                    "post_id": post["id"],
                    "status": "saved",
                    "reply": "",
                    "comment_id": None,
                    "product_id": product_id,
                    "added_at": now,
                }
            )
        return len(self.store.add_coming(rows))

    def start_coming_comments(self) -> dict[str, Any]:
        product = self._selected_raw()
        if not isinstance(product, dict) or not str(product.get("detail") or "").strip():
            raise AppError("Verify the product on Finder first.", 409, "not_verified")
        rows = self._coming_rows(product)
        pending = [row for row in rows if str(row.get("status") or "saved") == "saved"]
        if not pending:
            drafted = [row for row in rows if str(row.get("status") or "") == "drafted"]
            if drafted:
                raise AppError("Drafts are waiting. Use Post replies to send them.", 409, "needs_review")
            raise AppError("The coming list is empty.", 409, "empty")
        return self.jobs.start("coming_comments", lambda job: self._coming_comments(job, pending, product))

    def start_post_drafts(self) -> dict[str, Any]:
        """Post every drafted reply for the selected product. This click is the approval."""
        product = self._selected_raw()
        if not isinstance(product, dict) or not str(product.get("detail") or "").strip():
            raise AppError("Verify the product on Finder first.", 409, "not_verified")
        rows = [
            row
            for row in self._coming_rows(product)
            if str(row.get("status") or "") in {"drafted", "posting"} and str(row.get("reply") or "").strip() and str(row.get("url") or "").startswith("http")
        ]
        if not rows:
            raise AppError("No drafted reply is waiting.", 409, "empty")
        return self.jobs.start("coming_post", lambda job: self._post_drafts(job, rows, product))

    def start_post_coming(self, comment_id: str, idempotency_key: str) -> dict[str, Any]:
        """Post one drafted coming reply. The separate approve step is not required."""
        del idempotency_key
        matches = [row for row in self.store.list_coming() if str(row.get("comment_id") or "") == str(comment_id)]
        if not matches:
            raise AppError("Comment not found.", 404, "not_found")
        row = matches[0]
        if str(row.get("status") or "") == "published":
            raise AppError("This reply is already published.", 409, "published")
        if str(row.get("status") or "") not in {"drafted", "approved"} or not str(row.get("reply") or "").strip():
            raise AppError("This reply is not ready to post.", 409, "not_ready")
        product = self.store.get_product(str(row.get("product_id") or "")) or self._selected_raw() or {}
        return self.jobs.start("coming_post", lambda job: self._post_drafts(job, [row], product))

    def _coming_comments(self, job: dict[str, Any], pending: list[dict[str, Any]], product: dict[str, Any]) -> dict[str, Any]:
        """Draft a reply from the saved analysis. This does not publish it."""
        name = str(product.get("name") or "")
        url = str(product.get("url") or "")
        detail = str(product.get("detail") or "")
        product_id = str(product.get("id") or "")
        chat_url = str(product.get("chat_url") or "")
        if not chat_url.startswith("https://chatgpt.com/c/"):
            saved = public_state(self._post_path()).get("details")
            if isinstance(saved, dict) and str(saved.get("name") or "").strip().casefold() == name.casefold():
                maybe = str(saved.get("chat_url") or "")
                if maybe.startswith("https://chatgpt.com/c/"):
                    chat_url = maybe
                    product["chat_url"] = chat_url
        fresh_chat = not chat_url.startswith("https://chatgpt.com/c/")
        drafted = 0
        done = {
            str(row.get("external_id") or "")
            for row in self.store.list_coming(product_id or None)
            if str(row.get("status") or "") in {"published", "uncertain", "posting"}
        }

        def mark(external_id: str, fields: dict[str, Any]) -> None:
            self.store.update_coming(external_id, fields, product_id or None)

        with BrowserSession(self.settings) as session:
            self._require_account(session, job, product)
            total = len(pending)
            for index, row in enumerate(pending):
                if _cancelled(job)():
                    break
                if index > 0 and self._pause(job, QUERY_PAUSE_SECONDS, "Waiting 30 seconds before the next post."):
                    break
                status_key = str(row.get("external_id") or row.get("url") or "")
                if status_key and status_key in done:
                    self.jobs.log(job["id"], "Already replied to this post. Skipping.")
                    continue
                if status_key:
                    done.add(status_key)
                self.jobs.log(job["id"], f"Drafting {index + 1} of {total}.", progress=10 + int(80 * index / max(total, 1)))
                stored_post = self.store.get_post(str(row.get("post_id") or "")) if row.get("post_id") else None
                text = str((stored_post or {}).get("text") or "").strip()
                handle = str((stored_post or {}).get("handle") or row.get("handle") or "")
                try:
                    opened = session.read_discussion(str(row.get("url") or ""))
                except RuntimeError as exc:
                    if "signed out" in str(exc).lower():
                        raise
                    self.jobs.log(job["id"], str(exc)[:180])
                    opened = None
                if not opened:
                    mark(str(row.get("external_id") or ""), {"status": "skipped", "reply": "Could not open this post."})
                    continue
                text = str(opened.get("text") or text).strip()
                handle = str(opened.get("handle") or handle)
                comments = [item for item in opened.get("comments") or [] if str(item.get("text") or "").strip()]
                views = opened.get("views") if opened.get("views") is not None else row.get("views")
                reply_total = engagement_count(opened.get("replies"), len(comments))
                if len(text) < 12:
                    mark(str(row.get("external_id") or ""), {"status": "skipped", "reply": "Too little text to reply to."})
                    continue
                own = str(product.get("x_handle") or self.settings.x_handle or "")
                if replied_by(comments, own) or session.account_replied_here():
                    mark(str(row.get("external_id") or ""), {"status": "skipped", "reply": "This account already replied."})
                    self.jobs.log(job["id"], "This account already replied. Skipping.")
                    continue
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
                        "created_at": published_time(opened.get("external_id") or row.get("external_id"), opened.get("created_at")) or None,
                        "collected_at": _now(),
                        "latest_views": views,
                        "latest_likes": opened.get("likes"),
                        "latest_reposts": opened.get("reposts"),
                        "latest_replies": reply_total,
                    }
                )
                if stored_post is None:
                    continue
                reason = reply_gate(
                    views,
                    reply_total,
                    text,
                    detail,
                    likes=opened.get("likes"),
                    reposts=opened.get("reposts"),
                )
                if reason:
                    self.jobs.log(job["id"], reason)
                    mark(str(row.get("external_id") or ""), {"status": "skipped", "reply": reason})
                    continue
                self.jobs.log(job["id"], f"Read {len(comments)} comments on a post with {views} views.")
                self.jobs.log(job["id"], f"Writing a reply for @{handle or 'post'}.", progress=20 + int(70 * index / max(total, 1)))
                recent = [
                    str(item.get("reply") or "")
                    for item in self.store.list_coming(product_id or None)
                    if str(item.get("reply") or "").strip() and str(item.get("external_id") or "") != str(row.get("external_id") or "")
                ]
                raw = self._complete(
                    session,
                    reply_prompt(
                        text,
                        str(row.get("url") or ""),
                        name=name,
                        url=url,
                        analysis=detail,
                        comments=comments,
                        created_at=str(opened.get("created_at") or ""),
                        author_handle=handle,
                    ),
                    _cancelled(job),
                    "" if fresh_chat else chat_url,
                    8,
                )
                chat_url, fresh_chat = self._keep_chat(session, product, chat_url, fresh_chat)
                skipped, reply = reply_outcome(raw)
                if skipped:
                    mark(str(row.get("external_id") or ""), {"status": "skipped", "reply": f"Skip: {skipped}"})
                    self.jobs.log(job["id"], f"Skip: {skipped}")
                    continue
                problems = reply_problems(reply, name=name, url=url, post_text=text, facts=detail, recent=recent)
                if problems:
                    raw = self._complete(
                        session,
                        rewrite_reply_prompt(" ".join(problems), name=name, url=url, post_text=text),
                        _cancelled(job),
                        "" if fresh_chat else chat_url,
                        8,
                    )
                    chat_url, fresh_chat = self._keep_chat(session, product, chat_url, fresh_chat)
                    skipped, reply = reply_outcome(raw)
                    if skipped:
                        mark(str(row.get("external_id") or ""), {"status": "skipped", "reply": f"Skip: {skipped}"})
                        self.jobs.log(job["id"], f"Skip: {skipped}")
                        continue
                    problems = reply_problems(reply, name=name, url=url, post_text=text, facts=detail, recent=recent)
                if problems:
                    mark(str(row.get("external_id") or ""), {"status": "skipped", "reply": " ".join(problems)})
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
                mark(
                    str(row.get("external_id") or ""),
                    {"status": "drafted", "reply": reply, "comment_id": comment["id"], "post_id": stored_post["id"]},
                )
                drafted += 1
                scored = reply_quality(reply, name=name, url=url, post_text=text, facts=detail, recent=recent)
                self.jobs.log(job["id"], f"Quality {scored['score']}. Draft saved for review. Nothing was posted.")
        self.jobs.update(job["id"], message=f"Drafted {drafted} replies. Nothing was posted.", progress=100, results_count=drafted)
        return {"drafted": drafted}

    def _post_drafts(
        self,
        job: dict[str, Any],
        rows: list[dict[str, Any]],
        product: dict[str, Any],
        *,
        pause_seconds: int = 120,
    ) -> dict[str, Any]:
        """Send each drafted reply, then wait two minutes. A failed send stays drafted. This does not like the post."""
        product_id = str(product.get("id") or "")
        posted = 0
        missed = 0

        def mark(external_id: str, fields: dict[str, Any]) -> None:
            self.store.update_coming(external_id, fields, product_id or None)

        with BrowserSession(self.settings) as session:
            self._require_account(session, job, product)
            total = len(rows)
            for index, row in enumerate(rows):
                if _cancelled(job)():
                    break
                if index > 0 and self._wait_between_replies(job, pause_seconds):
                    break
                external_id = str(row.get("external_id") or "")
                text = str(row.get("reply") or "").strip()
                url = str(row.get("url") or "")
                self.jobs.log(job["id"], f"Posting {index + 1} of {total}.", progress=10 + int(80 * index / max(total, 1)))
                mark(external_id, {"status": "posting"})
                try:
                    outcome = session.reply(url, text)
                except RuntimeError as exc:
                    mark(external_id, {"status": "drafted", "reply": text})
                    self.jobs.log(job["id"], str(exc)[:180])
                    missed += 1
                    if "signed out" in str(exc).lower():
                        raise
                    continue
                status = "published" if outcome == "published" else "uncertain"
                mark(external_id, {"status": status, "reply": text})
                comment_id = str(row.get("comment_id") or "")
                if comment_id and self.store.get_comment(comment_id):
                    self.store.update_comment(comment_id, {"status": status})
                posted += 1
                self.jobs.log(job["id"], "Reply posted." if status == "published" else "The reply was not confirmed. It was not sent again.")
        message = f"Posted {posted} replies."
        if missed:
            message = f"{message} {missed} stayed as drafts."
        self.jobs.update(job["id"], message=message, progress=100, results_count=posted)
        return {"posted": posted, "missed": missed}

    def _wait_between_replies(self, job: dict[str, Any], seconds: int) -> bool:
        """True when the job was stopped during the two-minute break between replies."""
        if seconds <= 0:
            return False
        self.jobs.log(job["id"], "Waiting 2 minutes before the next reply.")
        for _ in range(seconds):
            if _cancelled(job)():
                return True
            time.sleep(1)
        return _cancelled(job)()

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
            owner = self._selected_raw()
            if isinstance(owner, dict) and owner.get("id"):
                self.store.remember_post(str(stored.get("id") or ""), str(owner["id"]))
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
        product = self._selected_raw()
        if not isinstance(product, dict) or not product.get("id"):
            return self.store.list_posts(limit)
        ids = self._product_post_ids(str(product["id"]))
        if not ids:
            return self.store.list_posts(limit)
        return self.store.list_posts(limit, ids[:limit])

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
            raw = self._complete(
                session,
                analysis_prompt(post, discussion, product_name=str(self.current_settings().get("product_name") or ""), facts=self._saved_analysis()),
                _cancelled(job),
            )
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
        self.jobs.log(job["id"], "Writing one reply.", progress=30)
        with BrowserSession(self.settings) as session:
            raw = self._complete(
                session,
                comments_prompt(
                    post,
                    payload,
                    allow_promotion=allowed,
                    product_name=prefs["product_name"],
                    product_url=prefs["product_url"],
                    product_pitch=self._saved_analysis(),
                ),
                _cancelled(job),
            )
            try:
                parsed = CommentBody.model_validate(parse_json_object(raw))
            except (ValueError, ValidationError):
                raw = self._complete(
                    session,
                    "Return only the JSON object. Put the one reply in natural, or set should_reply to false and explain why in reason.",
                    _cancelled(job),
                )
                parsed = CommentBody.model_validate(parse_json_object(raw))
        if not parsed.should_reply:
            self.store.log("comments", "skipped", parsed.reason)
            return {"comments": [], "reason": parsed.reason or "This post is not a good fit for a reply."}
        options = {
            "natural": parsed.natural,
            "insightful": parsed.insightful,
            "question": parsed.question,
        }
        options = {name: text for name, text in options.items() if str(text or "").strip()}
        problems = unique_comments(options)
        saved = []
        for variant, text in options.items():
            issues = comment_problems(
                text,
                product_name=prefs["product_name"],
                product_url=prefs["product_url"],
                allow_promotion=allowed,
                post_text=str(post.get("text") or ""),
            )
            issues.extend(
                item
                for item in reply_problems(
                    text,
                    name=prefs["product_name"],
                    url=prefs["product_url"],
                    post_text=str(post.get("text") or ""),
                    facts=self._saved_analysis(),
                )
                if item not in issues
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
        facts = self._saved_analysis()
        recent = [
            str(item.get("text") or "")
            for item in self.store.list_comments(str(post.get("id") or ""))
            if str(item.get("id") or "") != str(comment.get("id") or "") and str(item.get("text") or "").strip()
        ]
        self.jobs.log(job["id"], "Rewriting one reply.", progress=40)
        with BrowserSession(self.settings) as session:
            raw = self._complete(
                session,
                regenerate_prompt(
                    post,
                    comment["variant"],
                    comment["text"],
                    allow_promotion=allowed,
                    facts=facts,
                    recent=recent,
                ),
                _cancelled(job),
            )
            parsed = parse_json_object(raw)
        if parsed.get("should_reply") is False:
            self.store.log("comments", "skipped", str(parsed.get("reason") or ""))
            raise RuntimeError(str(parsed.get("reason") or "This post is not a good fit for a reply."))
        text = " ".join(str(parsed.get(comment["variant"]) or "").split())
        if comment.get("variant") == "coming":
            issues = reply_problems(
                text,
                name=prefs["product_name"],
                url=prefs["product_url"],
                post_text=str(post.get("text") or ""),
                facts=facts,
                recent=recent,
            )
        else:
            issues = comment_problems(
                text,
                product_name=prefs["product_name"],
                product_url=prefs["product_url"],
                allow_promotion=allowed,
                post_text=str(post.get("text") or ""),
            )
            issues.extend(
                item
                for item in reply_problems(
                    text,
                    name=prefs["product_name"],
                    url=prefs["product_url"],
                    post_text=str(post.get("text") or ""),
                    facts=facts,
                    recent=recent,
                )
                if item not in issues
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
        post = self.store.get_post(comment["post_id"])
        if comment.get("variant") == "coming":
            issues = reply_problems(
                cleaned,
                name=prefs["product_name"],
                url=prefs["product_url"],
                post_text=str((post or {}).get("text") or ""),
                facts=self._saved_analysis(),
            )
        else:
            issues = comment_problems(
                cleaned,
                product_name=prefs["product_name"],
                product_url=prefs["product_url"],
                allow_promotion=bool(prefs["allow_promotion"]),
                post_text=str((post or {}).get("text") or ""),
            )
            issues.extend(
                item
                for item in reply_problems(
                    cleaned,
                    name=prefs["product_name"],
                    url=prefs["product_url"],
                    post_text=str((post or {}).get("text") or ""),
                    facts=self._saved_analysis(),
                )
                if item not in issues
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
                self.store.update_coming(
                    str(row.get("external_id") or ""),
                    {"status": "approved", "reply": updated["text"]},
                    str(row.get("product_id") or "") or None,
                )
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
                self.store.update_coming(
                    str(row.get("external_id") or ""),
                    {"status": status, "reply": comment["text"]},
                    str(row.get("product_id") or "") or None,
                )
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
        product = self._selected_raw()
        counts = dict(self.store.counts())
        owned = self._coming_rows(product)
        if owned:
            counts["posts"] = len(owned)
        return {
            "counts": counts,
            "scan": self._scan_counts(),
            "logs": self.store.list_logs(20),
            "database": self.store.mode,
            "product_name": str((product or {}).get("name") or ""),
        }

    def _scan_counts(self) -> dict[str, Any]:
        """Original posts and the other totals from the latest activity refresh."""
        snapshot = self.activity_snapshot()
        fresh = bool(snapshot.get("collected_at"))

        def number(key: str) -> int | None:
            if not fresh:
                return None
            try:
                return int(snapshot.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        return {
            "posts": number("posts_created"),
            "comments": number("comments_dropped"),
            "views": number("views"),
            "likes": number("likes"),
            "replies": number("replies_received"),
            "collected_at": str(snapshot.get("collected_at") or ""),
            "handle": str(snapshot.get("handle") or ""),
        }

    def activity_snapshot(self) -> dict[str, Any]:
        path = self._activity_path()
        snapshot = load_activity(path)
        if snapshot.get("collected_at"):
            return snapshot
        product = self._selected_raw()
        product_id = str(product.get("id") or "") if isinstance(product, dict) else ""
        stored = self.store.load_activity_snapshot(product_id) if product_id else None
        if isinstance(stored, dict) and stored.get("collected_at"):
            save_activity(path, stored)
            return stored
        legacy_path = self.settings.store_path.parent / "activity.json"
        if product is None or path == legacy_path:
            return snapshot
        legacy = load_activity(legacy_path)
        handle = str(legacy.get("handle") or "")
        if legacy.get("collected_at") and same_account(handle, str(product.get("x_username") or ""), str(product.get("x_handle") or "")):
            return legacy
        return snapshot

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

    def _remember_activity(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict) or not snapshot.get("collected_at"):
            return
        product = self._selected_raw()
        if not isinstance(product, dict) or not product.get("id"):
            return
        self.store.save_activity_snapshot(str(product["id"]), snapshot)

    def _activity_path(self) -> Any:
        folder = self.settings.store_path.parent
        product = self._selected_raw()
        if isinstance(product, dict) and product.get("id"):
            return folder / f"activity-{product['id']}.json"
        return folder / "activity.json"

    def _post_path(self) -> Any:
        return self.settings.store_path.parent / "daily_post.json"

    def daily_post_state(self) -> dict[str, Any]:
        state = public_state(self._post_path())
        details = state.get("details") if isinstance(state.get("details"), dict) else None
        if str((details or {}).get("writeup") or "").strip():
            return state
        product = self._selected_raw()
        if not isinstance(product, dict):
            return state
        text = str(product.get("detail") or "").strip()
        name = str(product.get("name") or "").strip()
        if not text and not name:
            return state
        state["details"] = {
            "writeup": text,
            "summary": " ".join(text.split())[:500],
            "name": name,
            "url": str(product.get("url") or ""),
            "verified": bool(text),
            "verified_at": str(product.get("verified_at") or ""),
            "chat_url": str(product.get("chat_url") or ""),
        }
        return state

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
        existing = self.store.find_product(product_name, product_url) or {}
        prefs = self._raw_settings()
        username = str(existing.get("x_username") or "")
        password = str(existing.get("x_password") or "")
        handle = str(existing.get("x_handle") or "")
        if not username and not password:
            saved_name = str(prefs.get("product_name") or "").strip().casefold()
            if saved_name and saved_name == product_name.casefold() and str(prefs.get("x_username") or "").strip():
                username = str(prefs.get("x_username") or "").strip()
                password = str(prefs.get("x_password") or "")
                handle = username.lstrip("@") if "@" not in username else handle
        chat_url = str(existing.get("chat_url") or "")
        if not chat_url and isinstance(current, dict):
            chat_url = str(current.get("chat_url") or "")
        row = {
            "id": str(existing.get("id") or uuid.uuid4()),
            "name": product_name,
            "url": product_url,
            "detail": text,
            "finder_verified": bool(for_finder or existing.get("finder_verified")),
            "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "x_username": username,
            "x_password": password,
            "x_handle": handle,
            "chat_url": chat_url,
            "search_queries": _query_list(existing.get("search_queries")),
            "creators": _query_list(existing.get("creators")),
        }
        self.store.save_product(row)
        prefs["selected_product_id"] = row["id"]
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
        return self._public_product(row)

    def latest_product(self) -> dict[str, Any] | None:
        return self._public_product(self._selected_raw())

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
            sent = {"count": 0}

            def complete(prompt: str) -> str:
                sent["count"] += 1
                chat = ""
                if sent["count"] == 1:
                    self.jobs.log(job["id"], "Waiting until ChatGPT finishes this reply.", progress=45)
                else:
                    self.jobs.log(job["id"], "The first reply is finished. Asking again in that chat.", progress=60)
                    remembered = str(session.chatgpt_url or "")
                    if remembered.startswith("https://chatgpt.com/c/"):
                        chat = remembered
                return self._complete(session, prompt, _cancelled(job), chat)

            details = prepare_details(
                snapshot,
                name=name,
                url=url,
                pitch="",
                complete=complete,
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

        product = self._selected_raw()
        self.jobs.log(
            job["id"],
            f"Checking the X account for {product.get('name') or 'the selected product'}." if product else "Opening X.",
            progress=10,
        )
        with BrowserSession(self.settings) as session:
            if product:
                self._require_account(session, job, product)
            else:
                state = session.ensure_x(_cancelled(job), lambda message: self.jobs.log(job["id"], message, progress=15))
                if state.status != "signed_in":
                    raise RuntimeError(state.detail)
            snapshot = collect_activity(session, path, log, progress, _cancelled(job))
        self._remember_activity(snapshot)
        return snapshot

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
                    product_pitch=self._saved_analysis(),
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
                post_text=str(post.get("text") or ""),
            )
            issues.extend(
                item
                for item in reply_problems(
                    text,
                    name=prefs["product_name"],
                    url=prefs["product_url"],
                    post_text=str(post.get("text") or ""),
                    facts=self._saved_analysis(),
                )
                if item not in issues
            )
            if issues or not text:
                draft = {"status": "skipped", "reason": " ".join(issues) or "No reply was kept.", "text": "", "posted": False}
            else:
                draft = {"status": "draft", "reason": "", "text": text, "posted": False}
        try:
            store_reply_draft(self._activity_path(), str(row.get("id") or ""), draft)
            self._remember_activity(load_activity(self._activity_path()))
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
        products = copied.get("products")
        if isinstance(products, list):
            hidden = []
            for item in products:
                if isinstance(item, dict):
                    safe_product = dict(item)
                    safe_product.pop("x_password", None)
                    hidden.append(safe_product)
                else:
                    hidden.append(item)
            copied["products"] = hidden
        return copied
