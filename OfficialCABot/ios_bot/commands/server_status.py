from ios_bot.config import *
import a2s

from ios_bot.commands.request_sub import get_server_status


@bot.slash_command(
    name="server_status",
    description="Check the status of all RCON servers and their current player counts."
)
async def server_status(
    ctx: ApplicationContext,
    show_details: bool = Option(
        description="Show detailed server information (admin only)",
        default=False,
        required=False
    )
):
    try:
        await ctx.defer()
    except discord.NotFound:
        return
    except discord.HTTPException as e:
        if getattr(e, "code", None) == 10062:
            return
        raise

    if show_details and not ctx.author.guild_permissions.administrator:
        await ctx.followup.send(
            "❌ You need administrator permissions to view detailed server information.",
            ephemeral=True
        )
        return

    if show_details:
        embed = Embed(title="Server List", description="All active servers in the database:", color=discord.Color.blue())
        servers = await bot.db.servers.get_all_servers_with_details()

        if not servers:
            await ctx.followup.send("No servers found in database.", ephemeral=True)
            return

        for server in servers:
            status = "🟢 Active" if server["is_active"] else "🔴 Inactive"
            sftp_info = ""
            if server["sftp_ip"] and server["host_username"]:
                sftp_info = f"\n**SFTP:** {server['sftp_ip']} (user: {server['host_username']})"

            embed.add_field(
                name=f"ID: {server['id']} - {server['name']}",
                value=(
                    f"**Address:** {server['address']}\n"
                    f"**Status:** {status}{sftp_info}\n"
                    f"**Created:** {server['created_at']}\n"
                    f"**Updated:** {server['updated_at']}"
                ),
                inline=False
            )

        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        if ctx.guild and ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_footer(text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        await ctx.followup.send(embed=embed, ephemeral=True)
        return

    embed = Embed(title="Server Status List", color=0x00FF00)
    rcon_servers = await bot.db.servers.get_all_servers_with_details()

    if not rcon_servers:
        await ctx.followup.send("❌ No servers found in database. Please contact an administrator.")
        return

    async def check_server_status(server_info):
        server_link = server_info["address"]
        host, port = server_link.split(":")
        server_address = (host, int(port))

        rcon_status = await get_server_status(server_link, server_info["password"])
        query_online = False
        server_name = server_info["name"]
        server_map = "Unknown"
        query_players = 0
        query_max_players = 0
        is_mix_occurring = False

        try:
            info = await a2s.ainfo(server_address)
            query_online = True
            server_name = info.server_name or server_name
            server_map = info.map_name or server_map
            query_players = int(info.player_count or 0)
            query_max_players = int(info.max_players or 0)
            is_mix_occurring = bool(info.password_protected)
        except Exception:
            pass

        rcon_online = not bool(rcon_status.get("offline"))
        players = int(rcon_status.get("players") or query_players or 0)
        max_players = int(rcon_status.get("max_players") or query_max_players or 0)
        if rcon_online and rcon_status.get("name"):
            server_name = rcon_status["name"]

        if rcon_online:
            readiness = "Ready" if players <= 8 else "Busy"
            availability = "RCON OK"
        elif query_online:
            readiness = "Unavailable"
            availability = "Query only"
        else:
            readiness = "Offline"
            availability = "Offline"

        return {
            "name": server_name,
            "map": server_map,
            "players": players,
            "max_players": max_players,
            "is_mix": is_mix_occurring,
            "srv_link": server_link,
            "availability": availability,
            "readiness": readiness,
        }

    results = await asyncio.gather(*(check_server_status(server) for server in rcon_servers))
    results.sort(key=lambda x: x["players"], reverse=True)

    for status in results:
        server_info = (
            f"**Map:** `{status.get('map', 'Unknown')}`\n"
            f"**Players:** `{status['players']} / {status['max_players']}`\n"
            f"**Availability:** `{status['availability']}`\n"
            f"**Ready Status:** `{status['readiness']}`\n"
            f"**Type:** `{'Official Mix' if status.get('is_mix', False) else 'CASUAL'}`\n"
        )
        embed.add_field(
            name=f"- {status['name']}",
            value=f"[Connect: {status['srv_link']}](https://iosoccer.com/connect/#{status['srv_link']})\n{server_info}",
            inline=False
        )

    embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
    if ctx.guild and ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)
    embed.set_footer(text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    await ctx.followup.send(embed=embed)
