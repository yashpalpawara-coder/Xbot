from __future__ import annotations

import threading

from app.config import get_settings
from app.jobs import JobManager
from app.services import Service
from app.store import Store


class AppState:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.store = Store(self.settings)
        self.jobs = JobManager(self.store)
        self.service = Service(self.store, self.jobs, self.settings)


_state: AppState | None = None
_lock = threading.Lock()


def get_state() -> AppState:
    global _state
    if _state is not None:
        return _state
    with _lock:
        if _state is None:
            _state = AppState()
        return _state


def reset_state() -> None:
    global _state
    _state = None
    get_settings.cache_clear()
