from ios_bot.config import *
import re


def _parse_custom_emoji_input(raw: str) -> tuple[int | None, str | None, str]:
    value = str(raw or "").strip()
    if not value:
        return None, None, ""

    mention = re.fullmatch(r"<a?:([A-Za-z0-9_]+):(\d+)>", value)
    if mention:
        return int(mention.group(2)), mention.group(1), value

    if value.isdigit():
        return int(value), None, value

    return None, None, value


def _format_asset_line(asset: dict) -> str:
    asset_type = str(asset.get("asset_type") or "").lower()
    key = str(asset.get("asset_key") or "")
    discord_id = asset.get("discord_id")
    asset_name = asset.get("asset_name") or "n/a"
    raw_value = asset.get("raw_value") or ""

    if asset_type == "role":
        target = f"<@&{discord_id}>" if discord_id else "n/a"
    else:
        if raw_value:
            target = raw_value
        elif discord_id:
            target = f"`{discord_id}`"
        else:
            target = "n/a"
    return f"`{key}` -> {target} ({asset_name})"


@bot.slash_command(
    name="server_assets",
    description="[ADMIN] Manage guild role/emoji assets."
)
@commands.has_permissions(administrator=True)
async def server_assets_command(
    ctx: discord.ApplicationContext,
    action: Option(str, "Action", choices=["view", "add", "delete"]),
    asset_type: Option(str, "Asset type", choices=["role", "emoji"], required=False, default=""),
    asset_key: Option(str, "Logical key (e.g. d1_role, goal_emoji)", required=False, default=""),
    role: Option(discord.Role, "Role asset value (for role type)", required=False, default=None),
    emoji: Option(str, "Emoji value (custom emoji mention or ID)", required=False, default=""),
):
    if not ctx.guild_id:
        await ctx.respond("This command must be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    action_value = str(action or "").lower().strip()
    type_value = str(asset_type or "").lower().strip()
    key_value = str(asset_key or "").lower().strip()

    if action_value == "view":
        assets = await bot.db.server_assets.list_assets(
            int(ctx.guild_id),
            asset_type=type_value or None,
        )
        if not assets:
            await ctx.followup.send("No server assets found for this guild.", ephemeral=True)
            return

        lines = [_format_asset_line(item) for item in assets[:50]]
        if len(assets) > 50:
            lines.append(f"... and {len(assets) - 50} more")
        embed = discord.Embed(
            title="Server Assets",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.followup.send(embed=embed, ephemeral=True)
        return

    if not type_value or not key_value:
        await ctx.followup.send(
            "For add/delete you must provide both `asset_type` and `asset_key`.",
            ephemeral=True,
        )
        return

    if action_value == "delete":
        deleted = await bot.db.server_assets.delete_asset(int(ctx.guild_id), type_value, key_value)
        if not deleted:
            await ctx.followup.send(
                f"No asset deleted for `{type_value}:{key_value}` (not found).",
                ephemeral=True,
            )
            return
        await ctx.followup.send(
            f"Deleted asset `{type_value}:{key_value}`.",
            ephemeral=True,
        )
        return

    if action_value == "add":
        discord_id = None
        asset_name = None
        raw_value = None

        if type_value == "role":
            if role is None:
                await ctx.followup.send(
                    "For `asset_type=role`, provide the `role` option.",
                    ephemeral=True,
                )
                return
            discord_id = str(role.id)
            asset_name = role.name
            raw_value = role.mention
        else:
            emoji_id, emoji_name, parsed_raw = _parse_custom_emoji_input(emoji)
            if emoji_id is None:
                await ctx.followup.send(
                    "For `asset_type=emoji`, provide a custom emoji mention like `<:goal:123...>` or a numeric emoji ID.",
                    ephemeral=True,
                )
                return
            discord_id = str(emoji_id)
            raw_value = parsed_raw
            asset_name = emoji_name
            if not asset_name and ctx.guild:
                guild_emoji = discord.utils.get(ctx.guild.emojis, id=int(discord_id))
                if guild_emoji:
                    asset_name = guild_emoji.name

        saved = await bot.db.server_assets.upsert_asset(
            guild_id=int(ctx.guild_id),
            asset_type=type_value,
            asset_key=key_value,
            discord_id=discord_id,
            asset_name=asset_name,
            raw_value=raw_value,
            created_by=int(ctx.user.id),
        )
        if not saved:
            await ctx.followup.send("Failed to save server asset.", ephemeral=True)
            return

        await ctx.followup.send(
            f"Saved asset `{type_value}:{key_value}` -> {_format_asset_line(saved)}",
            ephemeral=True,
        )
        return

    await ctx.followup.send("Unsupported action.", ephemeral=True)


@server_assets_command.error
async def server_assets_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You need Administrator permissions to use this command.", ephemeral=True)
    else:
        await ctx.respond(f"An error occurred: {error}", ephemeral=True)
