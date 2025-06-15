from ios_bot.config import * # Corrected import to use absolute path from package root and specific names

# Get the current event loop; this should be done once, ideally where the bot is defined or starts
# However, for a self-contained manager, getting it on demand is also an option.
# Be mindful if this module is imported before an event loop is set by discord.py.
# A more robust way would be to pass the loop or bot instance to the db manager if needed.

async def run_blocking_db_operation(func, *args):
    """Runs a blocking database function in an executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args) # None uses default ThreadPoolExecutor

def _connect_db_sync(): # Renamed original connect_db
    """Connect to the MySQL database (synchronous)."""
    try:
        conn = mysql.connector.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset='utf8mb4',
            collation='utf8mb4_general_ci'
        )
        if conn.is_connected():
            return conn
    except Error as e:
        print(f"Error connecting to MySQL database: {e}")
        return None

async def connect_db():
    """Connect to the MySQL database (asynchronous wrapper)."""
    return await run_blocking_db_operation(_connect_db_sync)


def _execute_query_sync(query: str, params: tuple = None, fetchone: bool = False, fetchall: bool = False, commit: bool = False): # Renamed original
    """Execute a general query (synchronous). Handles connection opening/closing."""
    conn = None
    cursor = None
    try:
        # conn = connect_db() # This would now be an async call if we used the new connect_db
        # For simplicity within this synchronous function, call the synchronous connector directly
        conn = _connect_db_sync()
        if conn:
            conn.ping(reconnect=True, attempts=3, delay=1)
            cursor = conn.cursor(dictionary=True if fetchone or fetchall else False)
            cursor.execute(query, params)
            if commit:
                conn.commit()
                # For INSERT statements, cursor.lastrowid might be useful
                # For others, True indicates success.
                return True 
            elif fetchone:
                return cursor.fetchone()
            elif fetchall:
                return cursor.fetchall()
            # For non-commit/non-fetch queries (e.g., CREATE, or some specific non-returning UPDATE/DELETE)
            # Defaulting to True if no error. Consider if specific return is needed.
            return True 
    except Error as e:
        print(f"Error executing query: {e}")
        if conn and commit: 
            try:
                conn.rollback()
            except Error as re:
                print(f"Error during rollback: {re}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

async def execute_query(query: str, params: tuple = None, fetchone: bool = False, fetchall: bool = False, commit: bool = False):
    """Execute a general query (asynchronous wrapper)."""
    return await run_blocking_db_operation(_execute_query_sync, query, params, fetchone, fetchall, commit)


async def create_teams_table_if_not_exists():
    """Create the IOSCA_TEAMS table if it doesn't already exist (asynchronous)."""
    query = """
    CREATE TABLE IF NOT EXISTS IOSCA_TEAMS (
        guild_id BIGINT PRIMARY KEY,
        guild_name VARCHAR(255) NOT NULL,
        guild_icon VARCHAR(255),
        captain_id BIGINT NOT NULL,
        captain_name VARCHAR(255) NOT NULL,
        vice_captain_id BIGINT,
        vice_captain_name VARCHAR(255),
        sixes_channels JSON,
        eights_channels JSON,
        players JSON 
    );
    """
    # JSON type can store lists of channel IDs and player objects.
    # Example for players: '[{"id": 123, "name": "PlayerA"}, {"id": 456, "name": "PlayerB"}]'
    # Example for channels: '[123456789012345678, 987654321098765432]'
    return await execute_query(query) # No commit needed for CREATE TABLE IF NOT EXISTS

async def alter_teams_table_for_national_teams():
    """Adds the is_national_team column if it doesn't exist."""
    check_column_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOSCA_TEAMS' 
      AND COLUMN_NAME = 'is_national_team'
    """
    result = await execute_query(check_column_query, (database,), fetchone=True)

    # The result from a fetchone with dictionary=True will be a dict, e.g., {'count': 0}
    if result and result.get('count', 0) == 0:
        print("`is_national_team` column not found, adding it to `IOSCA_TEAMS`...")
        alter_query = """
        ALTER TABLE IOSCA_TEAMS 
        ADD COLUMN is_national_team BOOLEAN NOT NULL DEFAULT FALSE
        """
        await execute_query(alter_query)
        print("Column `is_national_team` added successfully.")
    else:
        print("`is_national_team` column already exists.")

async def _migrate_players_add_steam_id():
    """Adds the steam_id field to each player in the players JSON list for all teams."""
    print("Checking for steam_id migration...")
    all_teams_query = "SELECT guild_id, players FROM IOSCA_TEAMS"
    all_teams = await execute_query(all_teams_query, fetchall=True)

    if not all_teams:
        print("No teams to migrate.")
        return

    for team in all_teams:
        guild_id = team['guild_id']
        try:
            players_data = team.get('players')
            if players_data and isinstance(players_data, (str, bytes)):
                players = json.loads(players_data)
            elif players_data and isinstance(players_data, list):
                players = players_data
            else:
                continue  # Skip if no players or invalid format

            updated = False
            for player in players:
                if 'steam_id' not in player:
                    player['steam_id'] = None
                    updated = True
            
            if updated:
                print(f"Updating players for guild {guild_id} to include steam_id.")
                await update_team_players(guild_id, players)

        except json.JSONDecodeError as e:
            print(f"Error decoding players JSON for guild {guild_id}: {e}")
        except Exception as e:
            print(f"An unexpected error occurred during migration for guild {guild_id}: {e}")
    print("Steam_id migration check complete.")

# --- CRUD functions for IOSCA_TEAMS (now async) ---

async def add_team(guild_id: int, guild_name: str, guild_icon: str, 
             captain_id: int, captain_name: str, 
             vice_captain_id: int, vice_captain_name: str,
             sixes_channels: list,
             eights_channels: list, 
             initial_players: list,
             is_national_team: bool):
    """Add a new team to the database (asynchronous)."""
    query = """
    INSERT INTO IOSCA_TEAMS (guild_id, guild_name, guild_icon, captain_id, captain_name, 
                             vice_captain_id, vice_captain_name, sixes_channels, 
                             eights_channels, players, is_national_team)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    # Convert lists to JSON strings for storage
    sixes_channels_json = json.dumps(sixes_channels)
    eights_channels_json = json.dumps(eights_channels)
    
    # Ensure all initial players have a steam_id field
    for player in initial_players:
        if 'steam_id' not in player:
            player['steam_id'] = None

    players_json = json.dumps(initial_players)
    
    return await execute_query(query, (guild_id, guild_name, guild_icon, captain_id, captain_name, 
                                  vice_captain_id, vice_captain_name, sixes_channels_json, 
                                  eights_channels_json, players_json, is_national_team), commit=True)

async def get_team(guild_id: int):
    """Retrieve a team by its guild_id (asynchronous)."""
    query = "SELECT * FROM IOSCA_TEAMS WHERE guild_id = %s"
    team_data = await execute_query(query, (guild_id,), fetchone=True)
    if team_data:
        # Convert JSON strings back to Python lists/dicts
        if team_data.get('sixes_channels'):
            team_data['sixes_channels'] = json.loads(team_data['sixes_channels'])
        if team_data.get('eights_channels') and isinstance(team_data['eights_channels'], (str, bytes)):
            team_data['eights_channels'] = json.loads(team_data['eights_channels'])
        if team_data.get('players') and isinstance(team_data['players'], (str, bytes)):
            team_data['players'] = json.loads(team_data['players'])
    return team_data

async def get_all_teams():
    """Retrieve all registered teams (asynchronous)."""
    query = "SELECT guild_id, guild_name, guild_icon FROM IOSCA_TEAMS ORDER BY guild_name ASC" # Only fetch necessary fields for /view_teams dropdown
    teams_data = await execute_query(query, fetchall=True)
    # No JSON conversion needed here as we are not fetching JSON fields by default with this query.
    # If you fetch JSON fields later, you'll need to parse them.
    return teams_data

async def get_all_teams_with_channels():
    """Retrieve all teams with their channel information for challenges (asynchronous)."""
    query = "SELECT guild_id, guild_name, sixes_channels, eights_channels FROM IOSCA_TEAMS"
    teams_data = await execute_query(query, fetchall=True)
    if teams_data:
        for team in teams_data:
            if team.get('sixes_channels') and isinstance(team['sixes_channels'], (str, bytes)):
                team['sixes_channels'] = json.loads(team['sixes_channels'])
            else:
                team['sixes_channels'] = []
            if team.get('eights_channels') and isinstance(team['eights_channels'], (str, bytes)):
                team['eights_channels'] = json.loads(team['eights_channels'])
            else:
                team['eights_channels'] = [] # Ensure it's a list if null or already parsed
    return teams_data


async def update_team_players(guild_id: int, players_list: list):
    """Update the players list for a team (asynchronous). players_list should be a list of dicts."""
    query = "UPDATE IOSCA_TEAMS SET players = %s WHERE guild_id = %s"
    players_json = json.dumps(players_list)
    return await execute_query(query, (players_json, guild_id), commit=True)

async def update_team_details(guild_id: int, guild_name: str = None, guild_icon: str = None, 
                        captain_id: int = None, captain_name: str = None,
                        vice_captain_id: int = None, vice_captain_name: str = None,
                        sixes_channels: list = None, eights_channels: list = None):
    """Update various details of a team (asynchronous). Only provided fields are updated."""
    fields_to_update = []
    params = []

    if guild_name is not None:
        fields_to_update.append("guild_name = %s")
        params.append(guild_name)
    if guild_icon is not None:
        fields_to_update.append("guild_icon = %s")
        params.append(guild_icon)
    # Add other updatable fields similarly...
    if captain_id is not None:
        fields_to_update.append("captain_id = %s")
        params.append(captain_id)
    if captain_name is not None:
        fields_to_update.append("captain_name = %s")
        params.append(captain_name)
    if vice_captain_id is not None:
        fields_to_update.append("vice_captain_id = %s")
        params.append(vice_captain_id)
    if vice_captain_name is not None:
        fields_to_update.append("vice_captain_name = %s")
        params.append(vice_captain_name)
    if sixes_channels is not None:
        fields_to_update.append("sixes_channels = %s")
        params.append(json.dumps(sixes_channels))
    if eights_channels is not None:
        fields_to_update.append("eights_channels = %s")
        params.append(json.dumps(eights_channels))

    if not fields_to_update:
        return False # Nothing to update

    query = f"UPDATE IOSCA_TEAMS SET {', '.join(fields_to_update)} WHERE guild_id = %s"
    params.append(guild_id)
    
    return await execute_query(query, tuple(params), commit=True)


async def delete_team(guild_id: int):
    """Delete a team by its guild_id (asynchronous)."""
    query = "DELETE FROM IOSCA_TEAMS WHERE guild_id = %s"
    return await execute_query(query, (guild_id,), commit=True)

async def get_team_by_name(guild_name: str):
    """Retrieve a team by its name (case-insensitive search) (asynchronous)."""
    query = "SELECT * FROM IOSCA_TEAMS WHERE LOWER(guild_name) = LOWER(%s)"
    team_data = await execute_query(query, (guild_name,), fetchone=True)
    if team_data:
        # Convert JSON strings back to Python lists/dicts
        if team_data.get('sixes_channels') and isinstance(team_data['sixes_channels'], (str, bytes)):
            team_data['sixes_channels'] = json.loads(team_data['sixes_channels'])
        if team_data.get('eights_channels') and isinstance(team_data['eights_channels'], (str, bytes)):
            team_data['eights_channels'] = json.loads(team_data['eights_channels'])
        if team_data.get('players') and isinstance(team_data['players'], (str, bytes)):
            team_data['players'] = json.loads(team_data['players'])
    return team_data

async def is_player_in_team_type(player_id: int, is_national_team_check: bool):
    """
    Checks if a player is in any team of a specific type (club or national).
    Returns the name of the team they are on if found, otherwise None.
    """
    query = "SELECT guild_name, captain_id, vice_captain_id, players FROM IOSCA_TEAMS WHERE is_national_team = %s"
    teams = await execute_query(query, (is_national_team_check,), fetchall=True)

    if not teams:
        return None

    for team in teams:
        # Check captain and vice-captain
        if player_id == team.get('captain_id') or player_id == team.get('vice_captain_id'):
            return team.get('guild_name')
        
        # Check the 'players' JSON list
        players_list = []
        if team.get('players') and isinstance(team['players'], (str, bytes)):
            try:
                players_list = json.loads(team['players'])
            except json.JSONDecodeError:
                players_list = []
        elif isinstance(team.get('players'), list):
            players_list = team.get('players')
            
        for player_info in players_list:
            if isinstance(player_info, dict) and player_info.get('id') == player_id:
                return team.get('guild_name')
                
    return None

def get_unique_player_ids(team_data: dict) -> set:
    """
    Calculates the set of unique player IDs for a team, including captain, vice-captain,
    and all players listed in the 'players' field.
    """
    if not team_data:
        return set()

    ids = set()
    if team_data.get('captain_id'):
        ids.add(team_data['captain_id'])

    if team_data.get('vice_captain_id'):
        ids.add(team_data['vice_captain_id'])

    players_list = team_data.get('players', [])
    # Handle if players_list is a JSON string
    if isinstance(players_list, (str, bytes)):
        try:
            players_list = json.loads(players_list)
        except json.JSONDecodeError:
            players_list = []
    
    if isinstance(players_list, list):
        for player_info in players_list:
            if isinstance(player_info, dict) and 'id' in player_info:
                ids.add(player_info['id'])
    return ids

async def get_all_teams_with_details():
    """Retrieve all teams with full details, parsing JSON fields."""
    query = "SELECT * FROM IOSCA_TEAMS"
    teams_data = await execute_query(query, fetchall=True)
    if teams_data:
        for team in teams_data:
            # Safely parse JSON fields
            if team.get('sixes_channels') and isinstance(team['sixes_channels'], (str, bytes)):
                try:
                    team['sixes_channels'] = json.loads(team['sixes_channels'])
                except json.JSONDecodeError:
                    team['sixes_channels'] = []
            if team.get('eights_channels') and isinstance(team['eights_channels'], (str, bytes)):
                try:
                    team['eights_channels'] = json.loads(team['eights_channels'])
                except json.JSONDecodeError:
                    team['eights_channels'] = []
            if team.get('players') and isinstance(team['players'], (str, bytes)):
                try:
                    team['players'] = json.loads(team['players'])
                except json.JSONDecodeError:
                    team['players'] = []
    return teams_data

async def get_player_teams(player_id: int):
    """Retrieve all teams a player is on, returning a list of team names and types."""
    all_teams = await get_all_teams_with_details()
    player_s_teams = []
    if not all_teams:
        return player_s_teams
    
    for team in all_teams:
        # Using get_unique_player_ids to check for membership
        if player_id in get_unique_player_ids(team):
            player_s_teams.append({
                'name': team.get('guild_name'),
                'image_url': team.get('guild_icon'),
                'captain_id': team.get('captain_id'),
                'is_national_team': team.get('is_national_team', False)
            })
    return player_s_teams

async def create_players_table_if_not_exists():
    """Create the IOSCA_PLAYERS table if it doesn't already exist."""
    query = """
    CREATE TABLE IF NOT EXISTS IOSCA_PLAYERS (
        discord_id BIGINT PRIMARY KEY,
        username VARCHAR(255) NOT NULL,
        steam_id VARCHAR(255)
    );
    """
    return await execute_query(query)

async def register_player(discord_id: int, username: str, steam_id: str):
    """Inserts or updates a player's registration."""
    query = """
    INSERT INTO IOSCA_PLAYERS (discord_id, username, steam_id)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE
        username = VALUES(username),
        steam_id = VALUES(steam_id)
    """
    return await execute_query(query, (discord_id, username, steam_id), commit=True)

async def get_player_by_steam_id(steam_id: str):
    """Retrieve a player by their SteamID."""
    query = "SELECT discord_id, username, steam_id FROM IOSCA_PLAYERS WHERE steam_id = %s"
    return await execute_query(query, (steam_id,), fetchone=True)

async def get_player_by_discord_id(discord_id: int):
    """Retrieve a player's record by their Discord ID (asynchronous)."""
    query = "SELECT steam_id, username FROM IOSCA_PLAYERS WHERE discord_id = %s"
    return await execute_query(query, (discord_id,), fetchone=True)

async def alter_players_table_for_steam_id_length():
    """Alter the IOSCA_PLAYERS table to increase the length of the steam_id column."""
    # This addresses potential issues with longer SteamID formats.
    check_column_query = """
    SELECT CHARACTER_MAXIMUM_LENGTH 
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'IOSCA_PLAYERS' AND COLUMN_NAME = 'steam_id';
    """
    result = await execute_query(check_column_query, (database,), fetchone=True)
    
    # Assuming result is a dict like {'CHARACTER_MAXIMUM_LENGTH': 50}
    if result and result.get('CHARACTER_MAXIMUM_LENGTH', 0) < 50:
        print("Increasing steam_id column length in IOSCA_PLAYERS...")
        alter_query = "ALTER TABLE IOSCA_PLAYERS MODIFY COLUMN steam_id VARCHAR(50)"
        await execute_query(alter_query)
        print("steam_id column length increased.")

async def create_active_matches_table_if_not_exists():
    """Creates the ACTIVE_MATCHES table for tracking non-IOSCA matches."""
    query = """
    CREATE TABLE IF NOT EXISTS ACTIVE_MATCHES (
        id INT AUTO_INCREMENT PRIMARY KEY,
        home_team_name VARCHAR(255) NOT NULL,
        away_team_name VARCHAR(255) NOT NULL,
        channel_id BIGINT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    await execute_query(query)

async def add_active_match(home_team_name: str, away_team_name: str, channel_id: int):
    """Adds a new active match for the announcement task to find."""
    query = "INSERT INTO ACTIVE_MATCHES (home_team_name, away_team_name, channel_id) VALUES (%s, %s, %s)"
    await execute_query(query, (home_team_name, away_team_name, channel_id), commit=True)

async def pop_active_match_channel(home_team_name: str, away_team_name: str) -> int | None:
    """
    Finds the most recent channel for a given matchup and deletes the record.
    Returns the channel ID if found, otherwise None.
    """
    # This query finds the match regardless of which team is home or away
    find_query = """
    SELECT id, channel_id FROM ACTIVE_MATCHES
    WHERE (home_team_name = %s AND away_team_name = %s)
       OR (home_team_name = %s AND away_team_name = %s)
    ORDER BY created_at DESC
    LIMIT 1
    """
    
    match_record = await execute_query(find_query, (home_team_name, away_team_name, away_team_name, home_team_name), fetchone=True)

    if match_record:
        record_id = match_record['id']
        channel_id = match_record['channel_id']
        
        delete_query = "DELETE FROM ACTIVE_MATCHES WHERE id = %s"
        await execute_query(delete_query, (record_id,), commit=True)
        
        return channel_id
        
    return None

# Helper to call on bot startup or before first DB interaction in a session
async def initialize_database():
    """Initializes the database by creating tables if they don't exist."""
    print("Initializing database...")
    await create_teams_table_if_not_exists()
    await create_players_table_if_not_exists()
    await alter_teams_table_for_national_teams()
    await alter_players_table_for_steam_id_length()
    await _migrate_players_add_steam_id()
    await create_active_matches_table_if_not_exists()
    print("Database initialization complete.")