"""
Utility functions for handling player name display in embeds.
"""
import discord


def truncate_name(name: str, max_length: int = 20) -> str:
    """Truncate a name to a maximum length, adding ellipsis if needed.

    Args:
        name: The name to truncate
        max_length: Maximum length before truncation (default 20)

    Returns:
        Truncated name with ellipsis if needed
    """
    if len(name) <= max_length:
        return name
    return name[:max_length-3] + "..."


def get_display_name(player, max_length: int = 20) -> str:
    """Get a display name for a player, handling both Discord Members and TextPlayers.

    For Discord Members with long mentions, uses their display_name instead.
    For TextPlayers, truncates if needed.

    Args:
        player: Discord Member or TextPlayer object
        max_length: Maximum length before truncation (default 20)

    Returns:
        Formatted display name
    """
    from ios_bot.signup_manager import is_text_player

    if player is None:
        return "❔"

    # For TextPlayer objects
    if is_text_player(player):
        name = player.display_name if hasattr(player, 'display_name') else str(player)
        return truncate_name(name, max_length)

    # For Discord Member objects
    if isinstance(player, discord.Member) or isinstance(player, discord.User):
        # Check if mention would be too long (mentions are <@discord_id>)
        # If display_name is long, just use truncated display_name instead of mention
        if len(player.display_name) > max_length:
            return truncate_name(player.display_name, max_length)
        return player.display_name

    # Fallback
    return truncate_name(str(player), max_length)


def format_player_with_stats(name: str, stats_emojis: list, max_name_length: int = 18) -> str:
    """Format a player name with stat emojis, ensuring the line doesn't wrap.

    Args:
        name: Player name
        stats_emojis: List of emoji strings (e.g., ["GOALx2", "ASSISTx1"])
        max_name_length: Maximum length for the name portion

    Returns:
        Formatted string with truncated name and stats
    """
    truncated_name = truncate_name(name, max_name_length)
    stats_str = " ".join(stats_emojis) if stats_emojis else ""

    if stats_str:
        return f"{truncated_name} {stats_str}"
    return truncated_name
