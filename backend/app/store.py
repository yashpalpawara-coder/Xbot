from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.errors import DatabaseError

TABLES = (
    "searches",
    "posts",
    "metrics",
    "analyses",
    "comments",
    "jobs",
    "saved",
    "events",
    "logs",
    "accounts",
    "products",
    "coming",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def product_from_settings(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rebuild one product when the product file is gone but settings still name it."""
    if not isinstance(payload, dict):
        return None
    name = " ".join(str(payload.get("product_name") or "").split())
    product_id = str(payload.get("selected_product_id") or "").strip()
    if not name or not product_id:
        return None
    username = str(payload.get("x_username") or "").strip()
    handle = username.lstrip("@") if username and "@" not in username else ""
    return {
        "id": product_id,
        "name": name,
        "url": str(payload.get("product_url") or "").strip(),
        "detail": str(payload.get("product_pitch") or ""),
        "finder_verified": False,
        "verified_at": "",
        "x_username": username,
        "x_password": str(payload.get("x_password") or ""),
        "x_handle": handle,
        "chat_url": "",
        "search_queries": [],
        "creators": [],
    }


def _combine_products(remote: list[dict[str, Any]], local: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*remote, *local]:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if item_id not in by_id:
            order.append(item_id)
        by_id[item_id] = item
    return [by_id[item_id] for item_id in order]


def _keep_latest_per_product(rows: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    """Each product keeps its own latest posts. One product's finds do not erase another's."""
    kept: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in reversed(rows):
        if not isinstance(item, dict):
            continue
        key = str(item.get("product_id") or "")
        used = counts.get(key, 0)
        if used >= limit:
            continue
        counts[key] = used + 1
        kept.append(item)
    kept.reverse()
    return kept


def _blank() -> dict[str, Any]:
    data: dict[str, Any] = {name: [] for name in TABLES}
    data["settings"] = None
    return data


class LocalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _blank()
        data = json.loads(self.path.read_text())
        for name in TABLES:
            data.setdefault(name, [])
        data.setdefault("settings", None)
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(self.path)

    def upsert_search(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["searches"] = [item for item in data["searches"] if item["id"] != row["id"]]
            data["searches"].append(row)
            self._write(data)
        return row

    def upsert_post(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            kept = []
            for item in data["posts"]:
                if item["id"] == row["id"] or item["external_id"] == row["external_id"]:
                    row["id"] = item["id"]
                    continue
                kept.append(item)
            kept.append(row)
            data["posts"] = kept
            self._write(data)
        return row

    def add_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["metrics"].append(row)
            self._write(data)
        return row

    def list_posts(self, limit: int = 100, ids: list[str] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._read()["posts"])
        if ids is not None:
            wanted = set(ids)
            rows = [item for item in rows if item.get("id") in wanted]
            order = {post_id: index for index, post_id in enumerate(ids)}
            rows.sort(key=lambda item: order.get(item.get("id"), 10**9))
            return rows[:limit]
        rows.sort(key=lambda item: item.get("collected_at") or "", reverse=True)
        return rows[:limit]

    def get_post(self, post_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read()["posts"]:
                if item["id"] == post_id:
                    return item
        return None

    def metrics_for(self, post_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item for item in self._read()["metrics"] if item["post_id"] == post_id]
        rows.sort(key=lambda item: item.get("collected_at") or "")
        return rows

    def save_analysis(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["analyses"].append(row)
            self._write(data)
        return row

    def latest_analysis(self, post_id: str) -> dict[str, Any] | None:
        with self._lock:
            rows = [item for item in self._read()["analyses"] if item["post_id"] == post_id]
        if not rows:
            return None
        rows.sort(key=lambda item: item.get("created_at") or "")
        return rows[-1]

    def add_comment(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["comments"].append(row)
            self._write(data)
        return row

    def update_comment(self, comment_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            found = None
            for item in data["comments"]:
                if item["id"] == comment_id:
                    item.update(fields)
                    item["updated_at"] = _now()
                    found = item
                    break
            if found is None:
                raise DatabaseError("Comment not found.")
            self._write(data)
        return found

    def list_comments(self, post_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._read()["comments"])
        if post_id:
            rows = [item for item in rows if item["post_id"] == post_id]
        rows.sort(key=lambda item: item.get("created_at") or "")
        return rows

    def get_comment(self, comment_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read()["comments"]:
                if item["id"] == comment_id:
                    return item
        return None

    def upsert_job(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["jobs"] = [item for item in data["jobs"] if item["id"] != row["id"]]
            data["jobs"].append(row)
            self._write(data)
        return row

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._read()["jobs"])
        rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return rows[:limit]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read()["jobs"]:
                if item["id"] == job_id:
                    return item
        return None

    def add_saved(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            for item in data["saved"]:
                if item["item_type"] == row["item_type"] and item["item_id"] == row["item_id"]:
                    return item
            data["saved"].append(row)
            self._write(data)
        return row

    def list_saved(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._read()["saved"])
        rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return rows

    def delete_saved(self, saved_id: str) -> None:
        with self._lock:
            data = self._read()
            data["saved"] = [item for item in data["saved"] if item["id"] != saved_id]
            self._write(data)

    def add_event(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["events"].append(row)
            self._write(data)
        return row

    def update_event(self, event_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            found = None
            for item in data["events"]:
                if item["id"] == event_id:
                    item.update(fields)
                    found = item
                    break
            if found is None:
                raise DatabaseError("Publish event not found.")
            self._write(data)
        return found

    def find_event(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._read()["events"]:
                if item["idempotency_key"] == key:
                    return item
        return None

    def list_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read()["events"])

    def log(self, action: str, status: str, detail: str = "") -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "action": action, "status": status, "detail": detail, "created_at": _now()}
        with self._lock:
            data = self._read()
            data["logs"].append(row)
            data["logs"] = data["logs"][-500:]
            self._write(data)
        return row

    def list_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._read()["logs"])
        return list(reversed(rows[-limit:]))

    def save_product(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["products"] = [item for item in data["products"] if item.get("id") != row.get("id")]
            data["products"].append(row)
            data["products"] = data["products"][-20:]
            self._write(data)
        return row

    def list_products(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item for item in self._read()["products"] if isinstance(item, dict)]

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        wanted = str(product_id or "")
        if not wanted:
            return None
        for item in self.list_products():
            if str(item.get("id") or "") == wanted:
                return item
        return None

    def find_product(self, name: str, url: str) -> dict[str, Any] | None:
        name_key = " ".join(name.split()).casefold()
        url_key = url.strip().rstrip("/").casefold()
        by_name = None
        by_url = None
        for item in self.list_products():
            item_name = " ".join(str(item.get("name") or "").split()).casefold()
            item_url = str(item.get("url") or "").strip().rstrip("/").casefold()
            if name_key and item_name == name_key:
                by_name = item
            elif url_key and item_url == url_key:
                by_url = item
        return by_name or by_url

    def latest_product(self) -> dict[str, Any] | None:
        rows = self.list_products()
        return rows[-1] if rows else None

    def add_coming(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read()
            known = {
                (str(item.get("product_id") or ""), str(item.get("external_id") or ""))
                for item in data["coming"]
                if isinstance(item, dict)
            }
            added: list[dict[str, Any]] = []
            for row in rows:
                key = (str(row.get("product_id") or ""), str(row.get("external_id") or ""))
                if not key[1] or key in known:
                    continue
                known.add(key)
                data["coming"].append(row)
                added.append(row)
            data["coming"] = _keep_latest_per_product(data["coming"])
            self._write(data)
        return added

    def claim_coming(self, product_id: str) -> int:
        """Posts saved before products were split stay with the product selected now."""
        wanted = str(product_id or "")
        if not wanted:
            return 0
        with self._lock:
            data = self._read()
            owned = {
                str(item.get("external_id") or "")
                for item in data["coming"]
                if isinstance(item, dict) and str(item.get("product_id") or "") == wanted
            }
            changed = 0
            for item in data["coming"]:
                if not isinstance(item, dict) or str(item.get("product_id") or ""):
                    continue
                key = str(item.get("external_id") or "")
                if key and key in owned:
                    continue
                item["product_id"] = wanted
                if key:
                    owned.add(key)
                changed += 1
            if changed:
                self._write(data)
        return changed

    def remember_post(self, post_id: str, product_id: str) -> None:
        post_id = str(post_id or "")
        product_id = str(product_id or "")
        if not post_id or not product_id:
            return
        with self._lock:
            data = self._read()
            owners = data.get("post_owners")
            if not isinstance(owners, dict):
                owners = {}
            owners[post_id] = product_id
            if len(owners) > 1000:
                owners = dict(list(owners.items())[-1000:])
            data["post_owners"] = owners
            self._write(data)

    def post_ids_for(self, product_id: str) -> list[str]:
        with self._lock:
            owners = self._read().get("post_owners") or {}
        if not isinstance(owners, dict):
            return []
        return [post_id for post_id, owner in owners.items() if str(owner) == str(product_id)]

    def list_coming(self, product_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item for item in self._read()["coming"] if isinstance(item, dict)]
        if product_id is not None:
            rows = [item for item in rows if str(item.get("product_id") or "") == str(product_id)]
        return list(reversed(rows))

    def update_coming(self, external_id: str, fields: dict[str, Any], product_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            found = None
            for item in data["coming"]:
                if str(item.get("external_id") or "") != str(external_id):
                    continue
                if product_id is not None and str(item.get("product_id") or "") != str(product_id):
                    continue
                item.update(fields)
                found = item
                break
            if found is None:
                return None
            self._write(data)
        return found

    def get_settings(self) -> dict[str, Any] | None:
        with self._lock:
            return self._read()["settings"]

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["settings"] = payload
            self._write(data)
        return payload

    def set_account(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["accounts"] = [item for item in data["accounts"] if item["platform"] != row["platform"]]
            data["accounts"].append(row)
            self._write(data)
        return row

    def accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read()["accounts"])

    def counts(self) -> dict[str, int]:
        with self._lock:
            data = self._read()
        comments = data["comments"]
        events = data["events"]
        return {
            "posts": len(data["posts"]),
            "analyses": len(data["analyses"]),
            "comments": len(comments),
            "approved": sum(1 for item in comments if item.get("status") == "approved"),
            "published": sum(1 for item in comments if item.get("status") == "published"),
            "failed": sum(1 for item in events if item.get("status") == "failed"),
            "uncertain": sum(1 for item in events if item.get("status") == "uncertain"),
        }

    def export(self) -> dict[str, Any]:
        with self._lock:
            return self._read()


class SupabaseStore:
    def __init__(self, url: str, key: str) -> None:
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = key
        self.http = httpx.Client(timeout=30)

    def _headers(self, prefer: str = "return=representation") -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }

    def _request(self, method: str, table: str, *, params: dict | None = None, json_body: Any = None, prefer: str | None = None) -> Any:
        headers = self._headers(prefer or "return=representation")
        response = self.http.request(method, f"{self.base}/{table}", params=params, json=json_body, headers=headers)
        if response.status_code >= 400:
            raise DatabaseError(response.text[:500])
        if not response.content:
            return None
        return response.json()

    def _one(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, list):
            if not payload:
                raise DatabaseError("Supabase returned no row.")
            return payload[0]
        if isinstance(payload, dict):
            return payload
        raise DatabaseError("Unexpected Supabase response.")

    def upsert_search(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "searches",
            params={"on_conflict": "id"},
            json_body=row,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return self._one(payload)

    def upsert_post(self, row: dict[str, Any]) -> dict[str, Any]:
        """Keep the existing post id. A new id would retarget the primary key and break comments."""
        external_id = str(row.get("external_id") or "")
        if external_id:
            found = self._request(
                "GET",
                "posts",
                params={"select": "id", "external_id": f"eq.{external_id}", "limit": "1"},
            ) or []
            if found and found[0].get("id"):
                row = {**row, "id": found[0]["id"]}
        payload = self._request(
            "POST",
            "posts",
            params={"on_conflict": "external_id"},
            json_body=row,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return self._one(payload)

    def add_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._one(self._request("POST", "post_metrics", json_body=row))

    def list_posts(self, limit: int = 100, ids: list[str] | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {"select": "*", "order": "collected_at.desc", "limit": str(limit)}
        if ids is not None:
            if not ids:
                return []
            params["id"] = "in.(" + ",".join(ids) + ")"
            params["limit"] = str(len(ids))
        payload = self._request("GET", "posts", params=params) or []
        if ids is not None:
            order = {post_id: index for index, post_id in enumerate(ids)}
            payload.sort(key=lambda item: order.get(item.get("id"), 10**9))
        return payload[:limit]

    def get_post(self, post_id: str) -> dict[str, Any] | None:
        payload = self._request("GET", "posts", params={"select": "*", "id": f"eq.{post_id}", "limit": "1"})
        return payload[0] if payload else None

    def metrics_for(self, post_id: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "post_metrics",
            params={"select": "*", "post_id": f"eq.{post_id}", "order": "collected_at.asc"},
        )
        return payload or []

    def save_analysis(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._one(self._request("POST", "analyses", json_body=row))

    def latest_analysis(self, post_id: str) -> dict[str, Any] | None:
        payload = self._request(
            "GET",
            "analyses",
            params={"select": "*", "post_id": f"eq.{post_id}", "order": "created_at.desc", "limit": "1"},
        )
        return payload[0] if payload else None

    def add_comment(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._one(self._request("POST", "comments", json_body=row))

    def update_comment(self, comment_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        fields = {**fields, "updated_at": _now()}
        payload = self._request("PATCH", "comments", params={"id": f"eq.{comment_id}"}, json_body=fields)
        return self._one(payload)

    def list_comments(self, post_id: str | None = None) -> list[dict[str, Any]]:
        params = {"select": "*", "order": "created_at.asc"}
        if post_id:
            params["post_id"] = f"eq.{post_id}"
        return self._request("GET", "comments", params=params) or []

    def get_comment(self, comment_id: str) -> dict[str, Any] | None:
        payload = self._request("GET", "comments", params={"select": "*", "id": f"eq.{comment_id}", "limit": "1"})
        return payload[0] if payload else None

    def upsert_job(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "jobs",
            params={"on_conflict": "id"},
            json_body=row,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return self._one(payload)

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "jobs",
            params={"select": "*", "order": "updated_at.desc", "limit": str(limit)},
        )
        return payload or []

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        payload = self._request("GET", "jobs", params={"select": "*", "id": f"eq.{job_id}", "limit": "1"})
        return payload[0] if payload else None

    def add_saved(self, row: dict[str, Any]) -> dict[str, Any]:
        existing = self._request(
            "GET",
            "saved_items",
            params={
                "select": "*",
                "item_type": f"eq.{row['item_type']}",
                "item_id": f"eq.{row['item_id']}",
                "limit": "1",
            },
        )
        if existing:
            return existing[0]
        return self._one(self._request("POST", "saved_items", json_body=row))

    def list_saved(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "saved_items", params={"select": "*", "order": "created_at.desc"})
        return payload or []

    def delete_saved(self, saved_id: str) -> None:
        self._request("DELETE", "saved_items", params={"id": f"eq.{saved_id}"}, prefer="return=minimal")

    def add_event(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._one(self._request("POST", "publish_events", json_body=row))

    def update_event(self, event_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        payload = self._request("PATCH", "publish_events", params={"id": f"eq.{event_id}"}, json_body=fields)
        return self._one(payload)

    def find_event(self, key: str) -> dict[str, Any] | None:
        payload = self._request(
            "GET",
            "publish_events",
            params={"select": "*", "idempotency_key": f"eq.{key}", "limit": "1"},
        )
        return payload[0] if payload else None

    def list_events(self) -> list[dict[str, Any]]:
        return self._request("GET", "publish_events", params={"select": "*", "order": "published_at.desc"}) or []

    def log(self, action: str, status: str, detail: str = "") -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "action": action, "status": status, "detail": detail}
        return self._one(self._request("POST", "activity_logs", json_body=row))

    def list_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "activity_logs",
            params={"select": "*", "order": "created_at.desc", "limit": str(limit)},
        )
        return payload or []

    def get_settings(self) -> dict[str, Any] | None:
        payload = self._request("GET", "settings", params={"select": "payload", "id": "eq.1", "limit": "1"})
        if not payload:
            return None
        body = payload[0].get("payload") or {}
        return body or None

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        saved = self._request(
            "POST",
            "settings",
            params={"on_conflict": "id"},
            json_body={"id": 1, "payload": payload},
            prefer="resolution=merge-duplicates,return=representation",
        )
        return self._one(saved).get("payload") or payload

    def set_account(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "accounts",
            params={"on_conflict": "platform"},
            json_body=row,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return self._one(payload)

    def accounts(self) -> list[dict[str, Any]]:
        return self._request("GET", "accounts", params={"select": "*"}) or []

    def counts(self) -> dict[str, int]:
        def count(table: str, extra: dict | None = None) -> int:
            params = {"select": "id", "limit": "1"}
            if extra:
                params.update(extra)
            response = self.http.get(
                f"{self.base}/{table}",
                params=params,
                headers=self._headers("count=exact"),
            )
            if response.status_code >= 400:
                raise DatabaseError(response.text[:300])
            header = response.headers.get("content-range", "")
            if "/" not in header:
                return 0
            total = header.split("/")[-1]
            return int(total) if total.isdigit() else 0

        specs = (
            ("posts", None),
            ("analyses", None),
            ("comments", None),
            ("comments", {"status": "eq.approved"}),
            ("comments", {"status": "eq.published"}),
            ("publish_events", {"status": "eq.failed"}),
            ("publish_events", {"status": "eq.uncertain"}),
        )
        with ThreadPoolExecutor(max_workers=len(specs)) as pool:
            totals = list(pool.map(lambda spec: count(spec[0], spec[1]), specs))
        keys = ("posts", "analyses", "comments", "approved", "published", "failed", "uncertain")
        return dict(zip(keys, totals, strict=True))

    def export(self) -> dict[str, Any]:
        analyses = self._request(
            "GET",
            "analyses",
            params={"select": "*", "order": "created_at.desc", "limit": "200"},
        )
        return {
            "posts": self.list_posts(200),
            "analyses": analyses or [],
            "comments": self.list_comments(),
            "saved": self.list_saved(),
            "jobs": self.list_jobs(50),
            "logs": self.list_logs(100),
        }


def probe_supabase(url: str, key: str) -> tuple[bool, str]:
    try:
        response = httpx.get(
            url.rstrip("/") + "/rest/v1/posts",
            params={"select": "id", "limit": "1"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=8,
        )
    except httpx.HTTPError as exc:
        return False, str(exc)
    if response.status_code == 200:
        return True, "supabase"
    text = response.text[:240]
    if response.status_code in {404, 406} or "PGRST205" in text or "does not exist" in text:
        return False, "Supabase is reachable, but the XBot tables are missing. Run supabase/schema.sql in the SQL editor."
    return False, text or f"Supabase returned {response.status_code}."


class Store:
    def __init__(self, settings: Settings) -> None:
        self.local = LocalStore(settings.store_path)
        self.remote: SupabaseStore | None = None
        self.mode = "local"
        self.detail = "Using the local JSON store."
        if settings.database_mode != "local" and settings.supabase_url and settings.supabase_publishable_key:
            ok, detail = probe_supabase(settings.supabase_url, settings.supabase_publishable_key)
            if ok:
                self.remote = SupabaseStore(settings.supabase_url, settings.supabase_publishable_key)
                self.mode = "supabase"
                self.detail = "Using Supabase."
            else:
                self.detail = detail
        elif settings.database_mode == "supabase":
            self.detail = "SUPABASE_URL or the publishable key is missing. Using the local JSON store."

    def _settings_payload(self) -> dict[str, Any]:
        saved = self._db().get_settings()
        return saved if isinstance(saved, dict) else {}

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._settings_payload()
        for key in ("products", "activity_scans"):
            if key in current and key not in payload:
                payload = {**payload, key: current[key]}
        return self._db().save_settings(payload)

    def load_activity_snapshot(self, product_id: str) -> dict[str, Any] | None:
        scans = self._settings_payload().get("activity_scans")
        if not isinstance(scans, dict):
            return None
        row = scans.get(str(product_id or ""))
        return row if isinstance(row, dict) else None

    def save_activity_snapshot(self, product_id: str, snapshot: dict[str, Any]) -> None:
        product_id = str(product_id or "")
        if not product_id or not isinstance(snapshot, dict):
            return
        payload = self._settings_payload()
        scans = payload.get("activity_scans")
        if not isinstance(scans, dict):
            scans = {}
        scans[product_id] = snapshot
        payload["activity_scans"] = scans
        self._db().save_settings(payload)

    def save_product(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = self.local.save_product(row)
        if self.remote is not None:
            payload = self._settings_payload()
            remote = [item for item in payload.get("products") or [] if isinstance(item, dict)]
            payload["products"] = _combine_products(remote, self.local.list_products())[-20:]
            self.remote.save_settings(payload)
        return saved

    def list_products(self) -> list[dict[str, Any]]:
        local = self.local.list_products()
        if self.remote is None:
            return local
        payload = self._settings_payload()
        remote = [item for item in payload.get("products") or [] if isinstance(item, dict)]
        rows = _combine_products(remote, local)
        if not rows:
            recovered = product_from_settings(payload)
            rows = [recovered] if recovered else []
        if rows and rows != remote:
            payload["products"] = rows[-20:]
            self.remote.save_settings(payload)
        local_ids = {str(item.get("id") or "") for item in local}
        for item in rows:
            if str(item.get("id") or "") not in local_ids:
                self.local.save_product(item)
        return rows

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        wanted = str(product_id or "")
        if not wanted:
            return None
        for item in self.list_products():
            if str(item.get("id") or "") == wanted:
                return item
        return None

    def find_product(self, name: str, url: str) -> dict[str, Any] | None:
        name_key = " ".join(name.split()).casefold()
        url_key = url.strip().rstrip("/").casefold()
        by_name = None
        by_url = None
        for item in self.list_products():
            item_name = " ".join(str(item.get("name") or "").split()).casefold()
            item_url = str(item.get("url") or "").strip().rstrip("/").casefold()
            if name_key and item_name == name_key:
                by_name = item
            elif url_key and item_url == url_key:
                by_url = item
        return by_name or by_url

    def latest_product(self) -> dict[str, Any] | None:
        rows = self.list_products()
        return rows[-1] if rows else None

    def add_coming(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.local.add_coming(rows)

    def remember_post(self, post_id: str, product_id: str) -> None:
        self.local.remember_post(post_id, product_id)

    def post_ids_for(self, product_id: str) -> list[str]:
        return self.local.post_ids_for(product_id)

    def claim_coming(self, product_id: str) -> int:
        return self.local.claim_coming(product_id)

    def list_coming(self, product_id: str | None = None) -> list[dict[str, Any]]:
        return self.local.list_coming(product_id)

    def update_coming(self, external_id: str, fields: dict[str, Any], product_id: str | None = None) -> dict[str, Any] | None:
        return self.local.update_coming(external_id, fields, product_id)

    def _db(self) -> Any:
        return self.remote if self.remote is not None else self.local

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db(), name)
