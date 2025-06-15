from collections import Counter
from ios_bot.config import *
from ios_bot.database_manager import get_player_teams, get_player_by_discord_id

# Define the path to the stats CSV file
STATS_FILE_PATH = os.path.join(os.path.dirname(__file__), '..', 'ratings', 'player_stats.csv')

def get_player_stats_from_csv(steam_id):
    """Reads the player_stats.csv and returns all rows matching the steam_id."""
    
    if not os.path.exists(STATS_FILE_PATH):
        print(f"Stats file not found at: {STATS_FILE_PATH}")
        return None, [], 0

    try:
        with open(STATS_FILE_PATH, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader)
            
            # Print first few rows to see Steam ID format
            player_rows = []
            unique_matches = set()
            for i, row in enumerate(reader):
                if row and row[2] == steam_id:
                    player_rows.append(dict(zip(header, row)))
                    unique_matches.add(row[0])

            return header, player_rows, len(unique_matches)
    except (IOError, csv.Error, StopIteration) as e:
        print(f"Error reading or parsing stats file: {e}")
        return None, [], 0

def calculate_all_time_stats(player_stats_rows):
    """Aggregates all player stats from provided rows and determines the most played position."""
    if not player_stats_rows:
        return None, None

    # Define numeric stat fields to be summed
    numeric_fields = [
        'Matches Played', 'goals', 'assists', 'secondAssists', 'offsides',
        'chancesCreated', 'keyPasses', 'interceptions', 'slidingTacklesCompleted',
        'fouls', 'ownGoals', 'passesCompleted', 'passes', 'keeperSaves',
        'keeperSavesCaught', 'goalsConceded'
    ]
    
    # Initialize aggregated stats
    aggregated_stats = {field: 0 for field in numeric_fields}
    position_counter = Counter()

    for row in player_stats_rows:
        for field in numeric_fields:
            try:
                # Get value, default to '0', convert to float then int to handle "X.0" cases
                aggregated_stats[field] += int(float(row.get(field, '0')))
            except (ValueError, TypeError):
                continue # Skip if value is not a valid number
        
        position = row.get('Position')
        if position:
            position_counter[position] += 1

    # Determine the most common position
    most_common_position = position_counter.most_common(1)[0][0] if position_counter else "N/A"
    
    return most_common_position, aggregated_stats

def format_stats(position, stats_row, appearances):
    """Formats the stats string based on the player's position."""
    appearances_str = f"**Appearances:** `{appearances}`"
    
    # Helper to safely get and format stats
    def get_stat(key):
        return int(float(stats_row.get(key, '0')))

    # Calculate pass completion
    passes_completed = get_stat('passesCompleted')
    total_passes = get_stat('passes')
    pass_completion_str = f"`{passes_completed / total_passes:.2%}`" if total_passes > 0 else "`0%`"
    
    pos_lower = position.upper()
    
    if pos_lower in {'LW', 'CF', 'RW'}:
        stats_list = [
            f"**Goals:** `{get_stat('goals')}`",
            f"**Assists:** `{get_stat('assists')}`",
            f"**2nd Assists:** `{get_stat('secondAssists')}`",
            f"**Offsides:** `{get_stat('offsides')}`"
        ]
    elif pos_lower == 'CM':
        stats_list = [
            f"**Goals:** `{get_stat('goals')}`",
            f"**Assists:** `{get_stat('assists')}`",
            f"**2nd Assists:** `{get_stat('secondAssists')}`",
            f"**Chances Created:** `{get_stat('chancesCreated')}`",
            f"**Key Passes:** `{get_stat('keyPasses')}`"
        ]
    elif pos_lower in {'LB', 'CB', 'RB'}:
        stats_list = [
            f"**Interceptions:** `{get_stat('interceptions')}`",
            f"**Tackles:** `{get_stat('slidingTacklesCompleted')}`",
            f"**Fouls:** `{get_stat('fouls')}`",
            f"**Own Goals:** `{get_stat('ownGoals')}`",
            f"**Pass %:** {pass_completion_str}"
        ]
    elif pos_lower == 'GK':
        stats_list = [
            f"**Saves:** `{get_stat('keeperSaves')}`",
            f"**Saves Caught:** `{get_stat('keeperSavesCaught')}`",
            f"**Goals Conceded:** `{get_stat('goalsConceded')}`",
            f"**Pass %:** {pass_completion_str}"
        ]
    else:
        return "No stats available for this position."
        
    return f"{appearances_str}\n" + "\n".join(stats_list)

@bot.slash_command(name="view_player", description="View a player's stats and teams.")
async def view_player(interaction: discord.Interaction, user: discord.Member):
    """Shows a player card with their teams and stats."""
    await interaction.response.defer()

    # 1. Get Player's SteamID from our own DB
    player_record = await get_player_by_discord_id(user.id)
    if not player_record or not player_record.get('steam_id'):
        await interaction.followup.send(
            f"{user.mention} has not registered their SteamID. They can do so using `/player_register`.",
            ephemeral=True
        )
        return

    steam_id = player_record['steam_id']

    # 2. Get Player's Stats from CSV and Teams from DB
    header, player_stats_rows, appearances = get_player_stats_from_csv(steam_id)
    player_teams = await get_player_teams(user.id)

    # Exit if player has no teams and no stats to show
    if not player_teams and not player_stats_rows:
        await interaction.followup.send(
            f"No teams or match stats found for {user.mention} (SteamID: `{steam_id}`).",
            ephemeral=True
        )
        return
        
    if not header:
        await interaction.followup.send(
            "The stats file seems to be missing or corrupted. Please contact an admin.",
            ephemeral=True
        )
        return

    # 3. Build Embed
    is_captain_of_any_team = any(team.get('captain_id') == user.id for team in player_teams)
    color = discord.Color.gold() if is_captain_of_any_team else discord.Color.blue()
    embed = discord.Embed(
        title=f"Player Card for {user.display_name}",
        color=color
    )

    club_team_info = next((team for team in player_teams if not team['is_national_team']), None)
    national_team_info = next((team for team in player_teams if team['is_national_team']), None)

    # Set thumbnail based on team or default
    author_url = user.display_avatar.url
    if club_team_info and club_team_info.get('image_url'):
        thumbnail_url = club_team_info['image_url']
    elif national_team_info and national_team_info.get('image_url'):
        thumbnail_url = national_team_info['image_url']
    else: # Only use main guild icon if player is on NO teams and has stats
        main_guild = bot.get_guild(MAIN_GUILD_ID)
        if main_guild and main_guild.icon:
            thumbnail_url = main_guild.icon.url

    embed.set_author(name=f"{user.display_name}", icon_url=author_url if user.display_avatar else None)
    embed.set_thumbnail(url=thumbnail_url)
    
    if not player_teams:
        embed.description = "This player is not currently on any registered team."

    # Add Club Team Stats
    if club_team_info:
        team_name = club_team_info['name']
        display_name = f"`{team_name}`"
        if club_team_info.get('captain_id') == user.id:
            display_name += " (CAPTAIN)"
        embed.add_field(name="**__Club Team__**", value=display_name, inline=False)
        
        team_stats = next((stats for stats in player_stats_rows if stats.get('Team Name') == team_name), None)
        if team_stats:
            position = team_stats.get('Position', 'N/A')
            stats_str = format_stats(position, team_stats, appearances)
            embed.add_field(name=f"Position: `{position}`", value=stats_str, inline=False)
        else:
            embed.add_field(name="**__Stats__**", value="No competitive stats found for this team.", inline=False)

    # Add National Team Stats
    if national_team_info:
        team_name = national_team_info['name']
        display_name = f"**{team_name}**"
        if national_team_info.get('captain_id') == user.id:
            display_name += " (CAPTAIN)"
        embed.add_field(name="**__National Team__**", value=display_name, inline=False)
        
        team_stats = next((stats for stats in player_stats_rows if stats.get('Team Name') == team_name), None)
        if team_stats:
            position = team_stats.get('Position', 'N/A')
            stats_str = format_stats(position, team_stats, appearances)
            embed.add_field(name=f"Position: {position}", value=stats_str, inline=False)
        else:
            embed.add_field(name="__Stats__", value="No competitive stats found for this team.", inline=False)

    # Add All-Time Stats as a separate field if stats exist
    if player_stats_rows:
        all_time_pos, all_time_stats = calculate_all_time_stats(player_stats_rows)
        if all_time_pos and all_time_stats:
            stats_str = format_stats(all_time_pos, all_time_stats, appearances)
            embed.add_field(name=f"**__All-Time Stats__** (Most Played: {all_time_pos})", value=stats_str, inline=False)

    embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
    await interaction.followup.send(embed=embed)