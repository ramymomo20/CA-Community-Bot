from ios_bot.config import *
from ios_bot.signup_manager import (
    init_state as sm_init_state, 
    is_text_player as sm_is_text_player, 
    is_player_signed as sm_check_player_signed,
    get_player_position as sm_get_signed_position,
    refresh_lineup as sm_refresh_lineup,
    check_notification_cooldown,
    get_channel_context as sm_get_channel_context
)
from ios_bot.database_manager import get_team
from datetime import datetime, timezone

async def delete_after_delay(interaction, delay: int = 5):
    """Delete an interaction response after a delay"""
    await asyncio.sleep(delay)
    try:
        await interaction.delete_original_response()
    except:
        pass

def move_sub_to_position(state, position: str, team_number: int) -> Member:
    """Move the first sub to the given position and return the sub that was moved"""
    if not state["subs"]:
        return None
        
    sub = state["subs"].pop(0)
    state["teams"][team_number - 1][position] = {
        "player": sub,
        "signup_time": datetime.now(timezone.utc)
    }
    return sub

def is_player_signed(state, member: Member) -> bool:
    """Wrapper for backward compatibility"""
    return sm_check_player_signed(state, member)

def get_player_position(state, member: Member) -> tuple[int, str]:
    """Wrapper for backward compatibility"""
    return sm_get_signed_position(state, member)

class MoreOptionsView(View):
    def __init__(self, team_number: int, channel_id: int = None):
        super().__init__(timeout=60)  # 60 second timeout
        self.team_number = team_number
        self.channel_id = channel_id
        
        # Clear Position button
        clear_pos = Button(
            label="Clear Position",
            style=ButtonStyle.secondary,
            custom_id=f"clear_pos_team{team_number}"
        )
        clear_pos.callback = self.clear_position_callback
        self.add_item(clear_pos)
        
        # Clear Lineup button
        clear_lineup = Button(
            label="Clear Lineup",
            style=ButtonStyle.danger,
            custom_id="clear_lineup"
        )
        clear_lineup.callback = self.clear_lineup_callback
        self.add_item(clear_lineup)
        
        # Sub button
        sub_button = Button(
            label="Sub",
            style=ButtonStyle.primary,
            custom_id=f"sub_team{team_number}"
        )
        sub_button.callback = self.sub_callback
        self.add_item(sub_button)

        # Highlight button
        highlight = Button(
            label="Highlight",
            style=ButtonStyle.success,
            custom_id="highlight"
        )
        highlight.callback = self.highlight_callback
        self.add_item(highlight)
        
        # View Other Team's Lineup button (only if there's an active challenge)
        if self.channel_id and self._has_active_challenge():
            view_opponent = Button(
                label="View Opponent",
                style=ButtonStyle.secondary,
                custom_id="view_opponent_lineup"
            )
            view_opponent.callback = self.view_opponent_lineup_callback
            self.add_item(view_opponent)

    def _has_active_challenge(self) -> bool:
        """Check if this channel is involved in an active challenge"""
        from ios_bot.challenge_manager import active_challenges
        
        for challenge_id, challenge_data in active_challenges.items():
            if challenge_data.get("status") == "accepted":
                if (challenge_data.get("initiating_channel_id") == self.channel_id or 
                    challenge_data.get("opponent_channel_id") == self.channel_id):
                    return True
        return False

    async def clear_position_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await sm_init_state(interaction.guild_id, interaction.channel_id)
        if not state:
            await interaction.followup.send("Error: could not get channel state.", ephemeral=True)
            asyncio.create_task(delete_after_delay(interaction))
            return
        team = state["teams"][self.team_number - 1]
        
        # Show position selection for clearing
        positions_with_players = [(pos, member) for pos, member in team.items() if member is not None]
        if not positions_with_players:
            await interaction.followup.send("❌ No positions to clear!", ephemeral=True)
            asyncio.create_task(delete_after_delay(interaction))
            return
            
        # Create position buttons for clearing
        view = View(timeout=60)
        for pos, member in positions_with_players:
            button = Button(
                label=f"{pos}: {(member['player'].display_name if not sm_is_text_player(member['player']) else member['player'].name) if isinstance(member, dict) else (member.display_name if not sm_is_text_player(member) else member.name)}",
                style=ButtonStyle.secondary,
                custom_id=f"clear_{pos}"
            )
            
            async def make_callback(pos_arg=pos):
                async def callback(i: discord.Interaction):
                    await i.response.defer(ephemeral=True)
                    current_state = await sm_init_state(i.guild_id, i.channel_id)
                    if not current_state:
                        await i.followup.send("Error: could not get channel state for clearing.", ephemeral=True)
                        asyncio.create_task(delete_after_delay(i))
                        return
                    current_team = current_state["teams"][self.team_number - 1]
                    moved_sub = move_sub_to_position(current_state, pos_arg, self.team_number)
                    if moved_sub:
                        await i.followup.send(f"✅ Moved {moved_sub.mention} from subs to {pos_arg}", ephemeral=True)
                    else:
                        current_team[pos_arg] = None
                        await i.followup.send(f"✅ Cleared {pos_arg} position", ephemeral=True)
                    await sm_refresh_lineup(i.channel)
                    asyncio.create_task(delete_after_delay(i))
                return callback
                
            button.callback = await make_callback()
            view.add_item(button)
            
        await interaction.followup.send("Select position to clear:", view=view, ephemeral=True)
        asyncio.create_task(delete_after_delay(interaction, 10))

    async def clear_lineup_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.followup.send("❌ You need manage messages permission to clear the entire lineup", ephemeral=True)
            asyncio.create_task(delete_after_delay(interaction))
            return
            
        state = await sm_init_state(interaction.guild_id, interaction.channel_id)
        if state:
            for team in state["teams"]:
                for pos in team:
                    team[pos] = None
            state.get("subs", []).clear()
                
            await sm_refresh_lineup(interaction.channel)
            await interaction.followup.send("✅ Cleared all positions and subs", ephemeral=True)
        else:
            await interaction.followup.send("Error: could not get channel state to clear lineup.", ephemeral=True)
        asyncio.create_task(delete_after_delay(interaction))

    async def sub_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await sm_init_state(interaction.guild_id, interaction.channel_id)
        if not state:
            await interaction.followup.send("Error: could not get channel state for sub.", ephemeral=True)
            asyncio.create_task(delete_after_delay(interaction))
            return

        if sm_check_player_signed(state, interaction.user):
            await interaction.followup.send("❌ You are already signed to a position", ephemeral=True)
            asyncio.create_task(delete_after_delay(interaction))
            return
            
        subs = state.setdefault("subs", [])
        if interaction.user in subs:
            subs.remove(interaction.user)
            await interaction.followup.send("✅ You've been removed from subs", ephemeral=True)
        else:
            subs.append(interaction.user)
            await interaction.followup.send("✅ You've been added to subs", ephemeral=True)
            
        await sm_refresh_lineup(interaction.channel, author_override=interaction.user)
        asyncio.create_task(delete_after_delay(interaction))

    async def highlight_callback(self, interaction: discord.Interaction):
        # Check cooldown first using the unified checker
        can_send, minutes_remaining = check_notification_cooldown(interaction.channel_id)
        
        if can_send:
            await interaction.response.defer() 
            await interaction.channel.send(content="@here", allowed_mentions=discord.AllowedMentions(everyone=True))
            await interaction.followup.send("Highlight sent!", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Please wait {minutes_remaining} minute(s).", ephemeral=True)

    async def view_opponent_lineup_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Find the active challenge for this channel
        from ios_bot.challenge_manager import active_challenges
        from ios_bot.signup_manager import get_channel_state
        from ios_bot.config import EIGHTS_POSITIONS, SIXES_POSITIONS
        
        active_challenge_data = None
        is_initiator = False
        
        for challenge_data in active_challenges.values():
            if challenge_data.get("status") == "accepted":
                if challenge_data.get("initiating_channel_id") == self.channel_id:
                    active_challenge_data = challenge_data
                    is_initiator = True
                    break
                elif challenge_data.get("opponent_channel_id") == self.channel_id:
                    active_challenge_data = challenge_data
                    is_initiator = False
                    break
        
        if not active_challenge_data:
            await interaction.followup.send("❌ No active challenge found for this channel.", ephemeral=True)
            return
        
        # Get opponent's information
        if is_initiator:
            opponent_channel_id = active_challenge_data.get("opponent_channel_id")
            opponent_name = active_challenge_data.get("opponent_team_name", "Opponent")
            opponent_guild_id = active_challenge_data.get("opponent_guild_id")
        else:
            opponent_channel_id = active_challenge_data.get("initiating_channel_id")
            opponent_name = active_challenge_data.get("initiating_team_name", "Opponent")
            opponent_guild_id = active_challenge_data.get("initiating_guild_id")
        
        # Get opponent's current state
        opponent_state = get_channel_state(opponent_channel_id)
        if not opponent_state or not opponent_state.get("teams"):
            await interaction.followup.send(f"❌ Could not retrieve {opponent_name}'s lineup.", ephemeral=True)
            return
        
        # Determine which team index to use for opponent
        opponent_team_idx = 0
        if opponent_guild_id == MAIN_GUILD_ID and len(opponent_state.get("teams", [])) > 1:
            # If opponent is Main Guild and has multiple teams, they might be using team index 1
            if opponent_state.get("is_challenged_by_team_name"):
                opponent_team_idx = 0  # Main guild team lineup
        
        if len(opponent_state["teams"]) <= opponent_team_idx:
            await interaction.followup.send(f"❌ {opponent_name}'s lineup is not available.", ephemeral=True)
            return
        
        # Format opponent's lineup using the correct position order
        opponent_lineup = opponent_state["teams"][opponent_team_idx]
        lineup_parts = []
        
        if len(opponent_lineup) == 8:
            positions = EIGHTS_POSITIONS
        else:
            positions = SIXES_POSITIONS
        
        for pos in positions:
            player_data = opponent_lineup.get(pos)
            player = player_data['player'] if player_data else None
            player_display = "❔" if not player else player.display_name
            lineup_parts.append(f"`{pos}`: {player_display}")
        
        lineup_text = "\n".join(lineup_parts)
        
        # Create embed for opponent's lineup
        embed = discord.Embed(
            title=f"{opponent_name}'s Current Lineup",
            description=f"```\n{lineup_text}```",
            color=discord.Color.orange()
        )
        
        # Add subs if any
        opponent_subs = opponent_state.get("subs", [])
        if opponent_subs:
            subs_text = ", ".join(sub.display_name if hasattr(sub, 'display_name') else str(sub) for sub in opponent_subs)
            embed.add_field(name="Subs", value=subs_text, inline=False)
        
        # Add game type from challenge data
        game_type = active_challenge_data.get("game_type", "8s").upper() if len(opponent_lineup) == 8 else active_challenge_data.get("game_type", "6s").upper()
        embed.set_footer(text=f"Challenge: {game_type} • Updated in real-time")
        
        await interaction.followup.send(embed=embed)

class TeamView(View):
    def __init__(self, team_number: int):
        super().__init__(timeout=None)  # No timeout for these buttons
        self.team_number = team_number
        
        # Sign button with checkmark emoji
        sign_button = Button(
            label="Sign",
            emoji="✅",
            style=ButtonStyle.success,
            custom_id=f"sign_team{team_number}"
        )
        sign_button.callback = self.sign_callback
        self.add_item(sign_button)
        
        # Unsign button with X emoji
        unsign_button = Button(
            label="Unsign",
            emoji="❌",
            style=ButtonStyle.danger,
            custom_id=f"unsign_team{team_number}"
        )
        unsign_button.callback = self.unsign_callback
        self.add_item(unsign_button)
        
        # Sub button with swap emoji
        sub_button = Button(
            label="Sub",
            emoji="🔄",
            style=ButtonStyle.secondary,
            custom_id=f"sub_team{team_number}"
        )
        sub_button.callback = self.sub_callback
        self.add_item(sub_button)
        
        # More button with plus emoji
        more_button = Button(
            label="More",
            emoji="➕",
            style=ButtonStyle.secondary,
            custom_id=f"more_team{team_number}"
        )
        more_button.callback = self.more_callback
        self.add_item(more_button)

    async def sign_callback(self, interaction: Interaction):
        from ios_bot.commands.sign import PositionView, get_channel_context as sign_get_ctx, init_state as sign_init_state
        
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        
        state = await sign_init_state(guild_id, channel_id)
        if not state:
            await interaction.followup.send("Error: Could not get channel state for signing.", ephemeral=True)
            return
            
        channel_context = await sign_get_ctx(guild_id, channel_id)
        view = PositionView(self.team_number, guild_id, channel_id, channel_context.get("type"), state)
        await interaction.followup.send("Select which slot to sign for...", view=view, ephemeral=True)

    async def unsign_callback(self, interaction: Interaction):
        from ios_bot.commands.unsign import do_unsign
        await do_unsign(interaction, self.team_number)

    async def sub_callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        state = await sm_init_state(interaction.guild_id, interaction.channel_id)
        if not state:
            await interaction.followup.send("Error: could not get channel state for sub.", ephemeral=True)
            asyncio.create_task(delete_after_delay(interaction))
            return

        if sm_check_player_signed(state, interaction.user):
            await interaction.followup.send("❌ You are already signed to a position", ephemeral=True)
            asyncio.create_task(delete_after_delay(interaction))
            return
            
        subs = state.setdefault("subs", [])
        if interaction.user in subs:
            subs.remove(interaction.user)
            await interaction.followup.send("✅ You've been removed from subs", ephemeral=True)
        else:
            subs.append(interaction.user)
            await interaction.followup.send("✅ You've been added to subs", ephemeral=True)
            
        await sm_refresh_lineup(interaction.channel, author_override=interaction.user)
        asyncio.create_task(delete_after_delay(interaction))

    async def more_callback(self, interaction: Interaction):        
        channel_id_to_use = interaction.channel_id or (interaction.channel.id if interaction.channel else None)
        
        view = MoreOptionsView(team_number=self.team_number, channel_id=channel_id_to_use)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            "Additional options:",
            view=view,
            ephemeral=True
        ) 