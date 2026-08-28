from datetime import datetime, timezone

from ios_bot.config import *
from ios_bot.semaphores import get_channel_semaphore
from ios_bot.signup_manager import (
    TextPlayer,
    get_channel_context,
    get_lineup_lock_reason,
    get_player_position,
    init_state,
    is_lineup_locked,
    is_text_player,
    refresh_lineup,
    update_state,
)

from .utils import delete_after_delay, move_sub_to_position, try_defer_interaction


@bot.slash_command(
    name="unsign",
    description="Remove yourself or another player from a team or subs.",
)
async def unsign_slash(
    ctx: ApplicationContext,
    target_player_specifier: Option(str, "Player to unsign (@mention, ID, or text name). Leave blank for self.", required=False) = None,
):
    await ctx.defer(ephemeral=True)
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id
    requesting_user = ctx.author

    channel_context = await get_channel_context(guild_id, channel_id)
    context_type = channel_context.get("type")

    if context_type == "not_matchmaking":
        if channel_context.get("db_error"):
            await ctx.followup.send("⚠️ The bot's database is temporarily unavailable, so this channel can't be verified right now. Please try again in a moment.", ephemeral=True)
            return
        await ctx.followup.send("This command can only be used in a registered matchmaking channel.", ephemeral=True)
        return

    async with get_channel_semaphore(channel_id):
        state = await init_state(guild_id, channel_id)
        if not state:
            await ctx.followup.send("Error: Channel state not found.", ephemeral=True)
            return

        if is_lineup_locked(state):
            await ctx.followup.send(get_lineup_lock_reason(state), ephemeral=True)
            return

        state_copy = dict(state)
        action_taken = False

        target_player_obj = requesting_user
        player_display_name = requesting_user.display_name
        is_other_player = False

        if target_player_specifier:
            is_other_player = True
            resolved_member = None
            try:
                converter = commands.MemberConverter()
                resolved_member = await converter.convert(ctx, target_player_specifier)
            except commands.MemberNotFound:
                if ctx.guild:
                    resolved_member = discord.utils.get(ctx.guild.members, name=target_player_specifier)
                    if not resolved_member:
                        resolved_member = discord.utils.get(ctx.guild.members, display_name=target_player_specifier)

            if resolved_member:
                target_player_obj = resolved_member
                player_display_name = resolved_member.display_name
                if resolved_member.id == requesting_user.id:
                    is_other_player = False
            else:
                target_player_obj = TextPlayer(target_player_specifier)
                player_display_name = target_player_specifier

        signed_team_num, signed_pos = get_player_position(state_copy, target_player_obj)

        if signed_pos:
            team_idx = signed_team_num - 1
            state_copy["teams"][team_idx][signed_pos] = None
            action_taken = True

            team_name_desc = f"Team {signed_team_num}"
            if state_copy.get("context_type") in ["team_6s", "team_8s", "team_5s"] and signed_team_num == 1:
                team_name_desc = state_copy.get("team_name", "Your Team")

            response_description = f"❌ Removed **{player_display_name}** from **{signed_pos}** on {team_name_desc}."

            try:
                moved_sub = await move_sub_to_position(state_copy, signed_pos, signed_team_num, ctx.channel)
                if moved_sub:
                    moved_sub_display = moved_sub.mention if hasattr(moved_sub, "mention") else moved_sub.name
                    response_description += f"\nMoved {moved_sub_display} from subs into {signed_pos} on {team_name_desc}."
            except Exception as e:
                print(f"Error during move_sub_to_position: {e}")

            embed = Embed(description=response_description, color=0xE74C3C)
            timestamp_now = datetime.now(timezone.utc)
            embed.set_footer(
                text=f"Requested by {requesting_user.display_name} • {timestamp_now:%I:%M %p}",
                icon_url=requesting_user.display_avatar.url if requesting_user.display_avatar else None,
            )
            await ctx.followup.send(embed=embed)

        elif not is_text_player(target_player_obj) and target_player_obj in state_copy.get("subs", []):
            state_copy["subs"].remove(target_player_obj)
            action_taken = True
            await ctx.followup.send(f"❌ {player_display_name} removed from subs.", ephemeral=True)
        else:
            await ctx.followup.send(
                f"{player_display_name} is not currently signed up for a position or as a sub.",
                ephemeral=True,
            )
            return

        if action_taken:
            update_state(channel_id, state_copy)

    await refresh_lineup(ctx.channel, force_new_message=True, author_override=requesting_user)


async def do_unsign(interaction: discord.Interaction, team_num_for_button: int = None):
    if not await try_defer_interaction(interaction, ephemeral=True):
        return

    player_to_unsign = interaction.user
    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    requesting_user = interaction.user

    channel_context = await get_channel_context(guild_id, channel_id)
    if channel_context.get("type") == "not_matchmaking":
        if channel_context.get("db_error"):
            await interaction.followup.send("⚠️ The bot's database is temporarily unavailable, so this channel can't be verified right now. Please try again in a moment.", ephemeral=True)
            return
        await interaction.followup.send("This command/button only works in matchmaking channels.", ephemeral=True)
        return

    async with get_channel_semaphore(channel_id):
        state = await init_state(guild_id, channel_id)
        if not state:
            await interaction.followup.send("Error: Channel state not found.", ephemeral=True)
            return

        if is_lineup_locked(state):
            await interaction.followup.send(get_lineup_lock_reason(state), ephemeral=True)
            return

        state_copy = dict(state)
        action_taken = False
        signed_team_num, signed_pos = get_player_position(state_copy, player_to_unsign)

        if not signed_pos:
            if player_to_unsign in state_copy.get("subs", []):
                state_copy["subs"].remove(player_to_unsign)
                action_taken = True
                await interaction.followup.send(f"❌ {player_to_unsign.display_name} removed from subs.", ephemeral=True)
                if action_taken:
                    update_state(channel_id, state_copy)
                await refresh_lineup(interaction.channel, force_new_message=True, author_override=interaction.user)
                return
            await interaction.followup.send(
                f"{player_to_unsign.display_name}, you are not currently signed up for a position or as a sub.",
                ephemeral=True,
            )
            return

        if team_num_for_button is not None and signed_team_num != team_num_for_button:
            await interaction.followup.send(
                f"You clicked unsign for Team {team_num_for_button}, but you're signed on Team {signed_team_num} ({signed_pos}).",
                ephemeral=True,
            )
            return

        state_copy["teams"][signed_team_num - 1][signed_pos] = None
        action_taken = True

        team_name_desc = f"Team {signed_team_num}"
        if state_copy.get("context_type") in ["team_6s", "team_8s", "team_5s"]:
            team_name_desc = state_copy.get("team_name", "your team")

        response_description = f"❌ Removed {interaction.user.display_name} from **{signed_pos}** on {team_name_desc}."

        try:
            moved_sub = await move_sub_to_position(state_copy, signed_pos, signed_team_num, interaction.channel)
            if moved_sub:
                moved_sub_display = moved_sub.mention if hasattr(moved_sub, "mention") else moved_sub.name
                response_description += f"\nMoved {moved_sub_display} from subs into {signed_pos} on {team_name_desc}."
        except Exception as e:
            print(f"Error during move_sub_to_position: {e}")

        embed = Embed(description=response_description, color=0xE74C3C)
        timestamp_now = datetime.now(timezone.utc)
        embed.set_footer(
            text=f"Requested by {requesting_user.display_name} • {timestamp_now:%I:%M %p}",
            icon_url=requesting_user.display_avatar.url if requesting_user.display_avatar else None,
        )
        await interaction.followup.send(embed=embed)

        if action_taken:
            update_state(channel_id, state_copy)

    await refresh_lineup(interaction.channel, force_new_message=True, author_override=interaction.user)
