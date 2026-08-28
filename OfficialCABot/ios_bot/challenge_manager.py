# In-memory store for active challenges
# This will be expanded with helper functions to manage challenge states.
from collections import defaultdict
import asyncio
import datetime

broadcast_challenge_cooldowns = {}

_background_tasks: set = set()


async def _get_db_handle():
    from ios_bot import bot
    return getattr(bot, "db", None)


async def persist_challenge_state(challenge_id: str) -> None:
    """Save the current in-memory state of one challenge to the DB, so it
    survives a bot restart. Called automatically on every top-level
    active_challenges[challenge_id] = ... assignment (see
    _PersistentChallengeDict below). If code instead mutates an already-
    stored challenge's fields in place (e.g.
    active_challenges[cid]['status'] = 'x'), call this explicitly afterward
    since that bypasses __setitem__. Best-effort: a failure here shouldn't
    break the challenge flow itself, just persistence."""
    data = active_challenges.get(challenge_id)
    if not data:
        return
    try:
        db_handle = await _get_db_handle()
        if db_handle is not None:
            await db_handle.matches.save_challenge_state(challenge_id, data)
    except Exception:
        pass


async def remove_challenge_state(challenge_id: str) -> None:
    """Delete a challenge's persisted DB state once it's fully resolved
    (accepted-and-started, declined, cancelled, or expired)."""
    try:
        db_handle = await _get_db_handle()
        if db_handle is not None:
            await db_handle.matches.delete_challenge_state(challenge_id)
    except Exception:
        pass


def _fire_and_forget(coro) -> None:
    """Schedule a background coroutine from sync code (dict dunder methods
    can't await directly), keeping a reference so it can't be garbage
    collected mid-flight."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop (e.g. module import time) -- skip
    task = loop.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


class _PersistentChallengeDict(defaultdict):
    """defaultdict(dict) that auto-persists to CHALLENGE_STATE on every
    top-level assignment/deletion of active_challenges[challenge_id]. Covers
    the common `active_challenges[cid] = {...}` and `del active_challenges[cid]`
    patterns used throughout commands/challenge.py, commands/unchallenge.py,
    and commands/ready.py without needing to touch every call site. Does NOT
    catch in-place mutation of an already-stored dict (e.g.
    active_challenges[cid]['status'] = 'accepted') -- those call sites call
    persist_challenge_state(cid) explicitly."""

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        _fire_and_forget(persist_challenge_state(key))

    def __delitem__(self, key):
        super().__delitem__(key)
        _fire_and_forget(remove_challenge_state(key))


active_challenges = _PersistentChallengeDict(dict)


async def load_persisted_challenges() -> int:
    """Restore active_challenges from the DB on bot startup. Returns the
    number of challenges restored. Populates the dict directly (bypassing
    __setitem__) since these rows are already what's in the DB -- no need
    to immediately re-save what was just loaded."""
    try:
        db_handle = await _get_db_handle()
        if db_handle is None:
            return 0
        restored = await db_handle.matches.load_all_challenge_states()
    except Exception:
        return 0
    for challenge_id, data in restored.items():
        dict.__setitem__(active_challenges, challenge_id, data)
    return len(restored)

# Example structure for an entry in active_challenges:
# challenge_id (e.g., initiating_guild_id_timestamp): {
#     "initiating_team_id": int,
#     "initiating_team_name": str,
#     "initiating_guild_id": int, # Guild ID where challenge was made
#     "initiating_channel_id": int, # Channel ID where challenge was made
#     "game_type": str, # "6s" or "8s"
#     "target_type": str, # "broadcast", "team", "main_channel"
#     "target_id": int | str | None, # guild_id of target team, or main_channel_id/name, or None for broadcast
#     "target_name": str | None, # Name of target team or main channel
#     "status": str, # "pending_broadcast", "pending_direct", "accepted", "declined", "cancelled"
#     "challenge_message_id": int, # The ID of the message in the initiating team's channel showing their challenge status
#     "opponent_guild_id": int | None, # Guild ID of the team that accepted (if applicable)
#     "opponent_channel_id": int | None, # Channel ID of the opponent's matchmaking channel for this challenge
#     "opponent_team_name": str | None, 
#     "broadcast_messages": dict[int, int], # {channel_id: message_id} for broadcasted challenges
#     "challenge_issued_at": float, # timestamp
# }

# Helper functions will be added here, e.g.:
# def issue_challenge(...)
# def accept_challenge(...)
# def decline_challenge(...)
# def cancel_challenge(...)
# def get_challenge_by_initiator(...)
# def get_challenge_by_channel(...) # if a channel is involved in an active challenge 