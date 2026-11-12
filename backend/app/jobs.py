from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from app.errors import AppError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self, store: Any) -> None:
        self.store = store
        self._jobs: dict[str, dict[str, Any]] = {}
        self._guard = threading.Lock()
        self.browser_lock = threading.Lock()
        self._reserve = threading.Lock()

    def public(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job["id"],
            "type": job["type"],
            "status": job["status"],
            "progress": job["progress"],
            "message": job["message"],
            "error": job["error"],
            "results_count": job["results_count"],
            "logs": list(job["logs"]),
            "result": job["result"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._guard:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._guard:
            memory = {job["id"]: self.public(job) for job in self._jobs.values()}
        try:
            stored = {item["id"]: item for item in self.store.list_jobs(limit)}
        except Exception:
            stored = {}
        stored.update(memory)
        rows = list(stored.values())
        rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return rows[:limit]

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        with self._guard:
            job = self._jobs[job_id]
            job.update(fields)
            job["updated_at"] = _now()
            if "logs" in job and isinstance(job["logs"], list):
                job["logs"] = job["logs"][-200:]
            snapshot = self.public(job)
        try:
            self.store.upsert_job(snapshot)
        except Exception:
            pass
        return snapshot

    def log(self, job_id: str, message: str, **fields: Any) -> None:
        with self._guard:
            job = self._jobs[job_id]
            job["logs"].append(message)
            job["message"] = message
            job.update(fields)
            job["updated_at"] = _now()
            snapshot = self.public(job)
        try:
            self.store.upsert_job(snapshot)
        except Exception:
            pass

    def start(self, kind: str, work: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        if not self.browser_lock.acquire(blocking=False):
            raise AppError("Chrome is already in use. Wait for the current job or stop it.", 409, "browser_busy")
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "type": kind,
            "status": "running",
            "progress": 0,
            "message": "Starting",
            "error": None,
            "results_count": 0,
            "logs": [],
            "result": None,
            "created_at": _now(),
            "updated_at": _now(),
            "cancel": threading.Event(),
        }
        with self._guard:
            self._jobs[job_id] = job
        try:
            self.store.upsert_job(self.public(job))
        except Exception:
            pass

        def runner() -> None:
            try:
                result = work(job)
                if job["cancel"].is_set() and job["status"] == "running":
                    self.update(job_id, status="stopped", result=result, message="Stopped")
                elif job["status"] == "running":
                    self.update(job_id, status="done", progress=100, result=result, message=job["message"] or "Done")
            except Exception as exc:
                self.update(job_id, status="error", error=str(exc), message=str(exc))
            finally:
                self.browser_lock.release()

        threading.Thread(target=runner, daemon=True, name=f"xbot-{kind}").start()
        return self.public(job)

    def stop(self, job_id: str) -> dict[str, Any]:
        with self._guard:
            job = self._jobs.get(job_id)
            if job is None:
                stored = self.store.get_job(job_id)
                if stored is None:
                    raise AppError("Job not found.", 404, "not_found")
                return stored
            job["cancel"].set()
            job["message"] = "Stopping"
            job["updated_at"] = _now()
            snapshot = self.public(job)
        try:
            self.store.upsert_job(snapshot)
        except Exception:
            pass
        return snapshot
