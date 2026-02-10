from ios_bot.config import *
from discord.ui import View, Select, Button, Modal, InputText
import discord
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
import json as _json
import os
from zoneinfo import ZoneInfo

MAIN_GUILD_TIMEZONE = os.getenv("MAIN_GUILD_TIMEZONE", "America/New_York")
SCHEDULE_VOTE_THRESHOLD = int(os.getenv("SCHEDULE_VOTE_THRESHOLD", "4"))
SCHEDULE_VOTE_WINDOW_HOURS = int(os.getenv("SCHEDULE_VOTE_WINDOW_HOURS", "5"))
SCHEDULE_FORCE_MAIN_TIMEZONE = os.getenv("SCHEDULE_FORCE_MAIN_TIMEZONE", "1").lower() in ("1", "true", "yes", "on")


def _looks_like_steam_id(value) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    upper = raw.upper()
    if upper.startswith("STEAM_"):
        return True
    if raw.startswith("[") and raw.endswith("]") and upper.startswith("[U:"):
        return True
    return raw.isdigit() and len(raw) >= 16


def _format_standings_table(rows):
    if not rows:
        return ["```No standings available yet.```"]

    esc = "\x1b"

    def color_for_pos(pos: int) -> str:
        if pos == 1:
            return "32"  # green
        if pos == 2:
            return "33"  # yellow
        if pos == 3:
            return "37"  # white
        return "31"      # red

    team_width = min(
        max(len((r.get("team_name_snapshot") or "Team")[:20]) for r in rows),
        20
    )
    header = f"Pos {'Team'.ljust(team_width)} MP  W  D  L  GF  GA  GD  PTS"
    lines = [header]

    for idx, row in enumerate(rows, start=1):
        name = row.get("team_name_snapshot") or f"Team {row.get('guild_id')}"
        name = name[:team_width].ljust(team_width)
        mp = int(row.get("matches_played", 0))
        w = int(row.get("wins", 0))
        d = int(row.get("draws", 0))
        l = int(row.get("losses", 0))
        gf = int(row.get("goals_for", 0))
        ga = int(row.get("goals_against", 0))
        gd = int(row.get("goal_diff", 0))
        pts = int(row.get("points", 0))
        line = f"{idx:>3} {name} {mp:>2} {w:>2} {d:>2} {l:>2} {gf:>2} {ga:>2} {gd:>3} {pts:>3}"
        color = color_for_pos(idx)
        lines.append(f"{esc}[4;{color}m{line}{esc}[0m")

    chunks = []
    current = []
    for line in lines:
        current.append(line)
        if sum(len(l) + 1 for l in current) > 1800:
            chunks.append("```ansi\n" + "\n".join(current) + "\n```")
            current = []
    if current:
        chunks.append("```ansi\n" + "\n".join(current) + "\n```")
    return chunks


def _top_names(rows):
    if not rows:
        return "N/A", 0

    def _row_display_name(row):
        discord_name = row.get("discord_name")
        if discord_name:
            return str(discord_name)
        discord_id = row.get("discord_id")
        if discord_id:
            try:
                return f"<@{int(discord_id)}>"
            except Exception:
                pass
        player_name = row.get("player_name")
        if player_name and not _looks_like_steam_id(player_name):
            return str(player_name)
        return "Unlinked Player"

    top_total = rows[0].get("total", 0) or 0
    tied_names = []
    seen = set()
    for row in rows:
        if (row.get("total", 0) or 0) != top_total:
            continue
        name = _row_display_name(row)
        if not name or name in seen:
            continue
        seen.add(name)
        tied_names.append(name)

    if not tied_names:
        return "N/A", top_total

    shown = tied_names[:3]
    if len(tied_names) > 3:
        return f"{', '.join(shown)} (+{len(tied_names) - 3} tied)", top_total
    return ", ".join(shown), top_total


def _is_spanish(locale: str | None) -> bool:
    if not locale:
        return False
    return str(locale).lower().startswith("es")


def _t(locale: str | None, en: str, es: str) -> str:
    return es if _is_spanish(locale) else en


def _coerce_single_id(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.startswith("["):
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    value = parsed[0]
                else:
                    return None
            except Exception:
                pass
        try:
            return int(str(value).strip())
        except Exception:
            return None
    try:
        return int(value)
    except Exception:
        return None


def _normalize_discord_id(value) -> Optional[str]:
    """Normalize Discord-like IDs to a digit-only string."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    raw = str(value).strip()
    if not raw:
        return None
    if raw.startswith("["):
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list) and parsed:
                raw = str(parsed[0]).strip()
        except Exception:
            pass
    raw = raw.replace("<@", "").replace(">", "").replace("!", "").strip()
    if raw.isdigit():
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or None


def _same_discord_id(left, right) -> bool:
    l = _normalize_discord_id(left)
    r = _normalize_discord_id(right)
    return bool(l and r and l == r)


def _parse_time_input(raw: str) -> tuple[int, int] | None:
    if not raw:
        return None
    value = raw.strip().lower().replace(" ", "")
    am = "am" in value
    pm = "pm" in value
    value = value.replace("am", "").replace("pm", "")

    if ":" in value:
        parts = value.split(":")
        if len(parts) != 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except Exception:
            return None
    else:
        if not value.isdigit():
            return None
        if len(value) in (3, 4):
            hour = int(value[:-2])
            minute = int(value[-2:])
        else:
            hour = int(value)
            minute = 0

    if minute < 0 or minute > 59:
        return None

    if am or pm:
        if hour < 1 or hour > 12:
            return None
        if pm and hour != 12:
            hour += 12
        if am and hour == 12:
            hour = 0
    else:
        if hour < 0 or hour > 23:
            return None

    return hour, minute


def _format_time_left(expires_at: datetime | None) -> str:
    if not expires_at:
        return "N/A"
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    delta = expires_at - now
    if delta.total_seconds() <= 0:
        return "Expired"
    total_minutes = int(delta.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes}m"


def _to_utc(dt: datetime | None) -> datetime | None:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_main_timezone(dt: datetime | None) -> datetime | None:
    utc_dt = _to_utc(dt)
    if not utc_dt:
        return None
    try:
        return utc_dt.astimezone(ZoneInfo(MAIN_GUILD_TIMEZONE))
    except Exception:
        return utc_dt


def _unix_ts(dt: datetime | None) -> int:
    utc_dt = _to_utc(dt)
    if not utc_dt:
        return int(datetime.now(timezone.utc).timestamp())
    return int(utc_dt.timestamp())


def _format_main_tz_label(dt: datetime | None) -> str:
    local_dt = _to_main_timezone(dt)
    if not local_dt:
        return "Unknown time"
    # Keep this short enough for SelectOption descriptions.
    return local_dt.strftime("%b %d, %Y %I:%M %p %Z")


def _build_day_options(days: int = 14) -> list[SelectOption]:
    tz = ZoneInfo(MAIN_GUILD_TIMEZONE)
    today = datetime.now(tz).date()
    options: list[SelectOption] = []
    for i in range(days):
        day = today + timedelta(days=i)
        label = day.strftime("%a %b %d")
        options.append(SelectOption(label=label, value=day.isoformat()))
    return options[:25]


TIMEZONE_REGION_DEFS: dict[str, dict[str, Any]] = {
    "north_america": {
        "label": "US & Canada",
        "description": "United States and Canada zones",
        "zones": [
            ("America/St_Johns", "Newfoundland"),
            ("America/Halifax", "Atlantic"),
            ("America/New_York", "US Eastern (New York)"),
            ("America/Detroit", "US Eastern (Detroit)"),
            ("America/Indiana/Indianapolis", "US Eastern (Indianapolis)"),
            ("America/Chicago", "US Central (Chicago)"),
            ("America/Winnipeg", "Canada Central (Winnipeg)"),
            ("America/Regina", "Saskatchewan (Regina)"),
            ("America/Denver", "US Mountain (Denver)"),
            ("America/Phoenix", "Arizona (Phoenix)"),
            ("America/Edmonton", "Canada Mountain (Edmonton)"),
            ("America/Los_Angeles", "US Pacific (Los Angeles)"),
            ("America/Vancouver", "Canada Pacific (Vancouver)"),
            ("America/Anchorage", "Alaska (Anchorage)"),
            ("America/Adak", "Aleutian (Adak)"),
            ("Pacific/Honolulu", "Hawaii (Honolulu)"),
            ("America/Whitehorse", "Yukon (Whitehorse)"),
        ],
    },
    "central_america": {
        "label": "Central America & Caribbean",
        "description": "Mexico, Central America, Caribbean",
        "zones": [
            ("America/Tijuana", "Mexico (Tijuana)"),
            ("America/Mexico_City", "Mexico (Mexico City)"),
            ("America/Cancun", "Mexico (Cancun)"),
            ("America/Belize", "Belize"),
            ("America/Costa_Rica", "Costa Rica"),
            ("America/El_Salvador", "El Salvador"),
            ("America/Guatemala", "Guatemala"),
            ("America/Tegucigalpa", "Honduras"),
            ("America/Managua", "Nicaragua"),
            ("America/Panama", "Panama"),
            ("America/Havana", "Cuba"),
            ("America/Jamaica", "Jamaica"),
            ("America/Nassau", "Bahamas"),
            ("America/Puerto_Rico", "Puerto Rico"),
            ("America/Santo_Domingo", "Dominican Republic"),
            ("America/Port_of_Spain", "Trinidad and Tobago"),
            ("America/Barbados", "Barbados"),
            ("America/Aruba", "Aruba"),
            ("America/Curacao", "Curacao"),
            ("America/St_Thomas", "US Virgin Islands"),
            ("America/Guadeloupe", "Guadeloupe"),
            ("America/Martinique", "Martinique"),
        ],
    },
    "south_america": {
        "label": "South America",
        "description": "South American zones",
        "zones": [
            ("America/Bogota", "Colombia"),
            ("America/Lima", "Peru"),
            ("America/Caracas", "Venezuela"),
            ("America/La_Paz", "Bolivia"),
            ("America/Asuncion", "Paraguay"),
            ("America/Santiago", "Chile (Santiago)"),
            ("America/Punta_Arenas", "Chile (Punta Arenas)"),
            ("America/Montevideo", "Uruguay"),
            ("America/Argentina/Buenos_Aires", "Argentina (Buenos Aires)"),
            ("America/Sao_Paulo", "Brazil (Sao Paulo)"),
            ("America/Manaus", "Brazil (Manaus)"),
            ("America/Rio_Branco", "Brazil (Rio Branco)"),
            ("America/Noronha", "Brazil (Fernando de Noronha)"),
            ("America/Belem", "Brazil (Belem)"),
            ("America/Fortaleza", "Brazil (Fortaleza)"),
            ("America/Recife", "Brazil (Recife)"),
            ("America/Cayenne", "French Guiana"),
            ("America/Paramaribo", "Suriname"),
            ("America/Guyana", "Guyana"),
            ("America/Guayaquil", "Ecuador (Guayaquil)"),
            ("Pacific/Galapagos", "Ecuador (Galapagos)"),
            ("Pacific/Easter", "Chile (Easter Island)"),
        ],
    },
}


def _utc_offset_label(tz_name: str) -> str:
    try:
        offset = datetime.now(ZoneInfo(tz_name)).utcoffset() or timedelta()
    except Exception:
        offset = timedelta()
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _all_supported_timezones() -> set[str]:
    all_tz: set[str] = set()
    for region in TIMEZONE_REGION_DEFS.values():
        for tz_name, _ in region["zones"]:
            all_tz.add(tz_name)
    return all_tz


def _find_region_for_timezone(tz_name: str | None) -> str:
    if not tz_name:
        return "north_america"
    for key, region in TIMEZONE_REGION_DEFS.items():
        if any(zone == tz_name for zone, _ in region["zones"]):
            return key
    return "north_america"


def _build_timezone_region_options(selected_region: str) -> list[SelectOption]:
    options: list[SelectOption] = []
    for key, region in TIMEZONE_REGION_DEFS.items():
        options.append(
            SelectOption(
                label=region["label"],
                value=key,
                description=region["description"],
                default=(key == selected_region),
            )
        )
    return options


def _build_timezone_options(region_key: str, selected_timezone: str | None = None) -> list[SelectOption]:
    region = TIMEZONE_REGION_DEFS.get(region_key) or TIMEZONE_REGION_DEFS["north_america"]
    options: list[SelectOption] = []
    for tz_name, friendly in region["zones"]:
        offset = _utc_offset_label(tz_name)
        options.append(
            SelectOption(
                label=f"({offset}) {friendly}"[:100],
                value=tz_name,
                description=tz_name[:100],
                default=(tz_name == selected_timezone),
            )
        )
    return options[:25]


async def _ensure_press_channel(guild: discord.Guild, team: dict | None) -> discord.TextChannel | None:
    if not guild:
        return None
    existing_id = _coerce_single_id(team.get("press_channel_id")) if team else None
    if existing_id:
        ch = guild.get_channel(existing_id)
        if isinstance(ch, discord.TextChannel):
            return ch

    # Create or find by name
    channel = discord.utils.get(guild.text_channels, name="prensa")
    if not channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=True,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                read_message_history=True,
            ),
        }
        if team and team.get("captain_id"):
            captain_id = _coerce_single_id(_normalize_discord_id(team.get("captain_id")))
            captain = guild.get_member(captain_id) if captain_id else None
            if captain:
                overwrites[captain] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    add_reactions=True,
                    read_message_history=True,
                )
        if ADMIN_ROLE_ID:
            admin_role = guild.get_role(int(ADMIN_ROLE_ID))
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    add_reactions=True,
                    read_message_history=True,
                )

        channel = await guild.create_text_channel("prensa", overwrites=overwrites)

    if team and channel:
        await bot.db.teams.update_team_details(
            guild_id=team["guild_id"],
            press_channel_id=channel.id
        )
    return channel


def _build_schedule_embed_data(schedule: dict, time_left: str) -> dict:
    proposed_time = schedule.get("proposed_time")
    ts = _unix_ts(proposed_time)
    return {
        "home_name": schedule.get("home_name_raw") or "Home",
        "away_name": schedule.get("away_name_raw") or "Away",
        "tournament_name": schedule.get("tournament_name") or "Tournament",
        "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
        "date_str": f"<t:{ts}:D>",
        "time_str": f"<t:{ts}:t>",
        "server": schedule.get("server_name"),
        "schedule_id": schedule.get("id"),
        "proposed_by": schedule.get("proposed_by"),
        "confirmed_by": schedule.get("last_action_by") or "Pending",
        "time_left": time_left,
        "footer_datetime": f"<t:{ts}:f>",
        "footer_icon": bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None,
    }


def _schedule_embed(locale: str, data: dict, title_key: str = "confirmed") -> discord.Embed:
    is_es = _is_spanish(locale)
    title_map = {
        "confirmed": "Partido Confirmado Exitosamente" if is_es else "Match Confirmed Successfully",
        "pending": "Partido Pendiente" if is_es else "Match Pending",
        "updated": "Partido Actualizado" if is_es else "Match Updated",
        "countered": "Contra Propuesta" if is_es else "Counter Proposal",
        "cancelled": "Partido Cancelado" if is_es else "Match Cancelled",
        "declined": "Partido Rechazado" if is_es else "Match Declined",
    }
    desc_map = {
        "confirmed": "El partido ha sido registrado en el sistema" if is_es else "The match has been registered in the system",
        "pending": "El partido esta pendiente de confirmacion" if is_es else "The match is awaiting confirmation",
        "updated": "El partido ha sido actualizado" if is_es else "The match has been updated",
        "countered": "El partido tiene una contra propuesta" if is_es else "The match has a counter proposal",
        "cancelled": "El partido ha sido cancelado" if is_es else "The match has been cancelled",
        "declined": "El partido ha sido rechazado" if is_es else "The match has been declined",
    }
    color_map = {
        "confirmed": discord.Color.green(),
        "pending": discord.Color.orange(),
        "updated": discord.Color.blue(),
        "countered": discord.Color.purple(),
        "cancelled": discord.Color.red(),
        "declined": discord.Color.red(),
    }
    embed = discord.Embed(
        title=title_map.get(title_key, title_map["confirmed"]),
        description=desc_map.get(title_key, ""),
        color=color_map.get(title_key, discord.Color.orange())
    )

    def _format_actor(value):
        if value is None:
            return "N/A"
        if isinstance(value, int):
            return f"<@{value}>"
        raw = str(value).strip()
        if raw.isdigit():
            return f"<@{raw}>"
        return raw or "N/A"

    home_name = data.get("home_name", "N/A")
    away_name = data.get("away_name", "N/A")
    match_line = f"**{home_name}** vs **{away_name}**"

    embed.add_field(
        name=("Partido" if is_es else "Match"),
        value=match_line,
        inline=True,
    )
    embed.add_field(
        name=("Fecha del Torneo" if is_es else "Tournament Round"),
        value=data.get("week_label", "N/A"),
        inline=True,
    )
    embed.add_field(
        name=("Torneo" if is_es else "Tournament"),
        value=data.get("tournament_name", "N/A"),
        inline=True,
    )

    date_time = f"{data.get('date_str', 'N/A')} {data.get('time_str', '')}".strip()
    if SCHEDULE_FORCE_MAIN_TIMEZONE:
        tz_note = (
            f"Referencia: toda propuesta usa `{MAIN_GUILD_TIMEZONE}`. Discord muestra tu hora local. Guardado interno en UTC."
            if is_es
            else f"Reference: all proposals use `{MAIN_GUILD_TIMEZONE}`. Discord renders your local time. Stored internally in UTC."
        )
    else:
        tz_note = (
            "Referencia: la hora se interpreta con la zona horaria elegida por quien propone. Discord muestra tu hora local. Guardado interno en UTC."
            if is_es
            else "Reference: time is interpreted using the timezone selected by the proposer. Discord renders your local time. Stored internally in UTC."
        )
    embed.add_field(
        name=("Fecha y Hora" if is_es else "Date & Time"),
        value=f"{date_time}\n{tz_note}",
        inline=False,
    )
    embed.add_field(
        name=("Servidor" if is_es else "Server"),
        value=data.get("server", "TBD"),
        inline=False,
    )
    embed.add_field(
        name=("Propuesto por" if is_es else "Proposed by"),
        value=_format_actor(data.get("proposed_by")),
        inline=True,
    )
    embed.add_field(
        name=("Confirmado por" if is_es else "Confirmed by"),
        value=_format_actor(data.get("confirmed_by")),
        inline=True,
    )
    if data.get("time_left"):
        embed.add_field(
            name=("Tiempo restante" if is_es else "Time left to vote"),
            value=data.get("time_left"),
            inline=False,
        )

    footer_text = (
        "Hora local por usuario (base UTC)."
        if is_es
        else "Local time per viewer (UTC-based)."
    )
    footer_icon = data.get("footer_icon")
    if footer_icon:
        embed.set_footer(text=footer_text, icon_url=footer_icon)
    else:
        embed.set_footer(text=footer_text)
    return embed


async def _send_to_confirmed_channel(embed: discord.Embed) -> None:
    confirmed_channel_id = _coerce_single_id(CONFIRMED_SCHEDULE_CHANNEL_ID)
    if not confirmed_channel_id:
        try:
            from ios_bot.settings import settings
            await settings.load_guild_config(bot.db.pool)
            confirmed_channel_id = _coerce_single_id(settings.CONFIRMED_CHANNEL)
        except Exception:
            confirmed_channel_id = None
    if not confirmed_channel_id:
        return
    if not confirmed_channel_id:
        return
    ch = bot.get_channel(confirmed_channel_id)
    if not ch:
        try:
            ch = await bot.fetch_channel(confirmed_channel_id)
        except Exception:
            ch = None
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception:
            pass


async def _notify_schedule_channels(schedule: dict, embed: discord.Embed, view: Optional[View] = None, exclude_guild_id: Optional[int] = None) -> None:
    """Send schedule embed (and optional view) to home/away team channels."""
    if not schedule:
        return
    for guild_id in [schedule.get("home_guild_id"), schedule.get("away_guild_id")]:
        if not guild_id:
            continue
        if exclude_guild_id and guild_id == exclude_guild_id:
            continue
        try:
            team = await bot.db.teams.get_team(guild_id)
            if not team:
                continue
            channel_ids = (team.get("sixes_channels") or []) + (team.get("eights_channels") or []) + (team.get("fives_channels") or [])
            for ch_id in channel_ids[:1]:
                ch = bot.get_channel(ch_id)
                if ch:
                    await ch.send(embed=embed, view=view)
        except Exception:
            continue


async def _is_team_captain(user_id: int, guild_id: int) -> bool:
    team = await bot.db.teams.get_team(guild_id)
    if not team:
        return False
    return _same_discord_id(team.get("captain_id"), user_id)


async def _is_schedule_actor_allowed(user: discord.Member, schedule: dict) -> bool:
    if await _is_admin_in_main_guild(user):
        return True
    home_id = schedule.get("home_guild_id")
    away_id = schedule.get("away_guild_id")
    return (await _is_team_captain(user.id, home_id)) or (await _is_team_captain(user.id, away_id))


async def _is_admin_in_main_guild(user: discord.Member) -> bool:
    if not MAIN_GUILD_ID:
        # Fallback to current guild perms if main guild not configured
        return bool(user.guild_permissions.administrator or user.guild_permissions.manage_guild)
    main_guild = bot.get_guild(MAIN_GUILD_ID)
    if not main_guild:
        return bool(user.guild_permissions.administrator or user.guild_permissions.manage_guild)
    from ios_bot.commands.utils import fetch_member_live
    member = await fetch_member_live(main_guild, user.id)
    if not member:
        return bool(user.guild_permissions.administrator or user.guild_permissions.manage_guild)
    if main_guild.owner_id == user.id:
        return True
    if member.guild_permissions.administrator:
        return True
    admin_role = main_guild.get_role(ADMIN_ROLE_ID) if ADMIN_ROLE_ID else None
    if admin_role and admin_role in member.roles:
        return True
    return False


async def _can_manage_tournament(user: discord.Member, tournament: dict) -> bool:
    if user.guild_permissions.manage_guild:
        return True
    if tournament and tournament.get("created_by") == user.id:
        return True
    return await _is_admin_in_main_guild(user)


async def _get_captains_channel_id() -> Optional[int]:
    channel_id = _coerce_single_id(CAPTAINS_CHANNEL_ID)
    if channel_id:
        return channel_id
    try:
        from ios_bot.settings import settings
        await settings.load_guild_config(bot.db.pool)
        channel_id = _coerce_single_id(settings.CAPTAINS_CHANNEL)
        if channel_id:
            return channel_id
    except Exception:
        pass
    try:
        row = await bot.db.pool.fetchrow("SELECT captains_channel FROM main_discord LIMIT 1")
        if row:
            return _coerce_single_id(row.get("captains_channel"))
    except Exception:
        pass
    return None


async def _get_captains_channel() -> Optional[discord.TextChannel]:
    channel_id = await _get_captains_channel_id()
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        fetched = await bot.fetch_channel(channel_id)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass
    return None


async def _get_user_captain_team_ids(user_id: int) -> list[int]:
    team_ids: set[int] = set()
    normalized_user_id = _normalize_discord_id(user_id)
    if not normalized_user_id:
        return []

    # Primary path: DB-side string match to avoid fragile type assumptions.
    try:
        rows = await bot.db.pool.fetch(
            "SELECT guild_id FROM IOSCA_TEAMS WHERE CAST(captain_id AS TEXT) = $1",
            normalized_user_id,
        )
        for row in rows:
            gid = _coerce_single_id(row.get("guild_id"))
            if gid:
                team_ids.add(gid)
    except Exception:
        pass

    if team_ids:
        return sorted(team_ids)

    # Fallback for legacy/mis-typed captain_id formats.
    try:
        all_teams = await bot.db.teams.get_all_teams_with_details()
        for team in all_teams:
            if _same_discord_id(team.get("captain_id"), normalized_user_id):
                gid = _coerce_single_id(team.get("guild_id"))
                if gid:
                    team_ids.add(gid)
    except Exception:
        pass

    return sorted(team_ids)


async def _resolve_fixture_team_ids(fixture: dict) -> tuple[Optional[int], Optional[int]]:
    home_id = _coerce_single_id(fixture.get("home_guild_id"))
    away_id = _coerce_single_id(fixture.get("away_guild_id"))
    if home_id and away_id:
        return home_id, away_id

    teams = await bot.db.teams.get_all_teams()
    def _match_team_id(raw_name: str) -> Optional[int]:
        if not raw_name:
            return None
        raw = str(raw_name).strip().lower()
        if not raw:
            return None
        for team in teams:
            team_name = str(team.get("guild_name") or "").strip().lower()
            if team_name and (team_name == raw or team_name in raw or raw in team_name):
                return _coerce_single_id(team.get("guild_id"))
        return None

    if not home_id:
        home_id = _match_team_id(fixture.get("home_name_raw"))
    if not away_id:
        away_id = _match_team_id(fixture.get("away_name_raw"))
    return home_id, away_id


async def _start_match_proposal_flow(interaction: discord.Interaction, tournament_id: int) -> None:
    locale = interaction.locale
    captains_channel = await _get_captains_channel()
    if not captains_channel:
        await interaction.response.send_message(
            _t(
                locale,
                "Captains channel is not configured in `main_discord.captains_channel`.",
                "El canal de capitanes no esta configurado en `main_discord.captains_channel`.",
            ),
            ephemeral=True,
        )
        return
    if interaction.channel_id != captains_channel.id:
        await interaction.response.send_message(
            _t(
                locale,
                f"Use this command in {captains_channel.mention}.",
                f"Usa este comando en {captains_channel.mention}.",
            ),
            ephemeral=True,
        )
        return

    is_admin = await _is_admin_in_main_guild(interaction.user)
    captain_team_ids = await _get_user_captain_team_ids(interaction.user.id)
    if not is_admin and not captain_team_ids:
        await interaction.response.send_message(
            _t(
                locale,
                "Only captains can start scheduling from this channel.",
                "Solo los capitanes pueden iniciar la programacion desde este canal.",
            ),
            ephemeral=True,
        )
        return

    fixtures = []
    seen_fixture_ids = set()
    if is_admin:
        admin_fixtures = await bot.db.tournaments.get_open_fixtures(tournament_id)
        for fixture in admin_fixtures:
            fixture_id = int(fixture.get("id"))
            if fixture_id in seen_fixture_ids:
                continue
            seen_fixture_ids.add(fixture_id)
            fixtures.append(fixture)
    else:
        for team_id in captain_team_ids:
            team_fixtures = await bot.db.tournaments.get_open_fixtures_for_team(tournament_id, team_id)
            for fixture in team_fixtures:
                fixture_id = int(fixture.get("id"))
                if fixture_id in seen_fixture_ids:
                    continue
                seen_fixture_ids.add(fixture_id)
                fixtures.append(fixture)

    if not fixtures:
        await interaction.response.send_message(
            _t(
                locale,
                "No open fixtures found for your captain teams.",
                "No se encontraron jornadas abiertas para tus equipos de capitan.",
            ),
            ephemeral=True,
        )
        return

    fixture_options = []
    for f in fixtures[:25]:
        label = f"Jornada {f.get('week_number')}: {f.get('home_name_raw')} vs {f.get('away_name_raw')}"
        fixture_options.append(SelectOption(label=label[:100], value=str(f["id"])))
    fixture_select = Select(
        placeholder=_t(locale, "Select fixture...", "Selecciona la jornada..."),
        options=fixture_options
    )

    async def on_fixture_select(f_interaction: discord.Interaction):
        f_locale = f_interaction.locale
        fixture_id = int(fixture_select.values[0])
        fixture = next((f for f in fixtures if int(f.get("id")) == fixture_id), None)
        if not fixture:
            await f_interaction.response.send_message(
                _t(f_locale, "Fixture not found.", "No se encontro la jornada."),
                ephemeral=True
            )
            return

        home_id, away_id = await _resolve_fixture_team_ids(fixture)
        if not home_id or not away_id:
            await f_interaction.response.send_message(
                _t(
                    f_locale,
                    "This fixture is not mapped to both teams yet. Fix team links first.",
                    "Esta jornada aun no esta vinculada a ambos equipos. Corrige los IDs de equipo primero.",
                ),
                ephemeral=True,
            )
            return
        fixture["home_guild_id"] = home_id
        fixture["away_guild_id"] = away_id

        if not is_admin and home_id not in captain_team_ids and away_id not in captain_team_ids:
            await f_interaction.response.send_message(
                _t(
                    f_locale,
                    "You can only propose fixtures for your own team.",
                    "Solo puedes proponer jornadas para tu propio equipo.",
                ),
                ephemeral=True,
            )
            return

        servers = await bot.db.servers.get_all_servers()
        if not servers:
            await f_interaction.response.send_message(
                _t(f_locale, "No servers configured.", "No hay servidores configurados."),
                ephemeral=True
            )
            return

        view = ScheduleDayServerView(
            tournament_id,
            fixture,
            servers,
            f_interaction.user.id,
            locale=f_locale,
        )
        prompt = (
            _t(
                f_locale,
                f"Select day and server (time is interpreted as {MAIN_GUILD_TIMEZONE}).",
                f"Selecciona dia y servidor (la hora se interpreta en {MAIN_GUILD_TIMEZONE}).",
            )
            if SCHEDULE_FORCE_MAIN_TIMEZONE
            else _t(
                f_locale,
                "Select day, server, and the timezone you are entering time in:",
                "Selecciona dia, servidor y la zona horaria en la que escribes la hora:",
            )
        )
        await f_interaction.response.send_message(prompt, view=view, ephemeral=True)

    fixture_select.callback = on_fixture_select
    view = View(timeout=60)
    view.add_item(fixture_select)
    await interaction.response.send_message(
        _t(locale, "Select fixture:", "Selecciona la jornada:"),
        view=view,
        ephemeral=True,
    )


class CreateTournamentModal(Modal):
    def __init__(self, fmt: str):
        super().__init__(title="Create Tournament")
        self.fmt = fmt
        self.add_item(InputText(label="Tournament Name", placeholder="e.g. Winter Cup", required=True, max_length=255))
        self.add_item(InputText(label="Number of Teams", placeholder="e.g. 8", required=True, max_length=3))

    async def callback(self, interaction: discord.Interaction):
        name = self.children[0].value.strip()
        num_teams_raw = self.children[1].value.strip()
        try:
            num_teams = int(num_teams_raw)
            if num_teams < 2:
                raise ValueError("num_teams")
        except Exception:
            await interaction.response.send_message("❌ Number of teams must be a valid integer (>= 2).", ephemeral=True)
            return

        tournament_id = await bot.db.tournaments.create_tournament(
            name=name,
            format=self.fmt,
            num_teams=num_teams,
            created_by=interaction.user.id
        )
        if not tournament_id:
            await interaction.response.send_message("❌ Failed to create tournament.", ephemeral=True)
            return

        await interaction.response.send_message(
            "✅ Tournament created. Use `/view_tournament` to manage it.",
            ephemeral=True
        )


class TournamentTeamsSelect(View):
    def __init__(self, tournament_id: int, teams: list[dict], author_id: int, max_add: int):
        super().__init__(timeout=180)
        self.tournament_id = tournament_id
        self.teams = teams
        self.author_id = author_id
        self.max_add = max_add
        self.page = 0
        self.selected_ids = set()
        self._build_page()

    def _get_page(self):
        page_size = 25
        start = self.page * page_size
        end = start + page_size
        return self.teams[start:end]

    def _build_page(self):
        self.clear_items()
        page_items = self._get_page()
        options = []
        for team in page_items:
            options.append(SelectOption(label=team["guild_name"][:100], value=str(team["guild_id"])))
        if not options:
            options.append(SelectOption(label="No teams available", value="none"))
        select = Select(
            placeholder="Select teams to add...",
            min_values=0 if options[0].value != "none" else 1,
            max_values=min(len(options), 25),
            options=options
        )
        select.callback = self.on_select
        self.add_item(select)

        if self.page > 0:
            prev_btn = Button(label="Previous", style=ButtonStyle.secondary)
            prev_btn.callback = self.prev_page
            self.add_item(prev_btn)
        if (self.page + 1) * 25 < len(self.teams):
            next_btn = Button(label="Next", style=ButtonStyle.secondary)
            next_btn.callback = self.next_page
            self.add_item(next_btn)

        confirm_btn = Button(label="Confirm Selection", style=ButtonStyle.success)
        confirm_btn.callback = self.confirm
        self.add_item(confirm_btn)

    async def on_select(self, interaction: discord.Interaction):
        values = interaction.data.get("values", [])
        if "none" in values:
            await interaction.response.defer()
            return
        self.selected_ids |= {int(v) for v in values}
        await interaction.response.defer()

    async def prev_page(self, interaction: discord.Interaction):
        self.page -= 1
        self._build_page()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.page += 1
        self._build_page()
        await interaction.response.edit_message(view=self)

    async def confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You are not authorized to use this menu.", ephemeral=True)
            return
        if not self.selected_ids:
            await interaction.response.send_message("No teams selected.", ephemeral=True)
            return
        if len(self.selected_ids) > self.max_add:
            await interaction.response.send_message(f"Too many teams selected. You can add up to {self.max_add}.", ephemeral=True)
            return
        added = await bot.db.tournaments.add_teams(self.tournament_id, list(self.selected_ids))
        await interaction.response.edit_message(content=f"✅ Added {added} team(s) to tournament.", view=None)
        self.stop()


class TournamentMatchesSelect(View):
    def __init__(self, tournament_id: int, matches: list[dict], author_id: int):
        super().__init__(timeout=180)
        self.tournament_id = tournament_id
        self.matches = matches
        self.author_id = author_id
        self.page = 0
        self.selected_ids = set()
        self._build_page()

    def _get_page(self):
        page_size = 25
        start = self.page * page_size
        end = start + page_size
        return self.matches[start:end]

    def _build_page(self):
        self.clear_items()
        page_items = self._get_page()
        options = []
        for match in page_items:
            options.append(SelectOption(label=match["label"][:100], value=str(match["id"])))
        if not options:
            options.append(SelectOption(label="No matches available", value="none"))
        select = Select(
            placeholder="Select matches to add...",
            min_values=0 if options[0].value != "none" else 1,
            max_values=min(len(options), 25),
            options=options
        )
        select.callback = self.on_select
        self.add_item(select)

        if self.page > 0:
            prev_btn = Button(label="Previous", style=ButtonStyle.secondary)
            prev_btn.callback = self.prev_page
            self.add_item(prev_btn)
        if (self.page + 1) * 25 < len(self.matches):
            next_btn = Button(label="Next", style=ButtonStyle.secondary)
            next_btn.callback = self.next_page
            self.add_item(next_btn)

        confirm_btn = Button(label="Confirm Selection", style=ButtonStyle.success)
        confirm_btn.callback = self.confirm
        self.add_item(confirm_btn)

    async def on_select(self, interaction: discord.Interaction):
        values = interaction.data.get("values", [])
        if "none" in values:
            await interaction.response.defer()
            return
        self.selected_ids |= {int(v) for v in values}
        await interaction.response.defer()

    async def prev_page(self, interaction: discord.Interaction):
        self.page -= 1
        self._build_page()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.page += 1
        self._build_page()
        await interaction.response.edit_message(view=self)

    async def confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You are not authorized to use this menu.", ephemeral=True)
            return
        if not self.selected_ids:
            await interaction.response.send_message("No matches selected.", ephemeral=True)
            return
        added = 0
        for match_id in list(self.selected_ids):
            if await bot.db.tournaments.add_match_by_id(self.tournament_id, match_id):
                added += 1
        await interaction.response.edit_message(content=f"✅ Added {added} match(es) to tournament.", view=None)
        self.stop()


class ManageTournamentView(View):
    def __init__(self, tournament_id: int):
        super().__init__(timeout=120)
        self.tournament_id = tournament_id

    @discord.ui.button(label="End Tournament", style=discord.ButtonStyle.success)
    async def end_tournament(self, button: Button, interaction: discord.Interaction):
        if not await _is_admin_in_main_guild(interaction.user):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await bot.db.tournaments.end_tournament(self.tournament_id)
        await interaction.response.send_message("✅ Tournament ended.", ephemeral=True)

    @discord.ui.button(label="Delete Tournament", style=discord.ButtonStyle.danger)
    async def delete_tournament(self, button: Button, interaction: discord.Interaction):
        if not await _is_admin_in_main_guild(interaction.user):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        if await bot.db.tournaments.delete_tournament(self.tournament_id):
            await interaction.response.send_message("✅ Tournament deleted.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to delete tournament.", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_to_admin(self, button: Button, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Admin controls:", view=AdminControlsView(self.tournament_id))


class AdminControlsView(View):
    def __init__(self, tournament_id: int):
        super().__init__(timeout=180)
        self.tournament_id = tournament_id

    @discord.ui.button(label="Add Teams", style=discord.ButtonStyle.primary)
    async def add_teams(self, button: Button, interaction: discord.Interaction):
        tournament = await bot.db.tournaments.get_tournament(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        if not await _can_manage_tournament(interaction.user, tournament):
            await interaction.response.send_message("You are not authorized to manage this tournament.", ephemeral=True)
            return

        teams = await bot.db.teams.get_all_teams()
        existing = await bot.db.tournaments.get_tournament_team_ids(self.tournament_id)
        available = [t for t in teams if t["guild_id"] not in existing]

        remaining = max(tournament.get("num_teams", 0) - len(existing), 0)
        if remaining <= 0:
            await interaction.response.send_message("This tournament already has the maximum number of teams.", ephemeral=True)
            return

        view = TournamentTeamsSelect(self.tournament_id, available, interaction.user.id, remaining)
        await interaction.response.send_message(f"Select up to {remaining} teams:", view=view, ephemeral=True)

    @discord.ui.button(label="Add Matches", style=discord.ButtonStyle.secondary)
    async def add_matches(self, button: Button, interaction: discord.Interaction):
        tournament = await bot.db.tournaments.get_tournament(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        if not await _can_manage_tournament(interaction.user, tournament):
            await interaction.response.send_message("You are not authorized to manage this tournament.", ephemeral=True)
            return

        team_ids = await bot.db.tournaments.get_tournament_team_ids(self.tournament_id)
        team_name_map = await bot.db.tournaments._get_tournament_team_name_map(self.tournament_id)
        if not team_ids and not team_name_map:
            await interaction.response.send_message("No teams registered for this tournament.", ephemeral=True)
            return

        query = """
        SELECT m.id, m.datetime, m.home_team_name, m.away_team_name,
               m.home_guild_id, m.away_guild_id, m.home_score, m.away_score
        FROM MATCH_STATS m
        WHERE m.game_type = $1
          AND m.datetime >= $2
          AND NOT EXISTS (
              SELECT 1 FROM TOURNAMENT_MATCHES tm
              WHERE tm.tournament_id = $3 AND tm.match_stats_id = m.id
          )
        ORDER BY m.datetime DESC
        LIMIT 250
        """
        rows = await bot.db.pool.fetch(query, tournament.get("format"), tournament.get("created_at"), self.tournament_id)

        matches = []
        for r in rows:
            home_id = r["home_guild_id"]
            away_id = r["away_guild_id"]
            if home_id not in team_ids:
                home_id = bot.db.tournaments._resolve_team_id_by_name(
                    r.get("home_team_name"),
                    team_ids,
                    team_name_map,
                    threshold=0.8
                )
            if away_id not in team_ids:
                away_id = bot.db.tournaments._resolve_team_id_by_name(
                    r.get("away_team_name"),
                    team_ids,
                    team_name_map,
                    threshold=0.8
                )

            if home_id in team_ids and away_id in team_ids:
                label = f"{r['datetime'].date()} {r['home_team_name']} {r['home_score']}-{r['away_score']} {r['away_team_name']}"
                matches.append({"id": r["id"], "label": label})

        if not matches:
            await interaction.response.send_message("No eligible matches found.", ephemeral=True)
            return

        view = TournamentMatchesSelect(self.tournament_id, matches, interaction.user.id)
        await interaction.response.send_message("Select matches to add:", view=view, ephemeral=True)

    @discord.ui.button(label="Add Fixtures", style=discord.ButtonStyle.primary)
    async def add_fixtures(self, button: Button, interaction: discord.Interaction):
        tournament = await bot.db.tournaments.get_tournament(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        if not await _can_manage_tournament(interaction.user, tournament):
            await interaction.response.send_message("You are not authorized to manage this tournament.", ephemeral=True)
            return

        class FixturesModal(Modal):
            def __init__(self, tournament_id: int):
                super().__init__(title="Paste Fixtures")
                self.tournament_id = tournament_id
                self.add_item(InputText(label="Fixtures (Jornadas)", style=discord.InputTextStyle.long, required=True))

            async def callback(self, modal_interaction: discord.Interaction):
                fixtures_text = self.children[0].value
                result = await bot.db.tournaments.add_fixtures_from_text(self.tournament_id, fixtures_text)
                await modal_interaction.response.send_message(
                    f"✅ Fixtures added: {result.get('added', 0)} | Skipped: {result.get('skipped', 0)}",
                    ephemeral=True
                )

        await interaction.response.send_modal(FixturesModal(self.tournament_id))

    @discord.ui.button(label="Forfeit", style=discord.ButtonStyle.danger)
    async def add_forfeit(self, button: Button, interaction: discord.Interaction):
        tournament = await bot.db.tournaments.get_tournament(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        if not await _can_manage_tournament(interaction.user, tournament):
            await interaction.response.send_message("You are not authorized to manage this tournament.", ephemeral=True)
            return

        weeks = await bot.db.tournaments.get_week_numbers(self.tournament_id)
        if not weeks:
            await interaction.response.send_message("No fixtures found for this tournament.", ephemeral=True)
            return

        week_options = [SelectOption(label=f"Jornada {w}", value=str(w)) for w in weeks[:25]]
        week_select = Select(placeholder="Select week...", options=week_options)

        async def on_week_select(w_interaction: discord.Interaction):
            week_number = int(week_select.values[0])
            fixtures = await bot.db.tournaments.get_fixtures_for_week(self.tournament_id, week_number)
            if not fixtures:
                await w_interaction.response.send_message("No fixtures found for that week.", ephemeral=True)
                return

            fixture_options = []
            for f in fixtures:
                label = f"{f.get('home_name_raw')} vs {f.get('away_name_raw')}"
                fixture_options.append(SelectOption(label=label[:100], value=str(f["id"])))
            fixture_select = Select(placeholder="Select fixture...", options=fixture_options)

            async def on_fixture_select(f_interaction: discord.Interaction):
                fixture_id = int(fixture_select.values[0])
                fixture = next((f for f in fixtures if int(f.get("id")) == fixture_id), None)
                if not fixture:
                    await f_interaction.response.send_message("Fixture not found.", ephemeral=True)
                    return

                home_id = fixture.get("home_guild_id")
                away_id = fixture.get("away_guild_id")
                forfeit_options = [
                    SelectOption(label=f"{fixture.get('home_name_raw')} forfeits", value="home"),
                    SelectOption(label=f"{fixture.get('away_name_raw')} forfeits", value="away"),
                ]
                forfeit_select = Select(placeholder="Select forfeiting team...", options=forfeit_options)

                async def on_forfeit_select(ff_interaction: discord.Interaction):
                    choice = forfeit_select.values[0]
                    forfeiting_id = home_id if choice == "home" else away_id
                    winner_id = away_id if choice == "home" else home_id
                    ok = await bot.db.tournaments.add_forfeit(
                        tournament_id=self.tournament_id,
                        fixture_id=fixture_id,
                        forfeiting_guild_id=forfeiting_id,
                        winner_guild_id=winner_id,
                        created_by=ff_interaction.user.id
                    )
                    if ok:
                        await ff_interaction.response.send_message("✅ Forfeit recorded.", ephemeral=True)
                    else:
                        await ff_interaction.response.send_message("❌ Could not record forfeit.", ephemeral=True)

                forfeit_select.callback = on_forfeit_select
                view = View(timeout=60)
                view.add_item(forfeit_select)
                await f_interaction.response.send_message("Select forfeiting team:", view=view, ephemeral=True)

            fixture_select.callback = on_fixture_select
            view = View(timeout=60)
            view.add_item(fixture_select)
            await w_interaction.response.send_message("Select fixture:", view=view, ephemeral=True)

        week_select.callback = on_week_select
        view = View(timeout=60)
        view.add_item(week_select)
        await interaction.response.send_message("Select week for forfeit:", view=view, ephemeral=True)

    @discord.ui.button(label="Sync Matches", style=discord.ButtonStyle.primary)
    async def sync_matches(self, button: Button, interaction: discord.Interaction):
        tournament = await bot.db.tournaments.get_tournament(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        if not await _can_manage_tournament(interaction.user, tournament):
            await interaction.response.send_message("You are not authorized to manage this tournament.", ephemeral=True)
            return

        added = await bot.db.tournaments.sync_matches_for_tournament(self.tournament_id)
        await interaction.response.send_message(f"✅ Added {added} match(es).", ephemeral=True)

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.danger)
    async def manage(self, button: Button, interaction: discord.Interaction):
        if not await _is_admin_in_main_guild(interaction.user):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_message("Manage tournament:", view=ManageTournamentView(self.tournament_id), ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_to_default(self, button: Button, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Tournament controls:", view=TournamentControlView(self.tournament_id))


class FixturesPaginationView(View):
    def __init__(self, tournament_id: int, weeks: list[int], page: int = 0):
        super().__init__(timeout=180)
        self.tournament_id = tournament_id
        self.weeks = weeks
        self.page = max(0, min(page, len(weeks) - 1 if weeks else 0))
        self._sync_buttons()

    def _sync_buttons(self):
        for item in self.children:
            if isinstance(item, Button) and item.custom_id in ("fixtures_prev", "fixtures_next"):
                if item.custom_id == "fixtures_prev":
                    item.disabled = self.page <= 0
                if item.custom_id == "fixtures_next":
                    item.disabled = self.page >= len(self.weeks) - 1

    async def _build_embed(self):
        if not self.weeks:
            return discord.Embed(title="Fixtures", description="No fixtures found.", color=discord.Color.orange())
        week_number = self.weeks[self.page]
        fixtures = await bot.db.tournaments.get_fixtures_for_week(self.tournament_id, week_number)
        if not fixtures:
            desc = "No fixtures found for this week."
        else:
            lines = []
            for f in fixtures:
                home = f.get("home_name_raw") or "Home"
                away = f.get("away_name_raw") or "Away"
                lines.append(f"• {home} vs {away}")
            desc = "\n".join(lines)
        embed = discord.Embed(
            title=f"Jornada {week_number}",
            description=desc,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Week {self.page + 1} / {len(self.weeks)}")
        return embed

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, custom_id="fixtures_prev")
    async def prev_page(self, button: Button, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="fixtures_next")
    async def next_page(self, button: Button, interaction: discord.Interaction):
        self.page = min(len(self.weeks) - 1, self.page + 1)
        self._sync_buttons()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="fixtures_back")
    async def back(self, button: Button, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Tournament controls:", embed=None, view=TournamentControlView(self.tournament_id))


class TournamentControlView(View):
    def __init__(self, tournament_id: int):
        super().__init__(timeout=300)
        self.tournament_id = tournament_id

    @discord.ui.button(label="View Fixtures", style=discord.ButtonStyle.secondary)
    async def view_fixtures(self, button: Button, interaction: discord.Interaction):
        weeks = await bot.db.tournaments.get_week_numbers(self.tournament_id)
        if not weeks:
            await interaction.response.send_message("No fixtures found for this tournament.", ephemeral=True)
            return
        view = FixturesPaginationView(self.tournament_id, weeks, page=0)
        embed = await view._build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="View League Table", style=discord.ButtonStyle.secondary)
    async def view_table(self, button: Button, interaction: discord.Interaction):
        rows = await bot.db.tournaments.get_standings(self.tournament_id)
        chunks = _format_standings_table(rows)
        for idx, chunk in enumerate(chunks):
            if idx == 0:
                await interaction.response.send_message(chunk, ephemeral=False)
            else:
                await interaction.followup.send(chunk, ephemeral=False)

    @discord.ui.button(label="View Player Stats", style=discord.ButtonStyle.secondary)
    async def view_player_stats(self, button: Button, interaction: discord.Interaction):
        leaders = await bot.db.tournaments.get_player_leaders(self.tournament_id)
        top_goal_names, top_goals = _top_names(leaders.get("goals", []))
        top_assist_names, top_assists = _top_names(leaders.get("assists", []))
        top_gk_names, top_gk = _top_names(leaders.get("keeper_saves", []))
        top_def_names, top_def = _top_names(leaders.get("defenders", []))

        embed = discord.Embed(title="Tournament Player Leaders", color=discord.Color.gold())
        embed.add_field(name="Top Scorer(s)", value=f"{top_goal_names} ({top_goals})", inline=False)
        embed.add_field(name="Top Assister(s)", value=f"{top_assist_names} ({top_assists})", inline=False)
        embed.add_field(name="Top GK (Saves)", value=f"{top_gk_names} ({top_gk})", inline=False)
        embed.add_field(name="Top Defender (T+I)", value=f"{top_def_names} ({top_def})", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @discord.ui.button(label="Confirm Match", style=discord.ButtonStyle.primary)
    async def confirm_match(self, button: Button, interaction: discord.Interaction):
        await _start_match_proposal_flow(interaction, self.tournament_id)

    @discord.ui.button(label="Admin", style=discord.ButtonStyle.danger)
    async def admin_controls(self, button: Button, interaction: discord.Interaction):
        if not await _is_admin_in_main_guild(interaction.user):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_message("Admin controls:", view=AdminControlsView(self.tournament_id), ephemeral=True)


class ScheduleSelect(Select):
    def __init__(self, schedules_on_page: list[dict]):
        options = []
        for s in schedules_on_page:
            home = s.get("home_name_raw") or "Home"
            away = s.get("away_name_raw") or "Away"
            status = s.get("status") or "pending"
            dt = s.get("proposed_time")
            date_str = _format_main_tz_label(dt)
            label = f"{home} vs {away} ({status})"
            desc = f"{date_str}"
            options.append(SelectOption(label=label[:100], value=str(s.get("id")), description=desc[:100]))
        super().__init__(placeholder="Select a scheduled match / Selecciona partido...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        schedule_id = int(self.values[0])
        schedule = await bot.db.tournaments.get_schedule(schedule_id)
        if not schedule:
            await interaction.followup.send(
                _t(interaction.locale, "Schedule not found.", "No se encontro la propuesta."),
                ephemeral=True,
            )
            return
        selected_time = _unix_ts(schedule.get("proposed_time"))
        bot_name = bot.user.name if bot.user else "IOSoccer Bot"
        bot_icon = bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None
        data = {
            "home_name": schedule.get("home_name_raw") or "Home",
            "away_name": schedule.get("away_name_raw") or "Away",
            "tournament_name": schedule.get("tournament_name") or "Tournament",
            "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
            "date_str": f"<t:{selected_time}:D>",
            "time_str": f"<t:{selected_time}:t>",
            "server": schedule.get("server_name"),
            "schedule_id": schedule.get("id"),
            "proposed_by": schedule.get("proposed_by"),
            "confirmed_by": schedule.get("last_action_by") or "N/A",
            "footer_datetime": f"<t:{selected_time}:f>",
            "footer_icon": bot_icon,
        }
        title_key = "confirmed" if schedule.get("status") == "confirmed" else "countered" if schedule.get("status") == "countered" else "pending" if schedule.get("status") == "pending" else "cancelled"
        embed = _schedule_embed(interaction.locale, data, title_key=title_key)
        await interaction.edit_original_response(embed=embed)


class ScheduleListView(View):
    def __init__(self, interaction: discord.Interaction, schedules: list[dict]):
        super().__init__(timeout=None)
        self.interaction = interaction
        self.schedules = schedules
        self.page = 0
        self.page_size = 25
        self.prev_button = Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="Next", style=discord.ButtonStyle.secondary)
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page
        self.update_view()

    def update_view(self):
        self.clear_items()
        start = self.page * self.page_size
        end = start + self.page_size
        page_items = self.schedules[start:end]
        self.add_item(ScheduleSelect(page_items))
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = end >= len(self.schedules)
        self.prev_button.label = f"Page {self.page + 1}/{max(1, (len(self.schedules)-1)//self.page_size + 1)}"

    async def prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.update_view()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.page = min((len(self.schedules) - 1) // self.page_size, self.page + 1)
        self.update_view()
        await interaction.response.edit_message(view=self)


class CancelScheduleSelect(Select):
    def __init__(self, schedules_on_page: list[dict]):
        options = []
        for s in schedules_on_page:
            home = s.get("home_name_raw") or "Home"
            away = s.get("away_name_raw") or "Away"
            status = s.get("status") or "pending"
            dt = s.get("proposed_time")
            date_str = _format_main_tz_label(dt)
            label = f"{home} vs {away} ({status})"
            desc = f"{date_str}"
            options.append(SelectOption(label=label[:100], value=str(s.get("id")), description=desc[:100]))
        super().__init__(placeholder="Select a schedule to cancel / Selecciona propuesta para cancelar...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        schedule_id = int(self.values[0])
        schedule = await bot.db.tournaments.get_schedule(schedule_id)
        if not schedule:
            await interaction.followup.send(
                _t(interaction.locale, "Schedule not found.", "No se encontro la propuesta."),
                ephemeral=True,
            )
            return
        if not await _is_schedule_actor_allowed(interaction.user, schedule):
            await interaction.followup.send(
                _t(
                    interaction.locale,
                    "❌ You can only cancel schedules for your team.",
                    "❌ Solo puedes cancelar propuestas de tu equipo.",
                ),
                ephemeral=True,
            )
            return
        await bot.db.tournaments.set_schedule_status(schedule_id, "cancelled", interaction.user.id)

        selected_time = _unix_ts(schedule.get("proposed_time"))
        bot_name = bot.user.name if bot.user else "IOSoccer Bot"
        bot_icon = bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None
        data = {
            "home_name": schedule.get("home_name_raw") or "Home",
            "away_name": schedule.get("away_name_raw") or "Away",
            "tournament_name": schedule.get("tournament_name") or "Tournament",
            "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
            "date_str": f"<t:{selected_time}:D>",
            "time_str": f"<t:{selected_time}:t>",
            "server": schedule.get("server_name"),
            "schedule_id": schedule.get("id"),
            "proposed_by": schedule.get("proposed_by"),
            "confirmed_by": interaction.user.display_name,
            "footer_datetime": f"<t:{selected_time}:f>",
            "footer_icon": bot_icon,
        }
        embed = _schedule_embed(interaction.locale, data, title_key="cancelled")
        await _notify_schedule_channels(schedule, embed, view=None)
        await interaction.followup.send(
            _t(interaction.locale, "❌ Schedule cancelled.", "❌ Propuesta cancelada."),
            ephemeral=True,
        )


class CancelScheduleView(View):
    def __init__(self, interaction: discord.Interaction, schedules: list[dict]):
        super().__init__(timeout=300)
        self.interaction = interaction
        self.schedules = schedules
        self.page = 0
        self.page_size = 25
        self.prev_button = Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="Next", style=discord.ButtonStyle.secondary)
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page
        self.update_view()

    def update_view(self):
        self.clear_items()
        start = self.page * self.page_size
        end = start + self.page_size
        page_items = self.schedules[start:end]
        self.add_item(CancelScheduleSelect(page_items))
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = end >= len(self.schedules)
        self.prev_button.label = f"Page {self.page + 1}/{max(1, (len(self.schedules)-1)//self.page_size + 1)}"

    async def prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.update_view()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.page = min((len(self.schedules) - 1) // self.page_size, self.page + 1)
        self.update_view()
        await interaction.response.edit_message(view=self)


async def _get_team_captain_id(guild_id: int) -> Optional[str]:
    team = await bot.db.teams.get_team(guild_id)
    if not team:
        return None
    return _normalize_discord_id(team.get("captain_id"))


def _decode_schedule_meta(schedule: dict) -> dict:
    raw = schedule.get("proposal_message_ids") or {}
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


async def _get_schedule_thread(schedule: dict) -> Optional[discord.Thread]:
    meta = _decode_schedule_meta(schedule)
    thread_id = _coerce_single_id(meta.get("thread_id"))
    if not thread_id:
        return None
    thread = bot.get_channel(thread_id)
    if isinstance(thread, discord.Thread):
        return thread
    try:
        fetched = await bot.fetch_channel(thread_id)
        if isinstance(fetched, discord.Thread):
            return fetched
    except Exception:
        return None
    return None


async def _create_schedule_thread(schedule: dict) -> Optional[discord.Thread]:
    captains_channel = await _get_captains_channel()
    if not captains_channel:
        return None

    home_name = str(schedule.get("home_name_raw") or "Home")
    away_name = str(schedule.get("away_name_raw") or "Away")
    thread_name = f"{home_name} vs {away_name} | schedule-{schedule.get('id')}"
    thread_name = thread_name[:95]

    try:
        thread = await captains_channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440,
            invitable=False
        )
    except Exception:
        return None

    home_captain = await _get_team_captain_id(schedule.get("home_guild_id"))
    away_captain = await _get_team_captain_id(schedule.get("away_guild_id"))
    main_guild = bot.get_guild(MAIN_GUILD_ID) if MAIN_GUILD_ID else captains_channel.guild
    for captain_id in [home_captain, away_captain]:
        if not captain_id or not main_guild:
            continue
        captain_id_int = _coerce_single_id(captain_id)
        if not captain_id_int:
            continue
        try:
            member = main_guild.get_member(captain_id_int)
            if not member:
                member = await main_guild.fetch_member(captain_id_int)
            if member:
                await thread.add_user(member)
        except Exception:
            continue

    if main_guild and ADMIN_ROLE_ID:
        admin_role = main_guild.get_role(int(ADMIN_ROLE_ID))
        if admin_role:
            for admin_member in admin_role.members:
                try:
                    await thread.add_user(admin_member)
                except Exception:
                    continue

    return thread


async def _reset_schedule_proposal(schedule: dict, proposer_id: int, locale: str | None = None) -> None:
    schedule["last_action_by"] = proposer_id
    await _post_schedule_proposal(schedule, locale=locale)


async def _post_schedule_proposal(schedule: dict, locale: str | None = None) -> None:
    schedule = dict(schedule)
    thread = await _get_schedule_thread(schedule)
    if not thread:
        thread = await _create_schedule_thread(schedule)
    if not thread:
        return
    try:
        if thread.archived:
            await thread.edit(archived=False, locked=False)
    except Exception:
        pass

    selected_time = _unix_ts(schedule.get("proposed_time"))
    data = {
        "home_name": schedule.get("home_name_raw") or "Home",
        "away_name": schedule.get("away_name_raw") or "Away",
        "tournament_name": schedule.get("tournament_name") or "Tournament",
        "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
        "date_str": f"<t:{selected_time}:D>",
        "time_str": f"<t:{selected_time}:t>",
        "server": schedule.get("server_name"),
        "schedule_id": schedule.get("id"),
        "proposed_by": schedule.get("proposed_by"),
        "confirmed_by": _t(locale, "Pending", "Pendiente"),
        "footer_datetime": f"<t:{selected_time}:f>",
        "footer_icon": bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None,
    }
    title_key = "countered" if schedule.get("status") == "countered" else "pending"
    embed = _schedule_embed(locale, data, title_key=title_key)

    home_captain = await _get_team_captain_id(schedule.get("home_guild_id"))
    away_captain = await _get_team_captain_id(schedule.get("away_guild_id"))
    proposer_id = _normalize_discord_id(schedule.get("proposed_by"))
    awaiting_id = None
    if proposer_id and proposer_id == home_captain:
        awaiting_id = away_captain
    elif proposer_id and proposer_id == away_captain:
        awaiting_id = home_captain

    mention_parts = []
    if home_captain:
        mention_parts.append(f"<@{home_captain}>")
    if away_captain and away_captain != home_captain:
        mention_parts.append(f"<@{away_captain}>")
    content = " ".join(mention_parts) if mention_parts else _t(locale, "Captains", "Capitanes")
    if awaiting_id:
        waiting_text = _t(locale, "Waiting for", "Esperando respuesta de")
        content = f"{content}\n{waiting_text} <@{awaiting_id}>."

    view = CaptainScheduleDecisionView(schedule.get("id"), home_captain, away_captain, locale=locale)
    msg = await thread.send(content=content, embed=embed, view=view)
    await bot.db.tournaments.set_schedule_metadata(
        schedule.get("id"),
        None,
        {"thread_id": thread.id, "last_message_id": msg.id}
    )


async def handle_expired_schedule_proposal(schedule: dict) -> None:
    # Vote-based expiry is deprecated in centralized captain-thread workflow.
    schedule_id = _coerce_single_id(schedule.get("id") if schedule else None)
    if not schedule_id:
        return
    try:
        await bot.db.tournaments.set_schedule_metadata(
            schedule_id,
            None,
            _decode_schedule_meta(schedule or {})
        )
    except Exception:
        pass


class CaptainScheduleDecisionView(View):
    def __init__(
        self,
        schedule_id: int,
        home_captain_id: Optional[str],
        away_captain_id: Optional[str],
        locale: str | None = None,
    ):
        super().__init__(timeout=None)
        self.schedule_id = schedule_id
        self.home_captain_id = home_captain_id
        self.away_captain_id = away_captain_id
        self.locale = locale
        self.accept.label = _t(locale, "Accept", "Aceptar")
        self.decline.label = _t(locale, "Decline / Counter", "Rechazar / Contraoferta")

    async def _can_respond(self, interaction: discord.Interaction, schedule: dict) -> bool:
        if await _is_admin_in_main_guild(interaction.user):
            return True
        proposer_id = _normalize_discord_id(schedule.get("proposed_by"))
        responders = {
            _normalize_discord_id(cid)
            for cid in [self.home_captain_id, self.away_captain_id]
            if _normalize_discord_id(cid)
        }
        if proposer_id in responders and len(responders) > 1:
            responders.remove(proposer_id)
        return _normalize_discord_id(interaction.user.id) in responders

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, button: Button, interaction: discord.Interaction):
        locale = interaction.locale
        schedule = await bot.db.tournaments.get_schedule(self.schedule_id)
        if not schedule:
            await interaction.response.send_message(
                _t(locale, "Schedule not found.", "No se encontro la propuesta."),
                ephemeral=True
            )
            return
        if not await self._can_respond(interaction, schedule):
            await interaction.response.send_message(
                _t(
                    locale,
                    "Only the other captain can accept this proposal.",
                    "Solo el otro capitan puede aceptar esta propuesta.",
                ),
                ephemeral=True,
            )
            return

        await bot.db.tournaments.set_schedule_status(self.schedule_id, "confirmed", interaction.user.id)
        selected_time = _unix_ts(schedule.get("proposed_time"))
        data = {
            "home_name": schedule.get("home_name_raw") or "Home",
            "away_name": schedule.get("away_name_raw") or "Away",
            "tournament_name": schedule.get("tournament_name") or "Tournament",
            "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
            "date_str": f"<t:{selected_time}:D>",
            "time_str": f"<t:{selected_time}:t>",
            "server": schedule.get("server_name"),
            "schedule_id": self.schedule_id,
            "proposed_by": schedule.get("proposed_by"),
            "confirmed_by": interaction.user.id,
            "footer_datetime": f"<t:{selected_time}:f>",
            "footer_icon": bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None,
        }
        embed = _schedule_embed(locale, data, title_key="confirmed")
        await interaction.response.edit_message(view=None)
        try:
            await interaction.followup.send(
                _t(locale, "Schedule confirmed.", "Propuesta confirmada."),
                ephemeral=True
            )
        except Exception:
            pass

        await _send_to_confirmed_channel(embed)
        captains_channel = await _get_captains_channel()
        if captains_channel:
            await captains_channel.send(embed=embed)
        if isinstance(interaction.channel, discord.Thread):
            await interaction.channel.send(embed=embed)
            try:
                await interaction.channel.edit(archived=True, locked=True)
            except Exception:
                pass

    @discord.ui.button(label="Decline / Counter", style=discord.ButtonStyle.danger)
    async def decline(self, button: Button, interaction: discord.Interaction):
        locale = interaction.locale
        schedule = await bot.db.tournaments.get_schedule(self.schedule_id)
        if not schedule:
            await interaction.response.send_message(
                _t(locale, "Schedule not found.", "No se encontro la propuesta."),
                ephemeral=True
            )
            return
        if not await self._can_respond(interaction, schedule):
            await interaction.response.send_message(
                _t(
                    locale,
                    "Only the other captain can decline/counter this proposal.",
                    "Solo el otro capitan puede rechazar o contraofertar esta propuesta.",
                ),
                ephemeral=True,
            )
            return

        servers = await bot.db.servers.get_all_servers()
        if not servers:
            await interaction.response.send_message(
                _t(locale, "No servers configured.", "No hay servidores configurados."),
                ephemeral=True
            )
            return
        server_options = [
            SelectOption(label=str(s.get("name") or "Server"), value=str(s.get("name") or "Server"))
            for s in servers[:25]
        ]
        day_options = _build_day_options()
        view = CounterProposalView(
            schedule_id=self.schedule_id,
            home_captain_id=self.home_captain_id,
            away_captain_id=self.away_captain_id,
            author_id=interaction.user.id,
            server_options=server_options,
            day_options=day_options,
            locale=locale,
        )
        prompt = (
            _t(
                locale,
                f"Select day and server, then send your counter time in {MAIN_GUILD_TIMEZONE}.",
                f"Selecciona dia y servidor, luego envia tu contraoferta en {MAIN_GUILD_TIMEZONE}.",
            )
            if SCHEDULE_FORCE_MAIN_TIMEZONE
            else _t(
                locale,
                "Select day, server, and timezone, then send your counter time.",
                "Selecciona dia, servidor y zona horaria, luego envia tu contraoferta.",
            )
        )
        await interaction.response.send_message(prompt, view=view, ephemeral=True)


class ScheduleDayServerView(View):
    def __init__(
        self,
        tournament_id: int,
        fixture: dict,
        servers: list[dict],
        proposer_id: int,
        locale: str | None = None,
    ):
        super().__init__(timeout=300)
        self.tournament_id = tournament_id
        self.fixture = fixture
        self.proposer_id = proposer_id
        self.locale = locale

        day_options = _build_day_options()
        server_options = [
            SelectOption(label=str(s.get("name") or "Server"), value=str(s.get("name") or "Server"))
            for s in servers[:25]
        ]

        self.day_select = Select(
            placeholder=_t(locale, "Select day", "Selecciona dia"),
            options=day_options
        )
        self.server_select = Select(
            placeholder=_t(locale, "Select server", "Selecciona servidor"),
            options=server_options
        )
        initial_timezone = MAIN_GUILD_TIMEZONE if MAIN_GUILD_TIMEZONE in _all_supported_timezones() else "America/New_York"
        self.selected_region = _find_region_for_timezone(initial_timezone)
        self.selected_timezone = MAIN_GUILD_TIMEZONE if SCHEDULE_FORCE_MAIN_TIMEZONE else initial_timezone
        self.region_select = None
        self.timezone_select = None

        self.day_select.callback = self._on_day_select
        self.server_select.callback = self._on_server_select

        self.add_item(self.day_select)
        self.add_item(self.server_select)
        if not SCHEDULE_FORCE_MAIN_TIMEZONE:
            self.region_select = Select(
                placeholder=_t(locale, "Select timezone region", "Selecciona region horaria"),
                options=_build_timezone_region_options(self.selected_region)
            )
            self.timezone_select = Select(
                placeholder=_t(locale, "Select timezone used for entered time", "Selecciona zona horaria de la hora ingresada"),
                options=_build_timezone_options(self.selected_region, self.selected_timezone)
            )
            self.region_select.callback = self._on_region_select
            self.timezone_select.callback = self._on_timezone_select
            self.add_item(self.region_select)
            self.add_item(self.timezone_select)

        self.selected_day = None
        self.selected_server = None
        self.set_time.label = _t(locale, "Set time", "Confirmar hora")

    async def _on_day_select(self, interaction: discord.Interaction):
        self.selected_day = self.day_select.values[0] if self.day_select.values else None
        await interaction.response.defer()

    async def _on_server_select(self, interaction: discord.Interaction):
        self.selected_server = self.server_select.values[0] if self.server_select.values else None
        await interaction.response.defer()

    async def _on_region_select(self, interaction: discord.Interaction):
        if not self.region_select or not self.timezone_select:
            await interaction.response.defer()
            return
        self.selected_region = self.region_select.values[0] if self.region_select.values else self.selected_region
        tz_options = _build_timezone_options(self.selected_region)
        self.timezone_select.options = tz_options
        self.selected_timezone = tz_options[0].value if tz_options else MAIN_GUILD_TIMEZONE
        self.region_select.options = _build_timezone_region_options(self.selected_region)
        await interaction.response.edit_message(view=self)

    async def _on_timezone_select(self, interaction: discord.Interaction):
        if not self.timezone_select:
            await interaction.response.defer()
            return
        self.selected_timezone = self.timezone_select.values[0] if self.timezone_select.values else self.selected_timezone
        self.timezone_select.options = _build_timezone_options(self.selected_region, self.selected_timezone)
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Set time", style=discord.ButtonStyle.primary)
    async def set_time(self, button: Button, interaction: discord.Interaction):
        locale = interaction.locale
        if not self.selected_day or not self.selected_server:
            await interaction.response.send_message(
                _t(locale, "Select a day and server first.", "Primero selecciona dia y servidor."),
                ephemeral=True
            )
            return
        day_date = datetime.fromisoformat(self.selected_day).date()
        modal = ScheduleTimeModal(
            schedule_id=None,
            server_name=self.selected_server,
            day_date=day_date,
            timezone_name=self.selected_timezone,
            is_counter=False,
            tournament_id=self.tournament_id,
            fixture_id=self.fixture.get("id"),
            proposer_id=self.proposer_id,
            locale=locale,
        )
        await interaction.response.send_modal(modal)


class CounterProposalView(View):
    def __init__(
        self,
        schedule_id: int,
        home_captain_id: Optional[str],
        away_captain_id: Optional[str],
        author_id: int,
        server_options: list[SelectOption],
        day_options: list[SelectOption],
        locale: str | None = None,
    ):
        super().__init__(timeout=3600)
        self.schedule_id = schedule_id
        self.home_captain_id = home_captain_id
        self.away_captain_id = away_captain_id
        self.author_id = author_id
        self.locale = locale
        self.day_date = None

        self.server_select = Select(
            placeholder=_t(locale, "Select server", "Selecciona servidor"),
            options=server_options
        )
        self.day_select = Select(
            placeholder=_t(locale, "Select day", "Selecciona dia"),
            options=day_options
        )
        initial_timezone = MAIN_GUILD_TIMEZONE if MAIN_GUILD_TIMEZONE in _all_supported_timezones() else "America/New_York"
        self.selected_region = _find_region_for_timezone(initial_timezone)
        self.selected_timezone = MAIN_GUILD_TIMEZONE if SCHEDULE_FORCE_MAIN_TIMEZONE else initial_timezone
        self.region_select = None
        self.timezone_select = None

        self.server_select.callback = self._on_server_select
        self.day_select.callback = self._on_day_select

        self.add_item(self.day_select)
        self.add_item(self.server_select)
        if not SCHEDULE_FORCE_MAIN_TIMEZONE:
            self.region_select = Select(
                placeholder=_t(locale, "Select timezone region", "Selecciona region horaria"),
                options=_build_timezone_region_options(self.selected_region)
            )
            self.timezone_select = Select(
                placeholder=_t(locale, "Select timezone used for entered time", "Selecciona zona horaria de la hora ingresada"),
                options=_build_timezone_options(self.selected_region, self.selected_timezone)
            )
            self.region_select.callback = self._on_region_select
            self.timezone_select.callback = self._on_timezone_select
            self.add_item(self.region_select)
            self.add_item(self.timezone_select)

        self.selected_server = None
        self.selected_day = None
        self.set_time.label = _t(locale, "Set new time", "Enviar nueva hora")

    async def _on_day_select(self, interaction: discord.Interaction):
        self.selected_day = self.day_select.values[0] if self.day_select.values else None
        await interaction.response.defer()

    async def _on_server_select(self, interaction: discord.Interaction):
        self.selected_server = self.server_select.values[0] if self.server_select.values else None
        await interaction.response.defer()

    async def _on_region_select(self, interaction: discord.Interaction):
        if not self.region_select or not self.timezone_select:
            await interaction.response.defer()
            return
        self.selected_region = self.region_select.values[0] if self.region_select.values else self.selected_region
        tz_options = _build_timezone_options(self.selected_region)
        self.timezone_select.options = tz_options
        self.selected_timezone = tz_options[0].value if tz_options else MAIN_GUILD_TIMEZONE
        self.region_select.options = _build_timezone_region_options(self.selected_region)
        await interaction.response.edit_message(view=self)

    async def _on_timezone_select(self, interaction: discord.Interaction):
        if not self.timezone_select:
            await interaction.response.defer()
            return
        self.selected_timezone = self.timezone_select.values[0] if self.timezone_select.values else self.selected_timezone
        self.timezone_select.options = _build_timezone_options(self.selected_region, self.selected_timezone)
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Set new time", style=discord.ButtonStyle.primary)
    async def set_time(self, button: Button, interaction: discord.Interaction):
        locale = interaction.locale
        is_admin = await _is_admin_in_main_guild(interaction.user)
        user_id_norm = _normalize_discord_id(interaction.user.id)
        author_id_norm = _normalize_discord_id(self.author_id)
        allowed_ids = {
            _normalize_discord_id(cid)
            for cid in [self.home_captain_id, self.away_captain_id]
            if _normalize_discord_id(cid)
        }
        if not is_admin and user_id_norm != author_id_norm:
            await interaction.response.send_message(
                _t(
                    locale,
                    "Only the captain who declined can send this counter proposal.",
                    "Solo el capitan que rechazo puede enviar esta contraoferta.",
                ),
                ephemeral=True,
            )
            return
        if not is_admin and user_id_norm not in allowed_ids:
            await interaction.response.send_message(
                _t(locale, "Only captains can send counter proposals.", "Solo los capitanes pueden enviar contraofertas."),
                ephemeral=True
            )
            return
        if not self.selected_day or not self.selected_server:
            await interaction.response.send_message(
                _t(locale, "Please select a day and server first.", "Primero selecciona dia y servidor."),
                ephemeral=True
            )
            return
        self.day_date = datetime.fromisoformat(self.selected_day).date()
        modal = ScheduleTimeModal(
            schedule_id=self.schedule_id,
            server_name=self.selected_server,
            day_date=self.day_date,
            timezone_name=self.selected_timezone,
            is_counter=True,
            proposer_id=interaction.user.id,
            locale=locale,
        )
        await interaction.response.send_modal(modal)


class ScheduleTimeModal(Modal):
    def __init__(
        self,
        schedule_id: int | None,
        server_name: str,
        day_date: datetime.date,
        timezone_name: str,
        is_counter: bool = False,
        tournament_id: int | None = None,
        fixture_id: int | None = None,
        proposer_id: int | None = None,
        locale: str | None = None,
    ):
        title = (
            _t(locale, "Counter Proposal Time", "Hora de la contraoferta")
            if is_counter
            else _t(locale, "Proposal Time", "Hora de propuesta")
        )
        super().__init__(title=title)
        self.schedule_id = schedule_id
        self.server_name = server_name
        self.day_date = day_date
        self.timezone_name = timezone_name or MAIN_GUILD_TIMEZONE
        self.is_counter = is_counter
        self.tournament_id = tournament_id
        self.fixture_id = fixture_id
        self.proposer_id = proposer_id
        self.locale = locale
        self.time_input = InputText(
            label=_t(locale, "Time (e.g., 5pm, 5:30pm)", "Hora (ej: 5pm, 5:30pm)"),
            placeholder=(
                _t(locale, "5pm in", "5pm en") + f" {self.timezone_name}"
            )[:100],
            required=True
        )
        self.add_item(self.time_input)

    async def _handle_submit(self, interaction: discord.Interaction):
        locale = interaction.locale
        parsed = _parse_time_input(self.time_input.value)
        if not parsed:
            await interaction.response.send_message(
                _t(
                    locale,
                    "Invalid time format. Use 5pm, 5:30pm, 17:30, etc.",
                    "Formato de hora invalido. Usa 5pm, 5:30pm, 17:30, etc.",
                ),
                ephemeral=True
            )
            return

        if SCHEDULE_FORCE_MAIN_TIMEZONE:
            self.timezone_name = MAIN_GUILD_TIMEZONE

        try:
            tz = ZoneInfo(self.timezone_name)
        except Exception:
            tz = ZoneInfo(MAIN_GUILD_TIMEZONE)
            self.timezone_name = MAIN_GUILD_TIMEZONE

        hour, minute = parsed
        local_dt = datetime(
            self.day_date.year,
            self.day_date.month,
            self.day_date.day,
            hour,
            minute,
            tzinfo=tz
        )
        proposed_time = local_dt.astimezone(timezone.utc)
        ts = int(proposed_time.timestamp())

        if self.is_counter:
            ok = await bot.db.tournaments.update_schedule(
                self.schedule_id,
                interaction.user.id,
                proposed_time,
                self.server_name,
                "countered"
            )
            if not ok:
                await interaction.response.send_message(
                    _t(locale, "That time slot is already taken.", "Ese horario ya esta ocupado."),
                    ephemeral=True
                )
                return
            schedule = await bot.db.tournaments.get_schedule(self.schedule_id)
            if schedule:
                schedule = dict(schedule)
                await _reset_schedule_proposal(schedule, interaction.user.id, locale=locale)
                await interaction.response.send_message(
                    _t(
                        locale,
                        f"Counter proposal sent. Interpreted as <t:{ts}:F> from `{self.timezone_name}`.",
                        f"Contraoferta enviada. Interpretada como <t:{ts}:F> desde `{self.timezone_name}`.",
                    ),
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    _t(
                        locale,
                        "No open fixtures for your team.",
                        "No hay jornadas abiertas para tu equipo.",
                    ),
                    ephemeral=True
                )
            return

        if not self.tournament_id or not self.fixture_id:
            await interaction.response.send_message(
                _t(locale, "Missing fixture details.", "Faltan datos de la jornada."),
                ephemeral=True
            )
            return

        schedule_id = await bot.db.tournaments.create_schedule_proposal(
            self.tournament_id,
            self.fixture_id,
            self.proposer_id or interaction.user.id,
            proposed_time,
            self.server_name
        )
        if not schedule_id:
            await interaction.response.send_message(
                _t(locale, "That time slot is already taken.", "Ese horario ya esta ocupado."),
                ephemeral=True
            )
            return
        schedule = await bot.db.tournaments.get_schedule(schedule_id)
        if not schedule:
            await interaction.response.send_message(
                _t(locale, "Could not create schedule.", "No se pudo crear la propuesta."),
                ephemeral=True
            )
            return
        schedule = dict(schedule)
        await _post_schedule_proposal(schedule, locale=locale)
        await interaction.response.send_message(
            _t(
                locale,
                f"Proposal sent. Interpreted as <t:{ts}:F> from `{self.timezone_name}`.",
                f"Propuesta enviada. Interpretada como <t:{ts}:F> desde `{self.timezone_name}`.",
            ),
            ephemeral=True
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await self._handle_submit(interaction)
        except Exception as e:
            print(f"[SCHEDULE MODAL ERROR] {type(e).__name__}: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        _t(
                            interaction.locale,
                            "Failed to submit schedule proposal. Please try again.",
                            "No se pudo enviar la propuesta. Intentalo de nuevo.",
                        ),
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        _t(
                            interaction.locale,
                            "Failed to submit schedule proposal. Please try again.",
                            "No se pudo enviar la propuesta. Intentalo de nuevo.",
                        ),
                        ephemeral=True
                    )
            except Exception:
                pass

    async def on_submit(self, interaction: discord.Interaction):
        # Compatibility for modal implementations that call on_submit.
        await self.callback(interaction)

@bot.slash_command(name="confirm_match", description="Start match scheduling / Iniciar programacion de partido")
async def confirm_match(ctx: ApplicationContext):
    locale = getattr(ctx, "locale", None)
    if locale is None and getattr(ctx, "interaction", None):
        locale = getattr(ctx.interaction, "locale", None)
    if not ctx.guild_id:
        await ctx.respond(
            _t(
                locale,
                "Run this command inside your main guild captains channel.",
                "Ejecuta este comando dentro del canal de capitanes del servidor principal.",
            ),
            ephemeral=True,
        )
        return

    tournaments = await bot.db.tournaments.list_tournaments()
    if not tournaments:
        await ctx.respond(
            _t(locale, "No tournaments found.", "No se encontraron torneos."),
            ephemeral=True,
        )
        return

    options = [SelectOption(label=f"{t['name']}", value=str(t["id"])) for t in tournaments[:25]]
    tournament_select = Select(
        placeholder=_t(locale, "Select tournament...", "Selecciona torneo..."),
        options=options,
    )

    async def on_tournament_select(interaction: discord.Interaction):
        tournament_id = int(tournament_select.values[0])
        await _start_match_proposal_flow(interaction, tournament_id)

    tournament_select.callback = on_tournament_select
    view = View(timeout=60)
    view.add_item(tournament_select)
    await ctx.respond(
        _t(locale, "Select tournament:", "Selecciona torneo:"),
        view=view,
        ephemeral=True,
    )


@bot.slash_command(name="create_tournament", description="Create a new tournament.")
@commands.has_permissions(manage_guild=True)
async def create_tournament(
    ctx: ApplicationContext,
    format: Option(str, "Format", choices=["5v5", "6v6", "8v8"], required=True)
):
    modal = CreateTournamentModal(format)
    await ctx.send_modal(modal)


@bot.slash_command(name="view_tournament", description="View and manage tournaments.")
async def view_tournament(ctx: ApplicationContext):
    tournaments = await bot.db.tournaments.list_tournaments()
    if not tournaments:
        await ctx.respond("No tournaments found.", ephemeral=True)
        return

    options = [SelectOption(label=f"{t['name']}", value=str(t["id"])) for t in tournaments[:25]]
    select = Select(placeholder="Select a tournament...", options=options)

    async def on_select(interaction: discord.Interaction):
        tournament_id = int(select.values[0])
        tournament = await bot.db.tournaments.get_tournament(tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        embed = discord.Embed(
            title=tournament.get("name"),
            description=f"Format: **{tournament.get('format')}** | Status: **{tournament.get('status')}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="Teams", value=str(tournament.get("num_teams")), inline=True)
        await interaction.response.send_message(embed=embed, view=TournamentControlView(tournament_id), ephemeral=True)

    select.callback = on_select
    view = View(timeout=60)
    view.add_item(select)
    await ctx.respond("Select a tournament:", view=view, ephemeral=True)


@bot.slash_command(name="cancel_schedule_day", description="[ADMIN] Cancel all scheduled matches for a day.")
@commands.has_permissions(administrator=True)
async def cancel_schedule_day(ctx: ApplicationContext):
    tournaments = await bot.db.tournaments.list_tournaments()
    if not tournaments:
        await ctx.respond("No tournaments found.", ephemeral=True)
        return

    options = [SelectOption(label=f"{t['name']}", value=str(t["id"])) for t in tournaments[:25]]
    tournament_select = Select(placeholder="Select a tournament...", options=options)

    async def on_tournament_select(interaction: discord.Interaction):
        tournament_id = int(tournament_select.values[0])
        schedules = await bot.db.tournaments.list_schedules(
            tournament_id=tournament_id,
            status=None,
            limit=500
        )
        # Only show cancellable schedules; skip already-cancelled/declined entries.
        cancellable_statuses = {"pending", "countered", "confirmed"}
        schedules = [s for s in schedules if str(s.get("status") or "").lower() in cancellable_statuses]
        if not schedules:
            await interaction.response.send_message("No scheduled games found for this tournament.", ephemeral=True)
            return

        # Build unique days with counts
        day_map = {}
        for s in schedules:
            dt = s.get("proposed_time")
            if not isinstance(dt, datetime):
                continue
            day_key = dt.date().isoformat()
            day_map[day_key] = day_map.get(day_key, 0) + 1
        if not day_map:
            await interaction.response.send_message("No scheduled games found for this tournament.", ephemeral=True)
            return

        day_options = []
        for day_key, count in sorted(day_map.items()):
            day_options.append(SelectOption(
                label=f"{day_key}",
                value=day_key,
                description=f"{count} scheduled match(es)"
            ))
        day_select = Select(placeholder="Select a day (UTC)...", options=day_options[:25])

        async def on_day_select(day_interaction: discord.Interaction):
            selected_day = day_select.values[0]
            confirm_view = View(timeout=60)

            async def confirm(confirm_interaction: discord.Interaction):
                try:
                    # Keep this naive for DB timestamp columns stored as UTC without tz.
                    day = datetime.strptime(selected_day, "%Y-%m-%d")
                except Exception:
                    await confirm_interaction.response.send_message("Invalid date format.", ephemeral=True)
                    return
                count = await bot.db.tournaments.cancel_schedules_for_day(tournament_id, day)
                await confirm_interaction.response.edit_message(
                    content=f"Cancelled {count} scheduled match(es) for {day.date()}.",
                    view=None
                )
                await _send_to_confirmed_channel(discord.Embed(
                    title="Matches Cancelled",
                    description=f"All scheduled matches for {day.date()} have been cancelled by admin.",
                    color=discord.Color.red()
                ))

            async def cancel(cancel_interaction: discord.Interaction):
                await cancel_interaction.response.edit_message(content="Cancelled.", view=None)

            confirm_btn = Button(label="Confirm Cancel", style=ButtonStyle.danger)
            cancel_btn = Button(label="Back", style=ButtonStyle.secondary)
            confirm_btn.callback = confirm
            cancel_btn.callback = cancel
            confirm_view.add_item(confirm_btn)
            confirm_view.add_item(cancel_btn)

            await day_interaction.response.send_message(
                f"Cancel all schedules for **{selected_day}**?",
                view=confirm_view,
                ephemeral=True
            )

        day_select.callback = on_day_select
        view = View(timeout=60)
        view.add_item(day_select)
        await interaction.response.send_message("Select a day to cancel schedules:", view=view, ephemeral=True)

    tournament_select.callback = on_tournament_select
    view = View(timeout=60)
    view.add_item(tournament_select)
    await ctx.respond("Select a tournament:", view=view, ephemeral=True)


class ScheduleResponseView(View):
    def __init__(self, schedule_id: int):
        super().__init__(timeout=10800)  # 3 hours
        self.schedule_id = schedule_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, button: Button, interaction: discord.Interaction):
        locale = interaction.locale
        schedule = await bot.db.tournaments.get_schedule(self.schedule_id)
        if not schedule:
            await interaction.response.send_message(
                _t(locale, "Schedule not found.", "No se encontro la propuesta."),
                ephemeral=True
            )
            return
        if not await _is_schedule_actor_allowed(interaction.user, schedule):
            await interaction.response.send_message(
                _t(
                    locale,
                    "❌ Only team captains or admins can respond.",
                    "❌ Solo capitanes de equipo o administradores pueden responder.",
                ),
                ephemeral=True,
            )
            return
        await bot.db.tournaments.set_schedule_status(self.schedule_id, "confirmed", interaction.user.id)
        bot_name = bot.user.name if bot.user else "IOSoccer Bot"
        bot_icon = bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None
        data = {
            "home_name": schedule.get("home_name_raw") or "Home",
            "away_name": schedule.get("away_name_raw") or "Away",
            "tournament_name": schedule.get("tournament_name") or "Tournament",
            "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
            "date_str": f"<t:{_unix_ts(schedule.get('proposed_time'))}:D>",
            "time_str": f"<t:{_unix_ts(schedule.get('proposed_time'))}:t>",
            "server": schedule.get("server_name"),
            "schedule_id": self.schedule_id,
            "proposed_by": schedule.get("proposed_by"),
            "confirmed_by": interaction.user.display_name,
            "footer_datetime": f"<t:{_unix_ts(schedule.get('proposed_time'))}:f>",
            "footer_icon": bot_icon,
        }
        embed = _schedule_embed(locale, data, title_key="confirmed")
        await interaction.response.send_message(
            _t(locale, "✅ Confirmed.", "✅ Confirmado."),
            ephemeral=True
        )

        # announce in confirmed channel
        await _send_to_confirmed_channel(embed)
        await _notify_schedule_channels(schedule, embed, view=None)

    @discord.ui.button(label="Counter", style=discord.ButtonStyle.secondary)
    async def counter(self, button: Button, interaction: discord.Interaction):
        locale = interaction.locale
        schedule = await bot.db.tournaments.get_schedule(self.schedule_id)
        if not schedule:
            await interaction.response.send_message(
                _t(locale, "Schedule not found.", "No se encontro la propuesta."),
                ephemeral=True
            )
            return
        if not await _is_schedule_actor_allowed(interaction.user, schedule):
            await interaction.response.send_message(
                _t(
                    locale,
                    "❌ Only team captains or admins can respond.",
                    "❌ Solo capitanes de equipo o administradores pueden responder.",
                ),
                ephemeral=True,
            )
            return

        servers = await bot.db.servers.get_all_servers()
        server_options = [SelectOption(label=s.get("name", "Server"), value=s.get("name", "Server")) for s in servers[:25]]
        if not server_options:
            await interaction.response.send_message(
                _t(locale, "No servers configured.", "No hay servidores configurados."),
                ephemeral=True
            )
            return

        now = datetime.now(timezone.utc)
        slots = []
        cur = now.replace(minute=0, second=0, microsecond=0)
        for _ in range(24):
            slots.append(cur)
            cur += timedelta(hours=1)
        time_options = [
            SelectOption(
                label=_format_main_tz_label(slot),
                value=str(int(slot.timestamp()))
            )
            for slot in slots
        ]
        time_select = Select(
            placeholder=_t(locale, "Select time (hourly)", "Selecciona hora (por hora)"),
            options=time_options[:25]
        )
        server_select = Select(
            placeholder=_t(locale, "Select server", "Selecciona servidor"),
            options=server_options
        )

        async def on_time_select(ts_interaction: discord.Interaction):
            await ts_interaction.response.defer()

        async def on_server_select(ss_interaction: discord.Interaction):
            await ss_interaction.response.defer()

        time_select.callback = on_time_select
        server_select.callback = on_server_select

        async def on_submit(sub_interaction: discord.Interaction):
            sub_locale = sub_interaction.locale
            if not time_select.values or not server_select.values:
                await sub_interaction.response.send_message(
                    _t(
                        sub_locale,
                        "❌ Please select both a time and a server.",
                        "❌ Debes seleccionar hora y servidor.",
                    ),
                    ephemeral=True,
                )
                return

            selected_time = int(time_select.values[0])
            selected_server = server_select.values[0]
            proposed_time = datetime.fromtimestamp(selected_time, tz=timezone.utc)
            ok = await bot.db.tournaments.update_schedule(
                self.schedule_id, sub_interaction.user.id, proposed_time, selected_server, "countered"
            )
            if not ok:
                await sub_interaction.response.send_message(
                    _t(
                        sub_locale,
                        "❌ That time slot is already taken.",
                        "❌ Ese horario ya esta ocupado.",
                    ),
                    ephemeral=True,
                )
                return

            schedule = await bot.db.tournaments.get_schedule(self.schedule_id)
            if schedule:
                bot_name = bot.user.name if bot.user else "IOSoccer Bot"
                bot_icon = bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None
                data = {
                    "home_name": schedule.get("home_name_raw") or "Home",
                    "away_name": schedule.get("away_name_raw") or "Away",
                    "tournament_name": schedule.get("tournament_name") or "Tournament",
                    "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
                    "date_str": f"<t:{selected_time}:D>",
                    "time_str": f"<t:{selected_time}:t>",
                    "server": selected_server,
                    "schedule_id": self.schedule_id,
                    "proposed_by": sub_interaction.user.id,
                    "confirmed_by": sub_interaction.user.display_name,
                    "footer_datetime": f"<t:{selected_time}:f>",
                    "footer_icon": bot_icon,
                }
                embed = _schedule_embed(sub_interaction.locale, data, title_key="countered")
                view = ScheduleResponseView(self.schedule_id)

                # notify opponent team channel only
                await _notify_schedule_channels(
                    schedule,
                    embed,
                    view=view,
                    exclude_guild_id=sub_interaction.guild_id
                )

            await sub_interaction.response.send_message(
                _t(sub_locale, "✅ Counter proposal sent.", "✅ Contraoferta enviada."),
                ephemeral=True
            )

        view = View(timeout=120)
        view.add_item(time_select)
        view.add_item(server_select)
        submit = Button(
            label=_t(locale, "Send Counter", "Enviar contraoferta"),
            style=ButtonStyle.success
        )
        submit.callback = on_submit
        view.add_item(submit)
        await interaction.response.send_message(
            _t(locale, "Pick new time and server:", "Elige nueva hora y servidor:"),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, button: Button, interaction: discord.Interaction):
        locale = interaction.locale
        schedule = await bot.db.tournaments.get_schedule(self.schedule_id)
        if not schedule:
            await interaction.response.send_message(
                _t(locale, "Schedule not found.", "No se encontro la propuesta."),
                ephemeral=True
            )
            return
        if not await _is_schedule_actor_allowed(interaction.user, schedule):
            await interaction.response.send_message(
                _t(
                    locale,
                    "❌ Only team captains or admins can respond.",
                    "❌ Solo capitanes de equipo o administradores pueden responder.",
                ),
                ephemeral=True,
            )
            return
        await bot.db.tournaments.set_schedule_status(self.schedule_id, "cancelled", interaction.user.id)
        bot_name = bot.user.name if bot.user else "IOSoccer Bot"
        bot_icon = bot.user.display_avatar.url if bot.user and bot.user.display_avatar else None
        data = {
            "home_name": schedule.get("home_name_raw") or "Home",
            "away_name": schedule.get("away_name_raw") or "Away",
            "tournament_name": schedule.get("tournament_name") or "Tournament",
            "week_label": schedule.get("week_label") or f"Jornada {schedule.get('week_number')}",
            "date_str": f"<t:{_unix_ts(schedule.get('proposed_time'))}:D>",
            "time_str": f"<t:{_unix_ts(schedule.get('proposed_time'))}:t>",
            "server": schedule.get("server_name"),
            "schedule_id": self.schedule_id,
            "proposed_by": schedule.get("proposed_by"),
            "confirmed_by": interaction.user.display_name,
            "footer_datetime": f"<t:{_unix_ts(schedule.get('proposed_time'))}:f>",
            "footer_icon": bot_icon,
        }
        embed = _schedule_embed(locale, data, title_key="cancelled")
        await interaction.response.send_message(
            _t(locale, "❌ Schedule cancelled.", "❌ Propuesta cancelada."),
            ephemeral=True
        )
        await _notify_schedule_channels(schedule, embed, view=None)
