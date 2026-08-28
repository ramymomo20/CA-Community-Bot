from ios_bot.config import *

from .utils import build_player_registration_url


@bot.slash_command(
    name="player_register",
    description="Start hub account linking for your player profile.",
)
async def register_me(ctx: ApplicationContext):
    await ctx.defer(ephemeral=True)

    try:
        status = await bot.db.players.get_registration_link_status(ctx.user.id)
        if status.get("linked"):
            await ctx.followup.send(
                "Your Discord account already appears linked to a player record. If you need to add a new Steam account, sign into the hub and use Link Steam / Smurf from your account page.",
                ephemeral=True,
            )
            return

        token = await bot.db.players.create_registration_intent(
            discord_id=ctx.user.id,
            discord_name=ctx.user.display_name,
            guild_id=ctx.guild_id,
        )
        registration_url = build_player_registration_url(token)
    except Exception as e:
        await ctx.followup.send(f"Failed to create registration link: {e}", ephemeral=True)
        return

    dm_status = "I also sent this link to your DMs."
    try:
        await ctx.user.send(
            "Use this hub link to sign in with Discord and Steam, then finish linking your player profile:\n"
            f"{registration_url}"
        )
    except Exception:
        dm_status = "I could not DM you, so use the link below directly."

    await ctx.followup.send(
        "Open this hub link to link your Discord and Steam accounts to your player record.\n"
        f"{registration_url}\n\n{dm_status}",
        ephemeral=True,
    )


@bot.slash_command(
    name="link_alt_steam",
    description="Admin: link a secondary Steam ID to an existing player account.",
)
@commands.has_permissions(administrator=True)
async def link_alt_steam(
    ctx: ApplicationContext,
    secondary_steam_id: Option(str, "Secondary Steam ID to link"),
    player: Option(discord.Member, "Target player by Discord account", required=False, default=None),
    primary_steam_id: Option(str, "Target primary Steam ID (if not using Discord user)", required=False, default=None),
):
    await ctx.defer(ephemeral=True)

    if not player and not primary_steam_id:
        await ctx.followup.send(
            "Provide either a Discord user or a primary Steam ID.",
            ephemeral=True,
        )
        return

    try:
        ok, message = await bot.db.players.link_secondary_steam_id(
            secondary_steam_id=str(secondary_steam_id or "").strip(),
            primary_steam_id=str(primary_steam_id).strip() if primary_steam_id else None,
            discord_id=player.id if player else None,
        )
    except Exception as e:
        await ctx.followup.send(f"Failed to link Steam ID: {e}", ephemeral=True)
        return

    await ctx.followup.send(
        f"{'✅' if ok else '❌'} {message}",
        ephemeral=True,
    )
