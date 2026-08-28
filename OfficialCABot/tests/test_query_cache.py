"""
QueryCache is the shared building block behind every DB-outage-resilience
fix made this session (get_team, get_all_servers, get_channel_context all
depend on get_last_good() correctly surviving both TTL expiry and
invalidate()). If this file's behavior regresses, all of that resilience
work silently regresses with it -- which is exactly the kind of bug this
session's manual verification process wasn't guaranteed to catch next time.
"""
import time

from conftest import load_module_from_file


def _new_cache(**kwargs):
    module = load_module_from_file("ios_bot/db/cache.py")
    return module.QueryCache(**kwargs)


def test_set_then_get_returns_the_value():
    cache = _new_cache(safety_ttl_seconds=60)
    cache.set("key", {"a": 1})
    assert cache.get("key") == {"a": 1}


def test_get_missing_key_returns_none():
    cache = _new_cache(safety_ttl_seconds=60)
    assert cache.get("missing") is None


def test_ttl_expiry_makes_get_return_none():
    cache = _new_cache(safety_ttl_seconds=0.05)
    cache.set("key", "value")
    time.sleep(0.1)
    assert cache.get("key") is None


def test_get_last_good_survives_ttl_expiry():
    """The exact property the DB-outage fallback in db/teams.py's get_team()
    and db/servers.py's get_all_servers()/get_server_by_name() rely on."""
    cache = _new_cache(safety_ttl_seconds=0.05)
    cache.set("key", "value")
    time.sleep(0.1)
    assert cache.get("key") is None
    assert cache.get_last_good("key") == "value"


def test_get_last_good_is_none_when_never_set():
    cache = _new_cache(safety_ttl_seconds=60)
    assert cache.get_last_good("never-set") is None


def test_invalidate_clears_the_ttl_store_but_not_last_good():
    """This is deliberate: a write invalidates the TTL-bound cache so the
    next read is forced to hit the DB, but if that DB read then fails, the
    fallback still needs the pre-invalidation value to serve."""
    cache = _new_cache(safety_ttl_seconds=60)
    cache.set("key", "value")
    cache.invalidate("key")
    assert cache.get("key") is None
    assert cache.get_last_good("key") == "value"


def test_invalidate_prefix_only_matches_prefixed_keys():
    cache = _new_cache(safety_ttl_seconds=60)
    cache.set("servers:all", "a")
    cache.set("servers:by_name:x", "b")
    cache.set("teams:by_id:1", "c")
    cache.invalidate_prefix("servers:")
    assert cache.get("servers:all") is None
    assert cache.get("servers:by_name:x") is None
    assert cache.get("teams:by_id:1") == "c"


def test_clear_wipes_both_the_ttl_store_and_last_good():
    cache = _new_cache(safety_ttl_seconds=60)
    cache.set("key", "value")
    cache.clear()
    assert cache.get("key") is None
    assert cache.get_last_good("key") is None


def test_set_overwrites_both_stores():
    cache = _new_cache(safety_ttl_seconds=60)
    cache.set("key", "old")
    cache.set("key", "new")
    assert cache.get("key") == "new"
    assert cache.get_last_good("key") == "new"
