"""Compare the X account open in Chrome with the account saved on a product."""

from __future__ import annotations


def _keys(*values: str) -> set[str]:
    found: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip().lstrip("@")
        if not cleaned or "@" in cleaned or " " in cleaned:
            continue
        found.add(cleaned.casefold())
    return found


def same_account(current: str, username: str, handle: str) -> bool:
    """True when the open @handle is the username or handle saved for this product."""
    current_key = str(current or "").strip().lstrip("@").casefold()
    if not current_key:
        return False
    return current_key in _keys(username, handle)
