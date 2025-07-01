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
            else:
                # For non-commit/non-fetch queries, we need to consume any results
                # to prevent "Unread result found" errors
                try:
                    cursor.fetchall()  # Consume any results
                except:
                    pass  # No results to consume
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
            try:
                cursor.close()
            except:
                pass  # Ignore errors when closing cursor
        if conn and conn.is_connected():
            try:
                conn.close()
            except:
                pass  # Ignore errors when closing connection

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
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    await create_servers_table_if_not_exists()
    await create_active_matches_table_if_not_exists()
    await create_tournament_tables_if_not_exist()
    await alter_teams_table_for_national_teams()
    await alter_players_table_for_steam_id_length()
    await _migrate_players_add_steam_id()
    await migrate_servers_table_add_new_fields()
    await initialize_default_servers()
    print("Database initialization complete.")
    
async def create_servers_table_if_not_exists():
    """Create the IOS_SERVERS table if it doesn't already exist."""
    query = """
    CREATE TABLE IF NOT EXISTS IOS_SERVERS (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL UNIQUE,
        address VARCHAR(255) NOT NULL,
        password VARCHAR(255) NOT NULL,
        sftp_ip VARCHAR(255),
        host_username VARCHAR(255),
        host_password VARCHAR(255),
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    );
    """
    return await execute_query(query)

async def add_server(name: str, address: str, password: str, host_username: str = None, host_password: str = None, is_active: bool = True):
    """Add a new server to the database."""
    query = """
    INSERT INTO IOS_SERVERS (name, address, password, host_username, host_password, is_active)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        address = VALUES(address),
        password = VALUES(password),
        host_username = VALUES(host_username),
        host_password = VALUES(host_password),
        is_active = VALUES(is_active),
        updated_at = CURRENT_TIMESTAMP
    """
    return await execute_query(query, (name, address, password, host_username, host_password, is_active), commit=True)

async def get_all_servers():
    """Retrieve all active servers from the database."""
    query = "SELECT name, address, password FROM IOS_SERVERS WHERE is_active = TRUE ORDER BY name ASC"
    return await execute_query(query, fetchall=True)

async def get_server_by_name(name: str):
    """Retrieve a server by its name."""
    query = "SELECT name, address, password FROM IOS_SERVERS WHERE name = %s AND is_active = TRUE"
    return await execute_query(query, (name,), fetchone=True)

async def update_server(name: str, address: str = None, password: str = None, sftp_ip: str = None, host_username: str = None, host_password: str = None, is_active: bool = None):
    """Update server details."""
    fields_to_update = []
    params = []

    if address is not None:
        fields_to_update.append("address = %s")
        params.append(address)
    if password is not None:
        fields_to_update.append("password = %s")
        params.append(password)
    if sftp_ip is not None:
        fields_to_update.append("sftp_ip = %s")
        params.append(sftp_ip)
    if host_username is not None:
        fields_to_update.append("host_username = %s")
        params.append(host_username)
    if host_password is not None:
        fields_to_update.append("host_password = %s")
        params.append(host_password)
    if is_active is not None:
        fields_to_update.append("is_active = %s")
        params.append(is_active)

    if not fields_to_update:
        return False

    fields_to_update.append("updated_at = CURRENT_TIMESTAMP")
    query = f"UPDATE IOS_SERVERS SET {', '.join(fields_to_update)} WHERE name = %s"
    params.append(name)
    
    return await execute_query(query, tuple(params), commit=True)

async def delete_server(name: str):
    """Delete a server by setting it as inactive."""
    query = "UPDATE IOS_SERVERS SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE name = %s"
    return await execute_query(query, (name,), commit=True)

async def initialize_default_servers():
    """Initialize the servers table with default servers if it's empty."""
    # Check if any servers exist
    existing_servers = await execute_query("SELECT COUNT(*) as count FROM IOS_SERVERS WHERE is_active = TRUE", fetchone=True)
    
    if existing_servers and existing_servers.get('count', 0) == 0:
        print("No servers found in database, adding default servers...")
        
        # Add default servers with SFTP details
        default_servers = [
            ("Florida", "*", "*", "*", "*"),
            ("Georgia", "*", "*", "*", "*")
        ]
        
        for name, address, password, host_username, host_password in default_servers:
            await add_server(name, address, password, host_username, host_password, True)
            print(f"Added default server: {name}")
        
        print("Default servers initialization complete.")

async def get_all_servers_with_details():
    """Retrieve all servers (including inactive ones) with full details for admin management."""
    query = "SELECT id, name, address, password, sftp_ip, host_username, host_password, is_active, created_at, updated_at FROM IOS_SERVERS ORDER BY name ASC"
    return await execute_query(query, fetchall=True)

async def get_server_by_id(server_id: int):
    """Retrieve a server by its ID."""
    query = "SELECT id, name, address, password, sftp_ip, host_username, host_password, is_active FROM IOS_SERVERS WHERE id = %s"
    return await execute_query(query, (server_id,), fetchone=True)

async def delete_server_by_id(server_id: int):
    """Delete a server by its ID (set as inactive)."""
    query = "UPDATE IOS_SERVERS SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    return await execute_query(query, (server_id,), commit=True)

async def migrate_servers_table_add_new_fields():
    """Add new fields to existing IOS_SERVERS table if they don't exist."""
    print("Checking for IOS_SERVERS table migration...")
    
    # Check for sftp_ip column
    check_sftp_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOS_SERVERS' 
      AND COLUMN_NAME = 'sftp_ip'
    """
    result = await execute_query(check_sftp_query, (database,), fetchone=True)
    
    if result and result.get('count', 0) == 0:
        print("Adding sftp_ip column to IOS_SERVERS...")
        alter_query = "ALTER TABLE IOS_SERVERS ADD COLUMN sftp_ip VARCHAR(255)"
        await execute_query(alter_query)
        print("sftp_ip column added successfully.")
    
    # Check for host_username column
    check_username_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOS_SERVERS' 
      AND COLUMN_NAME = 'host_username'
    """
    result = await execute_query(check_username_query, (database,), fetchone=True)
    
    if result and result.get('count', 0) == 0:
        print("Adding host_username column to IOS_SERVERS...")
        alter_query = "ALTER TABLE IOS_SERVERS ADD COLUMN host_username VARCHAR(255)"
        await execute_query(alter_query)
        print("host_username column added successfully.")
    
    # Check for host_password column
    check_password_query = """
    SELECT COUNT(*) as count
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_SCHEMA = %s 
      AND TABLE_NAME = 'IOS_SERVERS' 
      AND COLUMN_NAME = 'host_password'
    """
    result = await execute_query(check_password_query, (database,), fetchone=True)
    
    if result and result.get('count', 0) == 0:
        print("Adding host_password column to IOS_SERVERS...")
        alter_query = "ALTER TABLE IOS_SERVERS ADD COLUMN host_password VARCHAR(255)"
        await execute_query(alter_query)
        print("host_password column added successfully.")
    
    print("IOS_SERVERS table migration check complete.")

async def get_servers_for_compile_stats():
    """Retrieve all active servers with SFTP details formatted for compile_stats.py."""
    query = "SELECT name, address, password, host_username, host_password FROM IOS_SERVERS WHERE is_active = TRUE ORDER BY name ASC"
    servers = await execute_query(query, fetchall=True)
    
    formatted_servers = []
    for server in servers:
        if server['host_username'] and server['host_password']:
            # Extract IP and port from address (e.g., "87.98.129.61:27015" -> "87.98.129.61", "27015")
            address_parts = server['address'].split(':')
            if len(address_parts) == 2:
                host = address_parts[0]
                port = address_parts[1]
                # Derive SFTP IP from server address by changing port to 8822
                sftp_ip = f"{host}:8822"
                # Create directory path based on server IP and port
                dir_path = f"/{host}_{port}/iosoccer/statistics"
                
                formatted_servers.append({
                    "host": host,
                    "port": 8822,  # Default SFTP port
                    "user": server['host_username'],
                    "pass": server['host_password'],
                    "dir": dir_path
                })
    
    return formatted_servers

# === TOURNAMENT MANAGEMENT FUNCTIONS ===

async def create_tournament_tables_if_not_exist():
    """Create all tournament-related tables."""
    
    # Main tournaments table
    tournaments_query = """
    CREATE TABLE IF NOT EXISTS TOURNAMENTS (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL UNIQUE,
        num_teams INT NOT NULL,
        num_leagues INT NOT NULL,
        start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        end_date TIMESTAMP NULL,
        is_completed BOOLEAN DEFAULT FALSE,
        champion VARCHAR(255) NULL,
        runner_up VARCHAR(255) NULL,
        third_place VARCHAR(255) NULL,
        top_scorer VARCHAR(255) NULL,
        top_assister VARCHAR(255) NULL,
        top_defender VARCHAR(255) NULL,
        top_goalkeeper VARCHAR(255) NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    );
    """
    await execute_query(tournaments_query)
    
    # Tournament leagues table
    leagues_query = """
    CREATE TABLE IF NOT EXISTS TOURNAMENT_LEAGUES (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tournament_id INT NOT NULL,
        league_name VARCHAR(255) NOT NULL,
        league_order INT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tournament_id) REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
        UNIQUE KEY unique_tournament_league (tournament_id, league_name),
        UNIQUE KEY unique_tournament_order (tournament_id, league_order)
    );
    """
    await execute_query(leagues_query)
    
    # Tournament team assignments table
    teams_query = """
    CREATE TABLE IF NOT EXISTS TOURNAMENT_TEAMS (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tournament_id INT NOT NULL,
        league_id INT NOT NULL,
        guild_id BIGINT NOT NULL,
        guild_name VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tournament_id) REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
        FOREIGN KEY (league_id) REFERENCES TOURNAMENT_LEAGUES(id) ON DELETE CASCADE,
        UNIQUE KEY unique_tournament_team (tournament_id, guild_id)
    );
    """
    await execute_query(teams_query)
    
    # Tournament matches table (references to existing matches)
    matches_query = """
    CREATE TABLE IF NOT EXISTS TOURNAMENT_MATCHES (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tournament_id INT NOT NULL,
        league_id INT NOT NULL,
        match_id VARCHAR(255) NOT NULL,
        home_team_guild_id BIGINT NOT NULL,
        away_team_guild_id BIGINT NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tournament_id) REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
        FOREIGN KEY (league_id) REFERENCES TOURNAMENT_LEAGUES(id) ON DELETE CASCADE,
        UNIQUE KEY unique_tournament_match (tournament_id, match_id)
    );
    """
    await execute_query(matches_query)
    
    # Tournament stats cache table
    stats_query = """
    CREATE TABLE IF NOT EXISTS TOURNAMENT_STATS (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tournament_id INT NOT NULL,
        league_id INT NOT NULL,
        guild_id BIGINT NOT NULL,
        guild_name VARCHAR(255) NOT NULL,
        matches_played INT DEFAULT 0,
        wins INT DEFAULT 0,
        draws INT DEFAULT 0,
        losses INT DEFAULT 0,
        goals_for INT DEFAULT 0,
        goals_against INT DEFAULT 0,
        goal_difference INT DEFAULT 0,
        points INT DEFAULT 0,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (tournament_id) REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
        FOREIGN KEY (league_id) REFERENCES TOURNAMENT_LEAGUES(id) ON DELETE CASCADE,
        UNIQUE KEY unique_tournament_team_stats (tournament_id, league_id, guild_id)
    );
    """
    await execute_query(stats_query)

async def create_tournament(name: str, num_teams: int, num_leagues: int):
    """Create a new tournament with the specified parameters."""
    # Validate that teams can be evenly distributed
    if num_teams % num_leagues != 0:
        raise ValueError(f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues})")
    
    # Insert tournament
    query = """
    INSERT INTO TOURNAMENTS (name, num_teams, num_leagues)
    VALUES (%s, %s, %s)
    """
    result = await execute_query(query, (name, num_teams, num_leagues), commit=True)
    
    if not result:
        return None
        
    # Get the tournament ID
    tournament = await get_tournament_by_name(name)
    if not tournament:
        return None
        
    tournament_id = tournament['id']
    teams_per_league = num_teams // num_leagues
    
    # Create leagues
    for i in range(num_leagues):
        league_name = f"League {chr(65 + i)}"  # League A, League B, etc.
        await execute_query(
            "INSERT INTO TOURNAMENT_LEAGUES (tournament_id, league_name, league_order) VALUES (%s, %s, %s)",
            (tournament_id, league_name, i + 1),
            commit=True
        )
    
    return tournament_id

async def get_all_tournaments():
    """Get all tournaments with basic info."""
    query = """
    SELECT id, name, num_teams, num_leagues, start_date, end_date, is_completed,
           champion, runner_up, third_place, top_scorer, top_assister, top_defender, top_goalkeeper
    FROM TOURNAMENTS 
    ORDER BY is_completed ASC, start_date DESC
    """
    return await execute_query(query, fetchall=True)

async def get_tournament_by_name(name: str):
    """Get tournament by name."""
    query = "SELECT * FROM TOURNAMENTS WHERE name = %s"
    return await execute_query(query, (name,), fetchone=True)

async def get_tournament_by_id(tournament_id: int):
    """Get tournament by ID."""
    query = "SELECT * FROM TOURNAMENTS WHERE id = %s"
    return await execute_query(query, (tournament_id,), fetchone=True)

async def get_tournament_leagues(tournament_id: int):
    """Get all leagues for a tournament."""
    query = """
    SELECT id, league_name, league_order 
    FROM TOURNAMENT_LEAGUES 
    WHERE tournament_id = %s 
    ORDER BY league_order
    """
    return await execute_query(query, (tournament_id,), fetchall=True)

async def get_tournament_teams(tournament_id: int, league_id: int = None):
    """Get teams for a tournament, optionally filtered by league."""
    if league_id:
        query = """
        SELECT tt.guild_id, tt.guild_name, tl.league_name 
        FROM TOURNAMENT_TEAMS tt
        JOIN TOURNAMENT_LEAGUES tl ON tt.league_id = tl.id
        WHERE tt.tournament_id = %s AND tt.league_id = %s
        ORDER BY tt.guild_name
        """
        return await execute_query(query, (tournament_id, league_id), fetchall=True)
    else:
        query = """
        SELECT tt.guild_id, tt.guild_name, tl.league_name, tl.id as league_id
        FROM TOURNAMENT_TEAMS tt
        JOIN TOURNAMENT_LEAGUES tl ON tt.league_id = tl.id
        WHERE tt.tournament_id = %s
        ORDER BY tl.league_order, tt.guild_name
        """
        return await execute_query(query, (tournament_id,), fetchall=True)

async def add_team_to_tournament(tournament_id: int, league_id: int, guild_id: int, guild_name: str):
    """Add a team to a tournament league."""
    # Check if league has space
    teams_per_league = await get_teams_per_league_limit(tournament_id)
    current_teams = await execute_query(
        "SELECT COUNT(*) as count FROM TOURNAMENT_TEAMS WHERE tournament_id = %s AND league_id = %s",
        (tournament_id, league_id),
        fetchone=True
    )
    
    if current_teams and current_teams['count'] >= teams_per_league:
        raise ValueError(f"League is full. Maximum {teams_per_league} teams per league.")
    
    # Check if team is already in tournament
    existing = await execute_query(
        "SELECT id FROM TOURNAMENT_TEAMS WHERE tournament_id = %s AND guild_id = %s",
        (tournament_id, guild_id),
        fetchone=True
    )
    
    if existing:
        raise ValueError("Team is already registered in this tournament")
    
    query = """
    INSERT INTO TOURNAMENT_TEAMS (tournament_id, league_id, guild_id, guild_name)
    VALUES (%s, %s, %s, %s)
    """
    result = await execute_query(query, (tournament_id, league_id, guild_id, guild_name), commit=True)
    
    # Initialize stats for the team
    if result:
        await execute_query(
            """
            INSERT INTO TOURNAMENT_STATS (tournament_id, league_id, guild_id, guild_name)
            VALUES (%s, %s, %s, %s)
            """,
            (tournament_id, league_id, guild_id, guild_name),
            commit=True
        )
    
    return result

async def remove_team_from_tournament(tournament_id: int, guild_id: int):
    """Remove a team from a tournament."""
    # Remove from matches first (if any)
    await execute_query(
        "DELETE FROM TOURNAMENT_MATCHES WHERE tournament_id = %s AND (home_team_guild_id = %s OR away_team_guild_id = %s)",
        (tournament_id, guild_id, guild_id),
        commit=True
    )
    
    # Remove stats
    await execute_query(
        "DELETE FROM TOURNAMENT_STATS WHERE tournament_id = %s AND guild_id = %s",
        (tournament_id, guild_id),
        commit=True
    )
    
    # Remove team
    return await execute_query(
        "DELETE FROM TOURNAMENT_TEAMS WHERE tournament_id = %s AND guild_id = %s",
        (tournament_id, guild_id),
        commit=True
    )

async def get_teams_per_league_limit(tournament_id: int):
    """Get the maximum teams allowed per league for a tournament."""
    tournament = await get_tournament_by_id(tournament_id)
    if tournament:
        return tournament['num_teams'] // tournament['num_leagues']
    return 0

async def update_tournament_details(tournament_id: int, name: str = None, num_teams: int = None, num_leagues: int = None):
    """Update tournament details."""
    fields_to_update = []
    params = []
    
    if name is not None:
        fields_to_update.append("name = %s")
        params.append(name)
    if num_teams is not None and num_leagues is not None:
        # Validate divisibility
        if num_teams % num_leagues != 0:
            raise ValueError(f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues})")
        fields_to_update.append("num_teams = %s")
        fields_to_update.append("num_leagues = %s")
        params.extend([num_teams, num_leagues])
    
    if not fields_to_update:
        return False
    
    query = f"UPDATE TOURNAMENTS SET {', '.join(fields_to_update)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    params.append(tournament_id)
    
    return await execute_query(query, tuple(params), commit=True)

async def delete_tournament(tournament_id: int):
    """Delete a tournament and all related data."""
    return await execute_query("DELETE FROM TOURNAMENTS WHERE id = %s", (tournament_id,), commit=True)

async def add_match_to_tournament(tournament_id: int, match_id: str, home_team_guild_id: int, away_team_guild_id: int):
    """Add a match to a tournament and update stats."""
    # Get league for both teams (they should be in the same league)
    home_team = await execute_query(
        "SELECT league_id FROM TOURNAMENT_TEAMS WHERE tournament_id = %s AND guild_id = %s",
        (tournament_id, home_team_guild_id),
        fetchone=True
    )
    
    away_team = await execute_query(
        "SELECT league_id FROM TOURNAMENT_TEAMS WHERE tournament_id = %s AND guild_id = %s",
        (tournament_id, away_team_guild_id),
        fetchone=True
    )
    
    if not home_team or not away_team:
        raise ValueError("Both teams must be registered in the tournament")
    
    if home_team['league_id'] != away_team['league_id']:
        raise ValueError("Teams must be in the same league to play each other")
    
    league_id = home_team['league_id']
    
    # Check if match is already in tournament
    existing = await execute_query(
        "SELECT id FROM TOURNAMENT_MATCHES WHERE tournament_id = %s AND match_id = %s",
        (tournament_id, match_id),
        fetchone=True
    )
    
    if existing:
        raise ValueError("Match is already added to this tournament")
    
    # Add match to tournament
    query = """
    INSERT INTO TOURNAMENT_MATCHES (tournament_id, league_id, match_id, home_team_guild_id, away_team_guild_id)
    VALUES (%s, %s, %s, %s, %s)
    """
    result = await execute_query(query, (tournament_id, league_id, match_id, home_team_guild_id, away_team_guild_id), commit=True)
    
    if result:
        # Update tournament stats
        await recalculate_tournament_stats(tournament_id)
    
    return result

async def get_tournament_matches(tournament_id: int, league_id: int = None):
    """Get matches for a tournament, optionally filtered by league."""
    if league_id:
        query = """
        SELECT tm.match_id, tm.home_team_guild_id, tm.away_team_guild_id, tm.added_at,
               ht.guild_name as home_team_name, at.guild_name as away_team_name
        FROM TOURNAMENT_MATCHES tm
        JOIN TOURNAMENT_TEAMS ht ON tm.home_team_guild_id = ht.guild_id AND tm.tournament_id = ht.tournament_id
        JOIN TOURNAMENT_TEAMS at ON tm.away_team_guild_id = at.guild_id AND tm.tournament_id = at.tournament_id
        WHERE tm.tournament_id = %s AND tm.league_id = %s
        ORDER BY tm.added_at DESC
        """
        return await execute_query(query, (tournament_id, league_id), fetchall=True)
    else:
        query = """
        SELECT tm.match_id, tm.league_id, tm.home_team_guild_id, tm.away_team_guild_id, tm.added_at,
               ht.guild_name as home_team_name, at.guild_name as away_team_name,
               tl.league_name
        FROM TOURNAMENT_MATCHES tm
        JOIN TOURNAMENT_TEAMS ht ON tm.home_team_guild_id = ht.guild_id AND tm.tournament_id = ht.tournament_id
        JOIN TOURNAMENT_TEAMS at ON tm.away_team_guild_id = at.guild_id AND tm.tournament_id = at.tournament_id
        JOIN TOURNAMENT_LEAGUES tl ON tm.league_id = tl.id
        WHERE tm.tournament_id = %s
        ORDER BY tl.league_order, tm.added_at DESC
        """
        return await execute_query(query, (tournament_id,), fetchall=True)

async def recalculate_tournament_stats(tournament_id: int):
    """Recalculate and update tournament stats from matches."""
    import os
    import csv
    from datetime import datetime
    
    # Get tournament matches
    tournament_matches = await get_tournament_matches(tournament_id)
    if not tournament_matches:
        return
    
    # Get path to match summaries CSV
    match_summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
    if not os.path.exists(match_summaries_path):
        print(f"Match summaries CSV not found at {match_summaries_path}")
        return
    
    # Load match data from CSV
    match_data = {}
    try:
        with open(match_summaries_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                match_data[row['match_id']] = row
    except Exception as e:
        print(f"Error reading match summaries: {e}")
        return
    
    # Initialize stats for all tournament teams
    tournament_teams = await get_tournament_teams(tournament_id)
    team_stats = {}
    
    for team in tournament_teams:
        guild_id = team['guild_id']
        league_id = team['league_id']
        team_stats[guild_id] = {
            'tournament_id': tournament_id,
            'league_id': league_id,
            'guild_id': guild_id,
            'guild_name': team['guild_name'],
            'matches_played': 0,
            'wins': 0,
            'draws': 0,
            'losses': 0,
            'goals_for': 0,
            'goals_against': 0,
            'goal_difference': 0,
            'points': 0
        }
    
    # Process tournament matches
    for match in tournament_matches:
        match_id = match['match_id']
        home_guild_id = match['home_team_guild_id']
        away_guild_id = match['away_team_guild_id']
        
        if match_id not in match_data:
            continue
        
        match_info = match_data[match_id]
        try:
            home_score, away_score = map(int, match_info['scoreline'].split('-'))
        except (ValueError, KeyError):
            continue
        
        # Update home team stats
        if home_guild_id in team_stats:
            team_stats[home_guild_id]['matches_played'] += 1
            team_stats[home_guild_id]['goals_for'] += home_score
            team_stats[home_guild_id]['goals_against'] += away_score
            
            if home_score > away_score:
                team_stats[home_guild_id]['wins'] += 1
                team_stats[home_guild_id]['points'] += 3
            elif home_score == away_score:
                team_stats[home_guild_id]['draws'] += 1
                team_stats[home_guild_id]['points'] += 1
            else:
                team_stats[home_guild_id]['losses'] += 1
        
        # Update away team stats
        if away_guild_id in team_stats:
            team_stats[away_guild_id]['matches_played'] += 1
            team_stats[away_guild_id]['goals_for'] += away_score
            team_stats[away_guild_id]['goals_against'] += home_score
            
            if away_score > home_score:
                team_stats[away_guild_id]['wins'] += 1
                team_stats[away_guild_id]['points'] += 3
            elif away_score == home_score:
                team_stats[away_guild_id]['draws'] += 1
                team_stats[away_guild_id]['points'] += 1
            else:
                team_stats[away_guild_id]['losses'] += 1
    
    # Calculate goal differences
    for stats in team_stats.values():
        stats['goal_difference'] = stats['goals_for'] - stats['goals_against']
    
    # Update database with new stats
    for stats in team_stats.values():
        query = """
        INSERT INTO TOURNAMENT_STATS 
        (tournament_id, league_id, guild_id, guild_name, matches_played, wins, draws, losses, 
         goals_for, goals_against, goal_difference, points)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        matches_played = VALUES(matches_played),
        wins = VALUES(wins),
        draws = VALUES(draws),
        losses = VALUES(losses),
        goals_for = VALUES(goals_for),
        goals_against = VALUES(goals_against),
        goal_difference = VALUES(goal_difference),
        points = VALUES(points),
        last_updated = CURRENT_TIMESTAMP
        """
        await execute_query(query, (
            stats['tournament_id'], stats['league_id'], stats['guild_id'], stats['guild_name'],
            stats['matches_played'], stats['wins'], stats['draws'], stats['losses'],
            stats['goals_for'], stats['goals_against'], stats['goal_difference'], stats['points']
        ), commit=True)
    
    print(f"Tournament stats recalculated for tournament {tournament_id}")

async def get_filtered_matches_for_tournament(tournament_id: int, start_date: datetime = None):
    """Get matches filtered for tournament teams and date range."""
    import os
    import csv
    from datetime import datetime
    
    # Get tournament teams
    tournament_teams = await get_tournament_teams(tournament_id)
    if not tournament_teams:
        return []
    
    # Create a set of team names for quick lookup
    tournament_team_names = {team['guild_name'] for team in tournament_teams}
    
    # Get all matches from CSV
    match_summaries_path = os.path.join(os.path.dirname(__file__), 'ratings', 'match_summaries.csv')
    if not os.path.exists(match_summaries_path):
        return []
    
    filtered_matches = []
    tournament_match_ids = set()
    
    # Get matches already in tournament
    existing_matches = await get_tournament_matches(tournament_id)
    for match in existing_matches:
        tournament_match_ids.add(match['match_id'])
    
    try:
        with open(match_summaries_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Skip if match is already in tournament
                if row['match_id'] in tournament_match_ids:
                    continue
                
                # Check if both teams are in tournament
                home_team = row['home_team']
                away_team = row['away_team']
                if home_team not in tournament_team_names or away_team not in tournament_team_names:
                    continue
                
                # Check date filter
                if start_date:
                    try:
                        match_date = datetime.strptime(row['datetime'], '%Y-%m-%d %H:%M:%S')
                        if match_date < start_date:
                            continue
                    except ValueError:
                        continue
                
                # Check if teams are in the same league
                home_team_info = next((t for t in tournament_teams if t['guild_name'] == home_team), None)
                away_team_info = next((t for t in tournament_teams if t['guild_name'] == away_team), None)
                
                if home_team_info and away_team_info and home_team_info['league_id'] == away_team_info['league_id']:
                    row['home_team_guild_id'] = home_team_info['guild_id']
                    row['away_team_guild_id'] = away_team_info['guild_id']
                    row['league_id'] = home_team_info['league_id']
                    filtered_matches.append(row)
    
    except Exception as e:
        print(f"Error filtering matches: {e}")
        return []
    
    # Sort by date, most recent first
    filtered_matches.sort(key=lambda x: datetime.strptime(x['datetime'], '%Y-%m-%d %H:%M:%S'), reverse=True)
    
    return filtered_matches

async def get_tournament_league_table(tournament_id: int, league_id: int):
    """Get league table for a specific tournament league."""
    query = """
    SELECT guild_id, guild_name, matches_played, wins, draws, losses,
           goals_for, goals_against, goal_difference, points
    FROM TOURNAMENT_STATS
    WHERE tournament_id = %s AND league_id = %s
    ORDER BY points DESC, goal_difference DESC, goals_for DESC, guild_name ASC
    """
    return await execute_query(query, (tournament_id, league_id), fetchall=True)

async def complete_tournament(tournament_id: int, champion: str = None, runner_up: str = None, third_place: str = None):
    """Mark tournament as completed and set winners."""
    query = """
    UPDATE TOURNAMENTS 
    SET is_completed = TRUE, end_date = CURRENT_TIMESTAMP, champion = %s, runner_up = %s, third_place = %s
    WHERE id = %s
    """
    return await execute_query(query, (champion, runner_up, third_place, tournament_id), commit=True)