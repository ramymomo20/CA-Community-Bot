from ios_bot.config import *


@bot.slash_command(
    name="set_player_tier",
    description="[ADMIN] Set a player's D1/D2 loan tier (Premier/Pro), or clear it.",
)
@commands.has_permissions(administrator=True)
async def set_player_tier(
    ctx: ApplicationContext,
    player: Option(discord.Member, "The player to set a tier for", required=True),
    tier: Option(
        str,
        "Premier, Pro, or None to clear it",
        choices=["premier", "pro", "none"],
        required=True,
    ),
):
    await ctx.defer(ephemeral=True)

    record = await bot.db.players.get_player_by_discord_id(player.id)
    if not record:
        await ctx.followup.send(
            f"{player.mention} doesn't have a registered player profile.",
            ephemeral=True,
        )
        return

    tier_value = None if tier == "none" else tier
    updated = await bot.db.players.set_player_tier(player.id, tier_value)
    if not updated:
        await ctx.followup.send(f"Failed to update {player.mention}'s tier.", ephemeral=True)
        return

    if tier_value:
        await ctx.followup.send(f"✅ Set {player.mention}'s tier to **{tier_value.title()}**.", ephemeral=True)
    else:
        await ctx.followup.send(f"✅ Cleared {player.mention}'s tier.", ephemeral=True)


@set_player_tier.error
async def set_player_tier_error(ctx: ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need administrator permissions to use this command.", ephemeral=True)
    else:
        await ctx.respond(f"An unexpected error occurred: {error}", ephemeral=True)


def setup(bot):
    return
