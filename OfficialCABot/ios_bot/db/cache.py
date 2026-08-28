"""
Shared in-process read cache, invalidated by the write that actually
changed something instead of relying on a blind TTL.

The bot runs as a single process against a small, low-churn dataset (teams,
servers, and top-rating leaderboards change rarely -- new matches, team
registrations, or server edits are all explicit bot actions, not external
writes racing the cache). So the correctness story is: every write path that
touches a cached read calls invalidate()/invalidate_prefix() right after it
commits. The TTL here is a safety net only, for the rare write path that
forgets to invalidate, or a manual DB edit made outside the bot -- it should
be long (minutes), not the primary way staleness gets bounded.
"""
import time
from typing import Any, Dict, Optional


class QueryCache:
    def __init__(self, safety_ttl_seconds: int = 300):
        self._store: Dict[str, Dict[str, Any]] = {}
        # Last successfully-fetched value per key, independent of the TTL
        # store above and never cleared by expiry or invalidate() -- only by
        # a newer successful fetch or an explicit clear(). This is what lets
        # a handful of availability-critical reads (team channel routing,
        # the server list) degrade to "serve the last known-good answer"
        # instead of raising when the DB is briefly unreachable. Most
        # QueryCache callers never call get_last_good() and are unaffected.
        self._last_good: Dict[str, Any] = {}
        self._safety_ttl = safety_ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if not entry:
            return None
        if entry["expires_at"] <= time.monotonic():
            del self._store[key]
            return None
        return entry["value"]

    def get_last_good(self, key: str) -> Optional[Any]:
        """The most recent successfully-cached value for `key`, even if its
        normal TTL has since expired. Returns None if this key has never
        been successfully cached. Intended as a DB-outage fallback for read
        paths where availability matters more than freshness."""
        return self._last_good.get(key)

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        self._store[key] = {
            "value": value,
            "expires_at": time.monotonic() + (ttl_seconds if ttl_seconds is not None else self._safety_ttl),
        }
        self._last_good[key] = value

    def invalidate(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)
            # _last_good is deliberately left alone: if the write that just
            # invalidated this key is followed by a fetch that fails because
            # the DB is down, callers should still be able to fall back to
            # the last value we actually had rather than nothing at all.

    def invalidate_prefix(self, prefix: str) -> None:
        for key in list(self._store.keys()):
            if key.startswith(prefix):
                del self._store[key]

    def clear(self) -> None:
        self._store.clear()
        self._last_good.clear()
