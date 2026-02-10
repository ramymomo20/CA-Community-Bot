from ios_bot.config import * # Import bot to get channel

async def send_announcement(message_content: str = "", embed: discord.Embed = None, boolSend = False):
    """Sends a message or an embed to the predefined announcement channel."""
    try:
        main_channel_id = globals().get("MAIN_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID")
        other_channel_id = globals().get("OTHER_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID")
        announcement_channel = bot.get_channel(main_channel_id) if main_channel_id else None
        other_announcement_channel = bot.get_channel(other_channel_id) if other_channel_id else None
        if announcement_channel:
            await announcement_channel.send(content=message_content, embed=embed)
        if boolSend:
            if other_announcement_channel:
                await other_announcement_channel.send(content=message_content, embed=embed)
            else:
                print("[ANNOUNCEMENT ERROR] Secondary announcement channel not configured.")
        else:
            if main_channel_id:
                print(f"[ANNOUNCEMENT ERROR] Channel ID {main_channel_id} not found.")
            else:
                print("[ANNOUNCEMENT ERROR] MAIN_CHALLENGE_ANNOUNCEMENT_CHANNEL_ID not configured.")
    except Exception as e:
        print(f"[ANNOUNCEMENT ERROR] Failed to send announcement: {e}")

async def announce_challenge_issued(initiating_team_name: str, target_description: str, game_type: str, challenge_id: str, initiating_channel_mention: str):
    """Announces a new challenge."""
    message = f"⚔️ **{initiating_team_name}** has challenged **{target_description}** in {initiating_channel_mention} *Challenge ID: `{challenge_id}`*"
    await send_announcement(message_content=message)

async def announce_match_ready(home_team_name: str, opponent_team_name: str, game_type: str, initiating_channel_mention: str, embed_to_send: discord.Embed, content_to_send: str = ""):
    """Announces a match is ready and sends a detailed embed."""
    # The original message construction is now handled by the embed passed in.
    # We can use content_to_send for any additional pings or brief text alongside the embed.
    await send_announcement(message_content=content_to_send, embed=embed_to_send, boolSend=True)

async def announce_team_created(team_name: str, creator_name: str, guild_id: int):
    """Announces a new team creation."""
    message = f"🎉 Team **{team_name}** (Guild ID: `{guild_id}`) has been registered by **{creator_name}**!"
    await send_announcement(message_content=message)

async def announce_team_deleted(team_name: str, deleter_name: str, guild_id: int):
    """Announces a team deletion."""
    message = f"🗑️ Team **{team_name}** (Guild ID: `{guild_id}`) has been deleted by **{deleter_name}**."
    await send_announcement(message_content=message) 
