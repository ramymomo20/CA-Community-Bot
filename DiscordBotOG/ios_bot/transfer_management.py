from ios_bot.config import *
from ios_bot.database_manager import execute_query, get_team, get_all_teams_with_details
from ios_bot.announcements import send_announcement
from datetime import datetime, timedelta
import difflib

# === TRANSFER WINDOW MANAGEMENT ===

async def get_transfer_window_status():
    """Get current transfer window status."""
    result = await execute_query(
        "SELECT transfer_window_open FROM TRANSFER_SETTINGS ORDER BY id DESC LIMIT 1",
        fetchone=True
    )
    return result['transfer_window_open'] if result else True

async def set_transfer_window_status(is_open: bool, updated_by_id: int, updated_by_name: str):
    """Set transfer window status."""
    await execute_query(
        """UPDATE TRANSFER_SETTINGS 
           SET transfer_window_open = %s, updated_by_discord_id = %s, updated_by_name = %s 
           WHERE id = (SELECT id FROM (SELECT id FROM TRANSFER_SETTINGS ORDER BY id DESC LIMIT 1) as temp)""",
        (is_open, updated_by_id, updated_by_name),
        commit=True
    )
    
    # Send announcement
    status = "OPEN" if is_open else "CLOSED"
    icon = "🟢" if is_open else "🔴"
    message = f"{icon} **Transfer Window is now {status}**"
    if is_open:
        message += "\nTeams can now register and remove players."
    else:
        message += "\nPlayer registrations and removals are temporarily disabled."
    
    await send_announcement(message_content=message)

# === TRANSFER LOGGING ===

async def log_transfer(
    player_discord_id: int,
    player_name: str,
    from_team_data: dict | None = None,
    to_team_data: dict | None = None,
    transfer_type: str = "TRANSFER",
    reason: str | None = None,
    processed_by_id: int | None = None,
    processed_by_name: str | None = None
):
    """Log a player transfer to the database."""
    
    from_guild_id = from_team_data['guild_id'] if from_team_data else None
    from_team_name = from_team_data['guild_name'] if from_team_data else None
    to_guild_id = to_team_data['guild_id'] if to_team_data else None
    to_team_name = to_team_data['guild_name'] if to_team_data else None
    
    await execute_query(
        """INSERT INTO PLAYER_TRANSFERS 
           (player_discord_id, player_name, from_team_guild_id, from_team_name, 
            to_team_guild_id, to_team_name, transfer_type, reason, 
            processed_by_discord_id, processed_by_name)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (player_discord_id, player_name, from_guild_id, from_team_name, 
         to_guild_id, to_team_name, transfer_type, reason, 
         processed_by_id, processed_by_name),
        commit=True
    )

async def get_player_transfer_history(limit: int = 50):
    """Get recent player transfers."""
    return await execute_query(
        """SELECT * FROM PLAYER_TRANSFERS 
           ORDER BY transfer_date DESC 
           LIMIT %s""",
        (limit,),
        fetchall=True
    )

async def get_player_team_at_date(player_discord_id: int, target_date: datetime):
    """Get which team a player was on at a specific date."""
    # Get all transfers for this player up to the target date
    transfers = await execute_query(
        """SELECT * FROM PLAYER_TRANSFERS 
           WHERE player_discord_id = %s AND transfer_date <= %s
           ORDER BY transfer_date DESC""",
        (player_discord_id, target_date),
        fetchall=True
    )
    
    if not transfers:
        # Check current team if no transfer history
        current_teams = await get_player_current_teams(player_discord_id)
        return current_teams[0] if current_teams else None
    
    # Find the most recent transfer before the target date
    latest_transfer = transfers[0]
    
    if latest_transfer['transfer_type'] == 'LEAVE':
        return None  # Player was unattached
    elif latest_transfer['transfer_type'] in ['JOIN', 'TRANSFER']:
        return {
            'guild_id': latest_transfer['to_team_guild_id'],
            'guild_name': latest_transfer['to_team_name']
        }
    
    return None

async def get_player_current_teams(player_discord_id: int):
    """Get current teams a player is on."""
    from ios_bot.database_manager import get_player_teams
    return await get_player_teams(player_discord_id)

# === TRANSFER ANNOUNCEMENTS ===

async def announce_player_join(player_name: str, team_data: dict, processed_by_name: str | None = None):
    """Announce a player joining a team."""
    embed = discord.Embed(
        title="Player Joined Team",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(name="Player", value=f"**{player_name}**", inline=True)
    embed.add_field(name="From", value="Free Agent", inline=True)
    embed.add_field(name="To", value=f"**{team_data['guild_name']}**", inline=True)
    
    if team_data.get('guild_icon'):
        embed.set_thumbnail(url=team_data['guild_icon'])
    
    if processed_by_name:
        embed.set_footer(text=f"Processed by {processed_by_name}")
    
    await send_announcement(embed=embed)

async def announce_player_leave(player_name: str, team_data: dict, reason: str | None = None, processed_by_name: str | None = None):
    """Announce a player leaving a team."""
    embed = discord.Embed(
        title="Player Left Team", 
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(name="Player", value=f"**{player_name}**", inline=True)
    embed.add_field(name="From", value=f"**{team_data['guild_name']}**", inline=True)
    embed.add_field(name="To", value="Free Agent", inline=True)
    
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    
    if team_data.get('guild_icon'):
        embed.set_thumbnail(url=team_data['guild_icon'])
    
    if processed_by_name:
        embed.set_footer(text=f"Processed by {processed_by_name}")
    
    await send_announcement(embed=embed)

async def announce_player_transfer(player_name: str, from_team_data: dict | None, to_team_data: dict, processed_by_name: str | None = None):
    """Announce a player transferring between teams."""
    embed = discord.Embed(
        title="Player Transfer",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(name="Player", value=f"**{player_name}**", inline=True)
    embed.add_field(
        name="From",
        value=f"**{from_team_data['guild_name']}**" if from_team_data else "N/A",
        inline=True
    )
    embed.add_field(name="To", value=f"**{to_team_data['guild_name']}**", inline=True)
    
    # Use the new team's logo
    if to_team_data.get('guild_icon'):
        embed.set_thumbnail(url=to_team_data['guild_icon'])
    
    if processed_by_name:
        embed.set_footer(text=f"Processed by {processed_by_name}")
    
    await send_announcement(embed=embed)

# === ENHANCED TEAM MANAGEMENT ===

async def add_player_to_team_with_transfer(
    guild_id: int,
    player_discord_id: int,
    player_name: str,
    processed_by_id: int | None = None,
    processed_by_name: str | None = None
):
    """Add player to team and log transfer."""
    # Check transfer window
    if not await get_transfer_window_status():
        raise ValueError("Transfer window is currently closed. Player registrations are not allowed.")
    
    # Get current teams
    current_teams = await get_player_current_teams(player_discord_id)
    to_team_data = await get_team(guild_id)
    
    if not to_team_data:
        raise ValueError("Target team not found.")
    
    # Determine transfer type and from_team
    from_team_data = None
    transfer_type = "JOIN"
    
    if current_teams:
        # Player is transferring from another team
        from_team_data = current_teams[0]  # Assume first team if multiple
        transfer_type = "TRANSFER"
        
        # Remove from old team first
        await remove_player_from_current_teams(player_discord_id)
    
    # Add to new team (use existing database functions)
    from ios_bot.database_manager import update_team_players
    team_data = await get_team(guild_id)
    if team_data:
        players_list = team_data.get('players', [])
        
        # Add new player
        new_player = {
            'id': player_discord_id,
            'name': player_name,
            'steam_id': None  # Will be set when they register
        }
        players_list.append(new_player)
        
        await update_team_players(guild_id, players_list)
    
    # Log transfer
    await log_transfer(
        player_discord_id, player_name, from_team_data, to_team_data,
        transfer_type, None, processed_by_id, processed_by_name
    )
    
    # Send announcement
    if transfer_type == "JOIN":
        await announce_player_join(player_name, to_team_data, processed_by_name)
    else:
        await announce_player_transfer(player_name, from_team_data, to_team_data, processed_by_name)

async def remove_player_from_team_with_transfer(
    guild_id: int,
    player_discord_id: int,
    player_name: str,
    reason: str | None = None,
    processed_by_id: int | None = None,
    processed_by_name: str | None = None
):
    """Remove player from team and log transfer."""
    # Check transfer window
    if not await get_transfer_window_status():
        raise ValueError("Transfer window is currently closed. Player removals are not allowed.")
    
    from_team_data = await get_team(guild_id)
    if not from_team_data:
        raise ValueError("Team not found.")
    
    # Remove from team (use existing database functions)
    await remove_player_from_current_teams(player_discord_id)
    
    # Log transfer
    await log_transfer(
        player_discord_id, player_name, from_team_data, None,
        "LEAVE", reason, processed_by_id, processed_by_name
    )
    
    # Send announcement
    await announce_player_leave(player_name, from_team_data, reason, processed_by_name)

async def remove_player_from_current_teams(player_discord_id: int):
    """Remove player from all their current teams."""
    from ios_bot.database_manager import get_all_teams_with_details, update_team_players
    
    all_teams = await get_all_teams_with_details()
    for team in all_teams:
        players_list = team.get('players', [])
        
        # Remove player if they're in this team
        updated_players = [p for p in players_list if p.get('id') != player_discord_id]
        
        if len(updated_players) != len(players_list):
            await update_team_players(team['guild_id'], updated_players)

# === GUILD LEAVE DETECTION ===

async def handle_guild_member_leave(member: discord.Member):
    """Handle when a player leaves the Discord guild."""
    player_discord_id = member.id
    player_name = member.display_name
    
    # Check if they're on any teams
    current_teams = await get_player_current_teams(player_discord_id)
    
    for team_data in current_teams:
        # Remove from team
        await remove_player_from_current_teams(player_discord_id)
        
        # Log transfer with automatic reason
        await log_transfer(
            player_discord_id, player_name, team_data, None,
            "LEAVE", "Left Discord server", None, "System (Auto)"
        )
        
        # Send announcement
        await announce_player_leave(
            player_name, team_data, "Left Discord server", "System (Auto)"
        )

# === MATCH-TO-TEAM LINKING WITH TRANSFER HISTORY ===

async def determine_teams_from_match_with_history(match_data: dict, match_date: datetime):
    """Determine which teams played in a match using transfer history."""
    
    # Extract player Steam IDs from match
    home_players = []
    away_players = []
    
    for player in match_data.get('players', []):
        steam_id = player['info']['steamId']
        
        for period in player.get('matchPeriodData', []):
            team_side = period['info']['team']
            if team_side == 'home':
                home_players.append(steam_id)
            elif team_side == 'away':
                away_players.append(steam_id)
    
    # Find teams by analyzing players at match time
    home_team = await find_team_by_players_at_date(home_players, match_date)
    away_team = await find_team_by_players_at_date(away_players, match_date)
    
    return home_team, away_team

async def find_team_by_players_at_date(player_steam_ids: list, match_date: datetime):
    """Find which team these players belonged to at a specific date."""
    from ios_bot.database_manager import get_player_by_steam_id
    team_scores: dict[int, int] = {}  # guild_id -> score
    for steam_id in player_steam_ids:
        # Find Discord user by Steam ID
        player_record = await get_player_by_steam_id(steam_id)
        if not player_record:
            continue
        discord_id = player_record['discord_id']
        # Get their team at the match date
        team_at_date = await get_player_team_at_date(discord_id, match_date)
        if isinstance(team_at_date, dict) and 'guild_id' in team_at_date:
            guild_id = team_at_date['guild_id']
            team_scores[guild_id] = team_scores.get(guild_id, 0) + 1
    # Find team with most players
    if team_scores:
        best_team_id = max(team_scores, key=lambda k: team_scores[k])
        best_score = team_scores[best_team_id]
        # Only return if we have good confidence (at least 3 players)
        if best_score >= 3:
            return await get_team(best_team_id)
    return None

async def cache_match_team_determination(match_id: str, home_guild_id: int, away_guild_id: int, confidence: float):
    """Cache the result of match team determination."""
    await execute_query(
        """INSERT INTO MATCH_TEAM_CACHE (match_id, home_guild_id, away_guild_id, confidence_score)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE 
           home_guild_id = VALUES(home_guild_id),
           away_guild_id = VALUES(away_guild_id),
           confidence_score = VALUES(confidence_score)""",
        (match_id, home_guild_id, away_guild_id, confidence),
        commit=True
    )

async def get_cached_match_teams(match_id: str):
    """Get cached match team determination."""
    return await execute_query(
        "SELECT * FROM MATCH_TEAM_CACHE WHERE match_id = %s",
        (match_id,),
        fetchone=True
    ) 