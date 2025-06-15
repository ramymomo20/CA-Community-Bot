from ios_bot.config import *
import a2s

@bot.slash_command(
    name="server_status",
    description="Check the status of all RCON servers and their current player counts."
)
async def server_status(ctx: ApplicationContext):
    await ctx.defer()

    embed = Embed(title="Server Status List", color=0x00ff00)
    server_statuses = []

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
                'max_players': 0
            }

    tasks = [check_server_status(server) for server in RCON_SERVERS]
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