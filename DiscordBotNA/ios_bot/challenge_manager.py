# In-memory store for active challenges
# This will be expanded with helper functions to manage challenge states.
from collections import defaultdict
import datetime

active_challenges = defaultdict(dict)

broadcast_challenge_cooldowns = {}

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