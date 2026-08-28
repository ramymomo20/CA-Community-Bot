"""Lightweight signal for on-write Hub sync requests.

Write paths (team edits, match imports, tournament updates, player
linking) call request_hub_sync_soon() right after a change lands so the
Hub mirror picks it up on the next scheduler tick instead of waiting for
the full polling interval (up to 15 min) or the once-daily/weekly force-full
resync. Deliberately dependency-free so ios_bot/db/*.py and command modules
can import it without a circular import with ios_bot/tasks.py, which is
the module that actually reads and clears the flag each tick.
"""
from datetime import datetime, timezone
from typing import Optional

_requested_at: Optional[datetime] = None
_last_reason: str = ""


def request_hub_sync_soon(reason: str = "") -> None:
    """Ask the Hub sync scheduler to run on its next tick instead of waiting."""
    global _requested_at, _last_reason
    _requested_at = datetime.now(timezone.utc)
    _last_reason = reason


def has_pending_request() -> bool:
    return _requested_at is not None


def pending_reason() -> str:
    return _last_reason


def clear_pending_request() -> None:
    global _requested_at, _last_reason
    _requested_at = None
    _last_reason = ""
