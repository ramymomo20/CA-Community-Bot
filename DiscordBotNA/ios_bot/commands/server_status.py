from ios_bot.config import *
from ios_bot.database_manager import get_all_servers, get_all_servers_with_details
import a2s

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
    await ctx.defer()

    # Check if user has admin permissions for detailed view
    if show_details and not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ You need administrator permissions to view detailed server information.", ephemeral=True)
        return

    if show_details:
        # Show detailed server list for admins
        embed = Embed(title="Server List", description="All servers in the database:", color=discord.Color.blue())
        
        servers = await get_all_servers_with_details()
        
        if not servers:
            await ctx.followup.send("No servers found in database.", ephemeral=True)
            return

        for server in servers:
            status = "🟢 Active" if server['is_active'] else "🔴 Inactive"
            sftp_info = ""
            if server['sftp_ip'] and server['host_username']:
                sftp_info = f"\n**SFTP:** {server['sftp_ip']} (user: {server['host_username']})"
            
            embed.add_field(
                name=f"ID: {server['id']} - {server['name']}",
                value=f"**Address:** {server['address']}\n**Status:** {status}{sftp_info}\n**Created:** {server['created_at']}\n**Updated:** {server['updated_at']}",
                inline=False
            )

        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=ctx.guild.icon)
        embed.set_footer(text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        await ctx.followup.send(embed=embed, ephemeral=True)
        return

    # Regular server status check
    embed = Embed(title="Server Status List", color=0x00ff00)
    server_statuses = []

    # Get servers from database instead of hardcoded list
    rcon_servers = await get_all_servers()
    
    if not rcon_servers:
        await ctx.respond("❌ No servers found in database. Please contact an administrator.")
        return

    async def check_server_status(server_info):
        """Check if a server is online and get player count."""
        try:
            # Extract server info
            server_link = server_info['address']
            server_address  = (server_info['address'].split(':')[0], int(server_info['address'].split(':')[1]))
            
            # Create server query object
            info = await a2s.ainfo(server_address)
            
            # Get server info
            server_name = info.server_name
            server_map = info.map_name
            server_players = info.player_count
            server_max_players = info.max_players
            is_mix_occurring = info.password_protected
            
            return {
                'name': server_name,
                'map': server_map,
                'players': server_players,
                'max_players': server_max_players,
                'is_mix': is_mix_occurring,
                'srv_link': f"{server_link}"
            }
        except Exception as e:
            print(f"Error checking server {server_info['name']}: {e}")
            return {
                'name': server_info['name'],
                'status': 'Offline',
                'players': 0,
                'max_players': 0,
                'srv_link': server_info['address']
            }

    tasks = [check_server_status(server) for server in rcon_servers]
    results = await asyncio.gather(*tasks)
    server_statuses.extend(results)

    # Sort servers by player count (descending)
    server_statuses.sort(key=lambda x: x['players'], reverse=True)

    # Add each server's status to the embed
    for status in server_statuses:
        server_info = (
            f"**Map:** `{status.get('map')}`\n"
            f"**Players:** `{status['players']} / {status['max_players']}`\n"
            f"**Type:** {'Official Mix' if status.get('is_mix', False) else 'CASUAL'}\n"
        )
        
        server_embed = embed.add_field(
            name=f"- {status['name']}",
            value=f"[Connect: {status['srv_link']}](https://iosoccer.com/connect/#{status['srv_link']})\n{server_info}",
            inline=False
        )
    
    server_embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
    server_embed.set_thumbnail(url=ctx.guild.icon)
    # Add timestamp
    embed.set_footer(text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    await ctx.respond(embed=embed) 