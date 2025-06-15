from ios_bot.config import *
from ios_bot.signup_manager import get_channel_context

# --- Cooldown Management ---
request_sub_cooldowns = {}
COOLDOWN_MINUTES = 15

def check_request_sub_cooldown(channel_id: int, position: str) -> tuple[bool, int]:
    """Checks if a sub request for a specific position is on cooldown."""
    now = datetime.now(timezone.utc)
    cooldown_key = (channel_id, position)
    last_request_time = request_sub_cooldowns.get(cooldown_key)

    if not last_request_time:
        return True, 0

    time_since_last_request = now - last_request_time
    if time_since_last_request > timedelta(minutes=COOLDOWN_MINUTES):
        return True, 0

    minutes_remaining = COOLDOWN_MINUTES - int(time_since_last_request.total_seconds() / 60)
    return False, minutes_remaining

def set_request_sub_cooldown(channel_id: int, position: str):
    """Sets the cooldown for a specific position in a channel."""
    cooldown_key = (channel_id, position)
    request_sub_cooldowns[cooldown_key] = datetime.now(timezone.utc)

# --- RCON Helpers ---
# Copied from ready.py for self-containment
def get_server_status_sync(addr: str, passwd: str) -> dict:
    """Get the server status using RCON (synchronous part), including player count."""
    try:
        host, port = addr.split(":")
        port = int(port)
        with Client(host, port, passwd=passwd, timeout=2) as client:
            response = client.run("status")
            
            hostname_match = re.search(r"hostname:\s*(.+)", response)
            hostname = hostname_match.group(1).strip() if hostname_match else addr

            players_match = re.search(r"players\s*:\s*(\d+)\s+humans", response)
            players = int(players_match.group(1)) if players_match else 0
            
            max_players_match = re.search(r"players\s*:\s*\d+\s+humans,\s*\d+\s+bots,\s*(\d+)\s+max", response)
            max_players = int(max_players_match.group(1)) if max_players_match else 16

            return {"name": hostname, "players": players, "max_players": max_players, "offline": False}
    except (RconError, ConnectionRefusedError, TimeoutError):
        return {"name": addr, "players": 0, "max_players": 0, "offline": True}

async def get_server_status(addr: str, passwd: str) -> dict:
    """Get the server status using RCON, non-blocking via asyncio.to_thread."""
    loop = asyncio.get_running_loop()
    try:
        server_status_result = await loop.run_in_executor(None, get_server_status_sync, addr, passwd)
        return server_status_result
    except Exception:
        return {"name": addr, "players": 0, "max_players": 0, "offline": True}

# --- Views ---

class PositionSelectView(discord.ui.View):
    def __init__(self, server_name: str, server_addr: str):
        super().__init__(timeout=180)
        self.server_name = server_name
        self.server_addr = server_addr

        options = [discord.SelectOption(label=pos, value=pos) for pos in EIGHTS_POSITIONS]
        
        position_select = Select(
            placeholder="Select the position you need a sub for...",
            options=options,
            custom_id="request_sub_pos_select"
        )
        position_select.callback = self.on_position_selected
        self.add_item(position_select)

    async def on_position_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_position = self.children[0].values[0]
        channel_id = interaction.channel_id

        can_request, time_left = check_request_sub_cooldown(channel_id, selected_position)
        if not can_request:
            await interaction.followup.send(f"❌ A sub for **{selected_position}** has been requested recently. Please wait {time_left} more minute(s).", ephemeral=True)
            return

        set_request_sub_cooldown(channel_id, selected_position)
        
        embed = Embed(
            title=f"Sub Requested!",
            description=f"A substitute is needed for **{selected_position}** on server **{self.server_name}**.",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="🔗 Connect Info",
            value=f"Connect to [{self.server_addr}](https://iosoccer.com/connect/#{self.server_addr})",
            inline=False
        )
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        embed.timestamp = datetime.now(timezone.utc)

        await interaction.channel.send(content="@here", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
        await interaction.edit_original_response(content="✅ Your sub request has been posted.", view=None)
        self.stop()

class ServerSelectView(discord.ui.View):
    def __init__(self, server_options: list[discord.SelectOption]):
        super().__init__(timeout=180)
        
        server_select = Select(
            placeholder="Select a game server...",
            options=server_options,
            custom_id="request_sub_server_select"
        )
        server_select.callback = self.on_server_selected
        self.add_item(server_select)

    async def on_server_selected(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_server_name = self.children[0].values[0]
        
        server_details = next((s for s in RCON_SERVERS if s.get('name') == selected_server_name), None)
        if not server_details:
            await interaction.followup.send("❌ Error: Could not find details for the selected server.", ephemeral=True)
            return

        await interaction.edit_original_response(content="Now, select the position you need a sub for.", view=PositionSelectView(server_name=selected_server_name, server_addr=server_details['address']))
        self.stop()

# --- Command ---

@bot.slash_command(
    name="request_sub",
    description="Request a substitute for a specific position in a server."
)
async def request_sub(ctx: ApplicationContext):
    channel_context = await get_channel_context(ctx.guild_id, ctx.channel_id)
    if channel_context.get("type") == "not_matchmaking":
        await ctx.respond("❌ This command can only be used in a registered matchmaking channel.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    tasks = [get_server_status(s['address'], s['password']) for s in RCON_SERVERS]
    results = await asyncio.gather(*tasks)

    options = []
    for i, s_config in enumerate(RCON_SERVERS):
        status = results[i]
        if not status.get("offline"):
            label = f"{s_config['name']} ({status['players']}/{status['max_players']})"
            description = "Server is online."
            options.append(discord.SelectOption(
                label=label,
                value=s_config['name'],
                description=description
            ))
    
    if not options:
        await ctx.followup.send("❌ There are no online servers available to request a sub for.", ephemeral=True)
        return

    view = ServerSelectView(server_options=options)
    await ctx.followup.send("First, select the server where you need a substitute.", view=view, ephemeral=True) 