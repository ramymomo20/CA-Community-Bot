"""
Semaphores for preventing race conditions in concurrent operations.
"""

import asyncio

# Semaphores for different operation types
challenge_semaphore = asyncio.Semaphore(1)  # For challenge/unchallenge operations
ready_semaphore = asyncio.Semaphore(1)  # For ready command execution

# Per-channel semaphores for sign/unsign/sub/unsub operations. Keyed by
# channel_id rather than one global lock, so signups in unrelated channels
# (different guilds, different matchmaking channels) don't serialize behind
# each other -- only concurrent operations on the SAME channel's lineup need to.
channel_semaphores = {}
# Per-challenge semaphores for finer-grained control
challenge_match_start_semaphores = {}


def get_channel_semaphore(channel_id: int) -> asyncio.Semaphore:
    """Get or create a semaphore for a specific channel's signup state."""
    if channel_id not in channel_semaphores:
        channel_semaphores[channel_id] = asyncio.Semaphore(1)
    return channel_semaphores[channel_id]


def get_challenge_match_start_semaphore(challenge_key: str) -> asyncio.Semaphore:
    """Get or create a semaphore for challenge match finalization."""
    key = str(challenge_key or "").strip() or "unknown_challenge"
    if key not in challenge_match_start_semaphores:
        challenge_match_start_semaphores[key] = asyncio.Semaphore(1)
    return challenge_match_start_semaphores[key]
