from ios_bot.commands import *
# Import specific config variables needed for discovery
from ios_bot.config import *
from .database_manager import initialize_database
from .tasks import setup_tasks

async def discover_matchmaking_channels():
    """Dynamically discover main matchmaking channels based on regex patterns."""
    guild = bot.get_guild(MAIN_GUILD_ID)

    if not guild:
        print(f"Error: Main guild with ID {MAIN_GUILD_ID} not found for channel discovery.")
        return

    try:
        sixes_regex = re.compile(SIXES_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
        eights_regex = re.compile(EIGHTS_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
    except re.error as e:
        print(f"Error compiling regex patterns: {e}. Dynamic channel discovery will be skipped. Hardcoded/initial values will be used.")
        return
    
    discovered_sixes_count = 0
    discovered_eights_count = 0

    for channel in guild.text_channels:
        if eights_regex.search(channel.name): # Changed from .match() to .search()
            EIGHTS_MAIN_MATCHMAKING_CHANNELS.append(channel.id)
            discovered_eights_count += 1
        elif sixes_regex.search(channel.name):
            SIXES_MAIN_MATCHMAKING_CHANNELS.append(channel.id)
            discovered_sixes_count += 1

    if discovered_eights_count > 0:
        print(f"Discovered and updated 8s Main Matchmaking Channels: {EIGHTS_MAIN_MATCHMAKING_CHANNELS}")
    else:
        print("No 8s Main Matchmaking Channels discovered dynamically. The list in config remains empty (or retains initial values if discovery failed before clear).")
    if discovered_sixes_count > 0:
        print(f"Discovered and updated 6s Main Matchmaking Channels: {SIXES_MAIN_MATCHMAKING_CHANNELS}")
    else:
        print("No 6s Main Matchmaking Channels discovered dynamically. The list in config remains empty (or retains initial values if discovery failed before clear).")

@bot.event
async def on_connect():
    # Sync commands with optimal parameters for development
    # Consider using TEST_GUILD_ID for quicker syncs during dev if commands are guild-specific
    # For global commands, syncing without guild_ids is standard but can take time to propagate.
    await bot.sync_commands(
        force=True,  # Always sync to ensure latest changes
        register_guild_commands=True, # Ensure guild commands are registered if you use them
        delete_existing=True  # Remove old/stale commands
    )
    print(f"🔄 Commands synced.") # General message, adjust if using TEST_GUILD_ID

@bot.event
async def on_ready():
    print("================ Successful login")
    # Initialize the database (create tables if they don't exist)
    await initialize_database()

    await bot.change_presence(
        status=discord.Status.online, 
        activity=discord.Activity(
            type=discord.ActivityType.watching, 
            name="Your Performances..."
        )
    )
    # Discover matchmaking channels and update config lists directly
    await discover_matchmaking_channels()
    # Start all scheduled tasks
    setup_tasks()
    print("================ Bot fully initialized")

bot.run(token)