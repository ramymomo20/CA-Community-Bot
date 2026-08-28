"""
RCON-based player check command to verify which signed players are actually in-game.
"""
from ios_bot.config import *
import discord
from discord import SelectOption
from discord.ui import Select, View
import re
import json
from datetime import datetime, timedelta, timezone
import asyncio
from typing import Optional

try:
    from rcon.source import Client
    RCON_AVAILABLE = True
except ImportError:
    RCON_AVAILABLE = False
    print("Warning: rcon library not installed. /check_players will not work.")

def parse_status_output(output: str):
    """Parse `status` output and return list of (name, steam_id).

    This supports common Source-style `status` lines like:
      # 2 "PlayerName" STEAM_1:0:12345 01:23 50 0 active 127.0.0.1:27005
    Falls back to other common patterns if needed.
    """
    players = []
    # Primary pattern: numbered lines starting with '#'
    primary_re = re.compile(r'^\s*#\s*\d+\s+"(?P<name>.*?)"\s+(?P<id>\S+)', re.IGNORECASE)
    for line in output.splitlines():
        m = primary_re.match(line)
        if m:
            name = m.group('name')
            steam = m.group('id')
            players.append((name, steam))

    if players:
        return players

    # Fallback pattern: lines containing name (steamid) or name <steamid>
    fallback_re = re.compile(r'(?P<name>[^\(<>\n]+)\s*[<(]?(?P<id>STEAM_[0-5]:\d+:\d+|\[U:?\d+:?\d+\]|\d{17})[)>]?', re.IGNORECASE)
    for line in output.splitlines():
        m = fallback_re.search(line)
        if m:
            name = m.group('name').strip(' "')
            steam = m.group('id')
            players.append((name, steam))

    return players



def format_steam_id(steam_id: str) -> str:
    """Convert various Steam ID formats.
    
    Handles:
    - STEAM_0:X:YYYYYYY
    - STEAM_1:X:YYYYYYY
    - [U:1:ZZZZZZZ]
    - Steam64 (17-digit number)
    """
    if not steam_id:
        return steam_id

    s = str(steam_id).strip()
    
    ID64_BASE = 76561197960265728

    STEAM_LEGACY_RE = re.compile(r'^STEAM_\d+:\d+:\d+$', re.IGNORECASE)
    STEAM3_RE = re.compile(r'^\[.*:(?P<id>\d+)\]$')
    STEAM64_RE = re.compile(r'^\d{16,20}$')

    # If already a legacy steam id, normalize universe to 0 and return
    if STEAM_LEGACY_RE.match(s):
        parts = s.split(":")  # STEAM_X:Y:Z
        # parts[0] is 'STEAM_X'
        acct_type = parts[1]
        acct_num = parts[2]
        return f"STEAM_0:{acct_type}:{acct_num}"

    # SteamID3 like: [U:1:12345] or [g:1:12345]
    m = STEAM3_RE.match(s)
    if m:
        account3 = int(m.group("id"))
        acct_type = account3 % 2
        acct_num = (account3 - acct_type) // 2
        return f"STEAM_0:{acct_type}:{acct_num}"

    # SteamID64 (numeric)
    if STEAM64_RE.match(s):
        sid64 = int(s)
        offset = sid64 - ID64_BASE
        acct_type = offset % 2
        acct_num = (offset - acct_type) // 2
        return f"STEAM_0:{acct_type}:{acct_num}"

    # Try to be permissive: detect SteamID3 variants without brackets or with different labels
    alt_m = re.search(r'(?P<id>\d{3,})$', s)
    if alt_m:
        # fallback: interpret trailing large number as account3 and convert
        val = int(alt_m.group("id"))
        acct_type = val % 2
        acct_num = (val - acct_type) // 2
        return f"STEAM_0:{acct_type}:{acct_num}"

    return steam_id


def _parse_json_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _normalize_steam_aliases(*values) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()

    for value in values:
        iterable = value if isinstance(value, (list, tuple, set)) else [value]
        for raw in iterable:
            text = str(raw or "").strip()
            if not text:
                continue
            normalized = str(format_steam_id(text) or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            aliases.append(normalized)

    return aliases


def _resolve_display_name(ctx: discord.ApplicationContext, discord_id: int, fallback: str = "") -> str:
    member = ctx.guild.get_member(discord_id) if ctx.guild else None
    if member:
        return member.display_name

    user = bot.get_user(discord_id)
    if user:
        return getattr(user, "display_name", None) or getattr(user, "name", None) or fallback or f"<@{discord_id}>"

    fallback_text = str(fallback or "").strip()
    return fallback_text or f"<@{discord_id}>"


def _format_lines(lines: list[str], *, limit: int = 12, empty_text: str = "None") -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    if not cleaned:
        return empty_text

    output: list[str] = []
    total_len = 0
    for line in cleaned:
        if len(output) >= limit:
            break
        if total_len + len(line) + 1 > 950:
            break
        output.append(line)
        total_len += len(line) + 1

    remaining = len(cleaned) - len(output)
    if remaining > 0:
        output.append(f"... and {remaining} more")

    return "\n".join(output)


async def get_ingame_players(host: str, port: int, password: str) -> list:
    """Connect to server via RCON and get list of players with Steam IDs.
    
    Returns:
        List of tuples: [(name, steam_id_steam64), ...]
    """
    if not RCON_AVAILABLE:
        return []
    
    try:
        # Run RCON in thread pool to avoid blocking
        def _rcon_query():
            with Client(host, port, passwd=password, timeout=5) as client:
                return client.run("status")
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _rcon_query)
        
        if response:
            players = parse_status_output(response)
            # Convert all Steam IDs to Steam64 format for comparison
            converted_players = []
            for name, steam_id in players:
                steamid = format_steam_id(steam_id)
                converted_players.append((name, steamid))
            return converted_players
        return []
    except Exception as e:
        print(f"RCON Error: {e}")
        return []


async def find_recent_ready_message(channel: discord.TextChannel, max_age_minutes: int = 20) -> discord.Message:
    """Find the most recent ready message in the channel.
    
    Args:
        channel: The channel to search
        max_age_minutes: Maximum age of message to consider (default 20 minutes)
    
    Returns:
        The ready message if found, None otherwise
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    
    async for message in channel.history(limit=50, after=cutoff_time, oldest_first=False):
        # Check if message is from bot or webhook and contains ready/start indicators
        if not (message.author.bot or message.webhook_id or (bot.user and message.author.id == bot.user.id)):
            continue

        content_l = (message.content or "").lower()
        if "match starting" in content_l or "match start" in content_l or "ready" in content_l or "loading" in content_l:
            return message

        if message.embeds:
            for embed in message.embeds:
                title_l = (embed.title or "").lower()
                desc_l = (embed.description or "").lower()
                author_l = (embed.author.name or "").lower() if embed.author else ""
                if (
                    "match starting" in title_l
                    or "match starting" in author_l
                    or "ready" in title_l
                    or "ready" in desc_l
                    or "loading" in title_l
                ):
                    return message
    
    return None


class ServerSelectView(View):
    """View for selecting which server to check players on."""
    
    def __init__(self, servers: list):
        super().__init__(timeout=60)
        self.selected_server = None
        
        options = []
        for server in servers:
            # Format: "ServerName (host:port)"
            label = f"{server.get('name', 'Unknown')} ({server.get('address', 'N/A')})"
            display_port = server.get('port')
            if not display_port and server.get('address') and ':' in server.get('address'):
                try:
                    display_port = int(server.get('address').split(':')[1])
                except (ValueError, IndexError):
                    display_port = None
            options.append(SelectOption(
                label=label[:100],  # Discord limit
                value=str(server.get('id')),
                description=f"Port: {display_port if display_port else 'N/A'}"[:100]
            ))
        
        self.server_select = Select(
            placeholder="Choose a server to check...",
            options=options,
            custom_id="server_select"
        )
        self.server_select.callback = self.on_server_selected
        self.add_item(self.server_select)
    
    async def on_server_selected(self, interaction: discord.Interaction):
        """Handle server selection."""
        self.selected_server = self.server_select.values[0]
        await interaction.response.defer()
        self.stop()


async def _select_server(ctx: discord.ApplicationContext) -> Optional[dict]:
    # Was a raw `SELECT * FROM IOS_SERVERS`, bypassing db.servers entirely --
    # that meant it read the password/host_password columns' encrypted
    # ciphertext directly with no decryption, which would have silently
    # broken RCON auth here once credential encryption was added.
    servers_list = await bot.db.servers.get_all_servers_with_details()

    if not servers_list:
        await ctx.followup.send("No servers configured in the database.", ephemeral=True)
        return None
    view = ServerSelectView(servers_list)
    await ctx.followup.send("Select the server to check:", view=view, ephemeral=True)
    await view.wait()

    if not view.selected_server:
        await ctx.followup.send("No server selected. Command cancelled.", ephemeral=True)
        return None

    return next((s for s in servers_list if str(s['id']) == view.selected_server), None)


async def _run_check_players(ctx: discord.ApplicationContext, ready_message: discord.Message | None, selected_server: dict) -> None:
    if not selected_server:
        await ctx.followup.send("❌ Server not found.", ephemeral=True)
        return

    address = selected_server.get("address", "")
    port = selected_server.get("port")
    password = selected_server.get("rcon_password") or selected_server.get("password", "")

    if ":" in address:
        parts = address.split(":")
        address = parts[0]
        if not port and len(parts) > 1:
            try:
                port = int(parts[1])
            except ValueError:
                pass

    if isinstance(port, str):
        try:
            port = int(port)
        except ValueError:
            port = None

    if not address or not port or not password:
        await ctx.followup.send("❌ Server configuration incomplete (missing address, port, or RCON password).", ephemeral=True)
        return

    await ctx.followup.send(f"🔍 Connecting to **{selected_server.get('name')}** via RCON...", ephemeral=True)
    ingame_players = await get_ingame_players(address, port, password)
    if not ingame_players:
        await ctx.followup.send("❌ Could not retrieve player list from server (RCON failed or no players online).", ephemeral=True)
        return

    if not ready_message:
        roster_lines = [f"• **{name}** `({steam_id})`" for name, steam_id in ingame_players[:25]]
        if len(ingame_players) > 25:
            roster_lines.append(f"... and {len(ingame_players) - 25} more")
        embed = discord.Embed(
            title="🎮 In-Server Players",
            description=f"**Server:** {selected_server.get('name')}\n**Players In-Game:** {len(ingame_players)}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Current roster",
            value="\n".join(roster_lines) if roster_lines else "No players online.",
            inline=False,
        )
        if ctx.guild and ctx.guild.icon:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        elif ctx.guild:
            embed.set_author(name=ctx.guild.name)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.channel.send(embed=embed)
        return

    age = datetime.now(timezone.utc) - ready_message.created_at
    if age > timedelta(hours=1):
        await ctx.followup.send("❌ This match is old and can't be reviewed.", ephemeral=True)
        return

    signed_players: set[int] = set()
    if ready_message.content:
        for mention in ready_message.mentions:
            signed_players.add(mention.id)
    if ready_message.embeds:
        mention_pattern = re.compile(r"<@!?(\d+)>")
        for embed in ready_message.embeds:
            for field in embed.fields:
                if not field.value:
                    continue
                for match in mention_pattern.finditer(field.value):
                    signed_players.add(int(match.group(1)))

    if not signed_players:
        await ctx.followup.send("❌ Could not find any signed player mentions in the ready message. Showing current in-server roster only.", ephemeral=True)
        roster_lines = [f"• **{name}** `({steam_id})`" for name, steam_id in ingame_players[:25]]
        if len(ingame_players) > 25:
            roster_lines.append(f"... and {len(ingame_players) - 25} more")
        embed = discord.Embed(
            title="🎮 In-Server Players",
            description=f"**Server:** {selected_server.get('name')}\n**Players In-Game:** {len(ingame_players)}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Current roster", value="\n".join(roster_lines), inline=False)
        if ctx.guild and ctx.guild.icon:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        elif ctx.guild:
            embed.set_author(name=ctx.guild.name)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.channel.send(embed=embed)
        return

    ingame_steam_ids = {steam_id for _, steam_id in ingame_players}
    discord_to_steam = {}
    players_without_steamid = []
    # One query for every signed player instead of one fetchrow per player.
    player_rows = await bot.db.pool.fetch(
        "SELECT discord_id, steam_id, discord_name FROM IOSCA_PLAYERS WHERE discord_id = ANY($1::text[])",
        [str(d) for d in signed_players],
    ) if signed_players else []
    steam_by_discord = {str(r["discord_id"]): r["steam_id"] for r in player_rows if r["steam_id"]}
    for discord_id in signed_players:
        steam_id = steam_by_discord.get(str(discord_id))
        if steam_id:
            discord_to_steam[discord_id] = steam_id
        else:
            user = bot.get_user(discord_id)
            username = user.display_name if user else f"<@{discord_id}>"
            players_without_steamid.append(username)

    missing_players = []
    for discord_id, steam_id in discord_to_steam.items():
        steamid = format_steam_id(steam_id)
        if steamid not in ingame_steam_ids:
            user = bot.get_user(discord_id)
            username = user.display_name if user else f"<@{discord_id}>"
            missing_players.append(username)

    embed = discord.Embed(
        title="🎮 Player Check Results",
        description=f"**Server:** {selected_server.get('name')}\n**Players In-Game:** {len(ingame_players)}",
        color=discord.Color.red() if missing_players or players_without_steamid else discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    if missing_players:
        embed.add_field(name="❌ Missing Players", value="\n".join([f"• {name}" for name in missing_players]), inline=False)
    else:
        embed.add_field(name="✅ All Players Present", value="All signed players with Steam IDs are in the game!", inline=False)
    if players_without_steamid:
        embed.add_field(
            name="⚠️ No Steam ID Registered",
            value="\n".join([f"• {name}" for name in players_without_steamid]),
            inline=False,
        )
    if ctx.guild and ctx.guild.icon:
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
    elif ctx.guild:
        embed.set_author(name=ctx.guild.name)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.channel.send(embed=embed)


@bot.slash_command(name="check_players", description="Check who is in server (and compare to ready message if available)")
async def check_players(
    ctx: discord.ApplicationContext,
    message_id: Option(str, "Ready message ID (optional)", required=False, default="")
):
    """Check who is in server, and compare against ready message if provided/found."""

    if not RCON_AVAILABLE:
        await ctx.respond("RCON library not installed. This command is unavailable.", ephemeral=True)
        return

    await ctx.defer()

    ready_message = None
    if message_id:
        try:
            target_id = int(message_id)
        except Exception:
            await ctx.followup.send("❌ Invalid message ID.", ephemeral=True)
            return

        try:
            ready_message = await ctx.channel.fetch_message(target_id)
        except Exception:
            ready_message = None

        if not ready_message:
            await ctx.followup.send("❌ Ready message not found in this channel. Showing current in-server roster only.", ephemeral=True)
    else:
        ready_message = await find_recent_ready_message(ctx.channel, max_age_minutes=120)
        if not ready_message:
            await ctx.followup.send("ℹ️ No recent ready message found. Showing current in-server roster only.", ephemeral=True)

    selected_server = await _select_server(ctx)
    if not selected_server:
        return

    await _run_check_players(ctx, ready_message, selected_server)


@bot.message_command(name="Check Players")
async def check_players_message(ctx: discord.ApplicationContext, message: discord.Message):
    if not RCON_AVAILABLE:
        await ctx.respond("RCON library not installed. This command is unavailable.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    selected_server = await _select_server(ctx)
    if not selected_server:
        return

    await _run_check_players(ctx, message, selected_server)


