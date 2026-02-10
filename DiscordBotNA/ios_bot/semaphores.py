"""
Semaphores for preventing race conditions in concurrent operations.
"""

import asyncio

# Semaphores for different operation types
signup_semaphore = asyncio.Semaphore(1)  # For sign/unsign/sub/unsub operations
challenge_semaphore = asyncio.Semaphore(1)  # For challenge/unchallenge operations
ready_semaphore = asyncio.Semaphore(1)  # For ready command execution

# Per-channel semaphores for finer-grained control
channel_semaphores = {}

def get_channel_semaphore(channel_id: int) -> asyncio.Semaphore:
    """Get or create a semaphore for a specific channel.
    
    Args:
        channel_id: Discord channel ID
        
    Returns:
        Semaphore for that channel
    """
    if channel_id not in channel_semaphores:
        channel_semaphores[channel_id] = asyncio.Semaphore(1)
    return channel_semaphores[channel_id]
