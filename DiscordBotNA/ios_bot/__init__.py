from ios_bot.config import *
import ios_bot.commands as commands_module
from ios_bot.db import Database
from .tasks import setup_tasks
import inspect
import json
import os
import asyncio
import sys

async def load_guild_config_from_db():
    """Load guild configuration from database and update config variables."""
    global MAIN_GUILD_ID, FIXTURES_CHANNEL_ID, RESULTS_CHANNEL_ID, ADMIN_ROLE_ID, TEAM_LEADER_ID, CONFIRMED_SCHEDULE_CHANNEL_ID, CAPTAINS_CHANNEL_ID
    try:
        def coerce_single_id(value):
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
                # If JSON array string, take first element
                if raw.startswith("["):
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, list) and parsed:
                            value = parsed[0]
                        else:
                            return None
                    except Exception:
                        pass
                # If plain string numeric
                try:
                    return int(str(value).strip())
                except Exception:
                    return None
            try:
                return int(value)
            except Exception:
                return None

        # First try to populate settings so they're available immediately
        try:
            from ios_bot.settings import settings
            await settings.load_guild_config(bot.db.pool)
            if settings.MAIN_GUILD_ID:
                MAIN_GUILD_ID = coerce_single_id(settings.MAIN_GUILD_ID)
                ADMIN_ROLE_ID = coerce_single_id(settings.ADMIN_ROLE_ID)
                TEAM_LEADER_ID = coerce_single_id(settings.TEAM_LEADER_ID)
                RESULTS_CHANNEL_ID = coerce_single_id(settings.RESULTS_CHANNEL)
                CONFIRMED_SCHEDULE_CHANNEL_ID = coerce_single_id(settings.CONFIRMED_CHANNEL)
                CAPTAINS_CHANNEL_ID = coerce_single_id(settings.CAPTAINS_CHANNEL)
                # FIXTURES_CHANNEL_ID is not exposed in Settings yet; keep fallback below

                # Update config module variables
                import ios_bot.config as config_module
                config_module.MAIN_GUILD_ID = MAIN_GUILD_ID
                config_module.ADMIN_ROLE_ID = ADMIN_ROLE_ID
                config_module.TEAM_LEADER_ID = TEAM_LEADER_ID
                config_module.RESULTS_CHANNEL_ID = RESULTS_CHANNEL_ID
                config_module.CONFIRMED_SCHEDULE_CHANNEL_ID = CONFIRMED_SCHEDULE_CHANNEL_ID
                config_module.CAPTAINS_CHANNEL_ID = CAPTAINS_CHANNEL_ID

                print(f"✅ Guild config loaded (settings): Guild={MAIN_GUILD_ID}, Results={RESULTS_CHANNEL_ID}")
                # Fall through to DB fetch only if fixtures or other fields are still missing
        except Exception as settings_error:
            print(f"⚠️ Could not load guild config into settings: {settings_error}")

        try:
            result = await bot.db.pool.fetchrow(
                "SELECT guild_id, admin_role_id, team_leader_role_id, results_channel, fixtures_channel, confirmed_channel, captains_channel FROM main_discord LIMIT 1"
            )
        except Exception:
            result = await bot.db.pool.fetchrow(
                "SELECT guild_id, admin_role_id, team_leader_role_id, results_channel, fixtures_channel, confirmed_channel FROM main_discord LIMIT 1"
            )
        if result:
            MAIN_GUILD_ID = coerce_single_id(result.get('guild_id'))
            ADMIN_ROLE_ID = coerce_single_id(result.get('admin_role_id'))
            TEAM_LEADER_ID = coerce_single_id(result.get('team_leader_role_id'))
            RESULTS_CHANNEL_ID = coerce_single_id(result.get('results_channel'))
            FIXTURES_CHANNEL_ID = coerce_single_id(result.get('fixtures_channel'))
            CONFIRMED_SCHEDULE_CHANNEL_ID = coerce_single_id(result.get('confirmed_channel'))
            CAPTAINS_CHANNEL_ID = coerce_single_id(result.get('captains_channel'))
            
            # Update config module variables
            import ios_bot.config as config_module
            config_module.MAIN_GUILD_ID = MAIN_GUILD_ID
            config_module.FIXTURES_CHANNEL_ID = FIXTURES_CHANNEL_ID
            config_module.RESULTS_CHANNEL_ID = RESULTS_CHANNEL_ID
            config_module.ADMIN_ROLE_ID = ADMIN_ROLE_ID
            config_module.TEAM_LEADER_ID = TEAM_LEADER_ID
            config_module.CONFIRMED_SCHEDULE_CHANNEL_ID = CONFIRMED_SCHEDULE_CHANNEL_ID
            config_module.CAPTAINS_CHANNEL_ID = CAPTAINS_CHANNEL_ID
            
            print(f"✅ Guild config loaded: Guild={MAIN_GUILD_ID}, Fixtures={FIXTURES_CHANNEL_ID}")
        else:
            print("⚠️ No guild configuration found in database")
    except Exception as e:
        print(f"⚠️ Could not load guild config from database: {e}")
        print("   Bot will continue with default values")

async def discover_matchmaking_channels(save_to_db: bool = False):
    """Dynamically discover main matchmaking channels based on regex patterns.
    Only discovers if channels are not already configured in database.
    """
    guild = bot.get_guild(MAIN_GUILD_ID)

    if not guild:
        print(f"Error: Main guild with ID {MAIN_GUILD_ID} not found for channel discovery.")
        return
    
    def coerce_channel_list(values):
        if values is None:
            return []
        if isinstance(values, (list, tuple)):
            return list(values)
        if isinstance(values, str):
            raw = values.strip()
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
            if raw.startswith("{") and raw.endswith("}"):
                items = [item for item in raw[1:-1].split(",") if item]
                return items
            if "," in raw:
                items = [item.strip() for item in raw.split(",") if item.strip()]
                return items
            return [raw]
        return []

    def normalize_channel_ids(values):
        normalized = []
        for value in coerce_channel_list(values):
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        return normalized

    async def get_channel_storage_mode():
        """Detect whether main_discord channel columns are JSON or text."""
        try:
            row = await bot.db.pool.fetchrow(
                """
                SELECT data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'main_discord'
                  AND column_name = 'sixes_channels'
                """
            )
            if row and (row['data_type'] in ('json', 'jsonb') or row['udt_name'] in ('json', 'jsonb')):
                return "json"
        except Exception:
            pass
        return "text"

    try:
        # Check if channels are already configured in database
        result = await bot.db.pool.fetchrow(
            "SELECT sixes_channels, eights_channels, fives_channels FROM main_discord WHERE guild_id = $1",
            MAIN_GUILD_ID
        )
        
        if result:
            raw_sixes = result.get('sixes_channels')
            raw_eights = result.get('eights_channels')
            raw_fives = result.get('fives_channels')

            existing_sixes = normalize_channel_ids(raw_sixes)
            existing_eights = normalize_channel_ids(raw_eights)
            existing_fives = normalize_channel_ids(raw_fives)

            def has_raw_value(value):
                if value is None:
                    return False
                if isinstance(value, (list, tuple)):
                    return len(value) > 0
                raw = str(value).strip().lower()
                return raw not in ("", "null", "[]", "{}", "none")

            if existing_sixes:
                SIXES_MAIN_MATCHMAKING_CHANNELS.clear()
                SIXES_MAIN_MATCHMAKING_CHANNELS.extend(existing_sixes)
            if existing_eights:
                EIGHTS_MAIN_MATCHMAKING_CHANNELS.clear()
                EIGHTS_MAIN_MATCHMAKING_CHANNELS.extend(existing_eights)
            if existing_fives:
                FIVES_MAIN_MATCHMAKING_CHANNELS.clear()
                FIVES_MAIN_MATCHMAKING_CHANNELS.extend(existing_fives)

            if existing_sixes or existing_eights or existing_fives:
                print(
                    f"✅ Matchmaking channels loaded (Sixes: {len(existing_sixes)}, "
                    f"Eights: {len(existing_eights)}, Fives: {len(existing_fives)})"
                )

            # Determine which channels need discovery
            discover_sixes = not existing_sixes and not has_raw_value(raw_sixes)
            discover_eights = not existing_eights and not has_raw_value(raw_eights)
            discover_fives = not existing_fives and not has_raw_value(raw_fives)
        else:
            # No config exists, discover both
            discover_sixes = True
            discover_eights = True
            discover_fives = True
    except Exception as e:
        print(f"⚠️ Error checking existing channels: {e}")
        # If error, attempt discovery anyway
        discover_sixes = True
        discover_eights = True
        discover_fives = True
    
    # Compile regex patterns
    FIVES_CHANNEL_REGEX_PATTERN = r'5v5'
    SIXES_CHANNEL_REGEX_PATTERN = r'6v6'
    EIGHTS_CHANNEL_REGEX_PATTERN = r'8v8'

    try:
        fives_regex = re.compile(FIVES_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
        sixes_regex = re.compile(SIXES_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
        eights_regex = re.compile(EIGHTS_CHANNEL_REGEX_PATTERN, re.IGNORECASE)
    except re.error as e:
        print(f"Error compiling regex patterns: {e}. Channel discovery skipped.")
        return
    
    discovered_fives = []
    discovered_sixes = []
    discovered_eights = []

    # Discover channels based on regex
    for channel in guild.text_channels:
        if discover_eights and eights_regex.search(channel.name):
            discovered_eights.append(channel.id)
        elif discover_sixes and sixes_regex.search(channel.name):
            discovered_sixes.append(channel.id)
        elif discover_fives and fives_regex.search(channel.name):
            discovered_fives.append(channel.id)

    # Update in-memory config lists only (no DB writes unless explicitly requested)
    if discovered_fives:
        FIVES_MAIN_MATCHMAKING_CHANNELS.clear()
        FIVES_MAIN_MATCHMAKING_CHANNELS.extend(discovered_fives)
        print(f"🔍 Discovered {len(discovered_fives)} Fives channels: {discovered_fives}")
    if discovered_sixes:
        SIXES_MAIN_MATCHMAKING_CHANNELS.clear()
        SIXES_MAIN_MATCHMAKING_CHANNELS.extend(discovered_sixes)
        print(f"🔍 Discovered {len(discovered_sixes)} Sixes channels: {discovered_sixes}")
    if discovered_eights:
        EIGHTS_MAIN_MATCHMAKING_CHANNELS.clear()
        EIGHTS_MAIN_MATCHMAKING_CHANNELS.extend(discovered_eights)
        print(f"🔍 Discovered {len(discovered_eights)} Eights channels: {discovered_eights}")

    if not (discovered_fives or discovered_sixes or discovered_eights):
        print("⚠️ No matchmaking channels discovered")
        return

    if not save_to_db:
        return

    # Optional DB update (explicit only)
    try:
        storage_mode = await get_channel_storage_mode()
        update_fields = []
        update_values = []
        param_count = 1

        empty_json_check = "IS NULL OR {col} = '[]'::jsonb"
        empty_text_check = "IS NULL OR {col} = '[]'"
        def empty_check(col: str) -> str:
            if storage_mode == "json":
                return empty_json_check.format(col=col)
            return empty_text_check.format(col=col)

        if discovered_fives:
            update_fields.append(
                "fives_channels = CASE WHEN "
                + empty_check("fives_channels")
                + f" THEN ${param_count}{'::jsonb' if storage_mode == 'json' else ''} ELSE fives_channels END"
            )
            update_values.append(json.dumps(discovered_fives))
            param_count += 1

        if discovered_sixes:
            update_fields.append(
                "sixes_channels = CASE WHEN "
                + empty_check("sixes_channels")
                + f" THEN ${param_count}{'::jsonb' if storage_mode == 'json' else ''} ELSE sixes_channels END"
            )
            update_values.append(json.dumps(discovered_sixes))
            param_count += 1

        if discovered_eights:
            update_fields.append(
                "eights_channels = CASE WHEN "
                + empty_check("eights_channels")
                + f" THEN ${param_count}{'::jsonb' if storage_mode == 'json' else ''} ELSE eights_channels END"
            )
            update_values.append(json.dumps(discovered_eights))
            param_count += 1

        update_query = f"""
        UPDATE main_discord 
        SET {', '.join(update_fields)}
        WHERE guild_id = ${param_count}
        """
        update_values.append(MAIN_GUILD_ID)
        safe_update_values = [json.dumps(v) if isinstance(v, (list, tuple)) else v for v in update_values]
        await bot.db.pool.execute(update_query, *safe_update_values)
        print("✅ Discovered channels saved to database")
    except Exception as e:
        print(f"⚠️ Error saving discovered channels: {e}")

_extensions_loaded = False
_extensions = []

async def load_extensions():
    """Load bot extensions (cogs) once at startup."""
    global _extensions_loaded
    if _extensions_loaded:
        return

    for ext in _extensions:
        try:
            result = bot.load_extension(ext)
            if inspect.isawaitable(result):
                await result
            print(f"✅ Loaded extension: {ext}")
        except Exception as e:
            print(f"⚠️ Failed to load extension {ext}: {e}")

    _extensions_loaded = True

@bot.event
async def on_connect():
    # Sync commands with optimal parameters for development
    # Consider using TEST_GUILD_ID for quicker syncs during dev if commands are guild-specific
    # For global commands, syncing without guild_ids is standard but can take time to propagate.
    try:
        await load_extensions()
        await bot.sync_commands(
            force=True,  # Always sync to ensure latest changes
            register_guild_commands=True, # Ensure guild commands are registered if you use them
            delete_existing=True  # Remove old/stale commands
        )
        print(f"🔄 Commands synced.") # General message, adjust if using TEST_GUILD_ID
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.event
async def on_ready():
    try:
        print("================ Successful login")
        # Initialize the database connection pool
        bot.db = Database(os.getenv('SUPABASE_DB_URL'))
        await bot.db.initialize()
        print("✅ Database connection pool initialized")
        
        # Load guild configuration from database
        await load_guild_config_from_db()
        
        # Initialize default servers if needed
        await bot.db.servers.initialize_default_servers()

        # Initialize weekly ratings system
        from ios_bot.ratings.Rating_Generator.weekly_ratings import initialize_weekly_ratings
        await initialize_weekly_ratings()

        await bot.change_presence(
            status=discord.Status.online, 
            activity=discord.Activity(
                type=discord.ActivityType.watching, 
                name="Your Performances..."
            )
        )
        # Discover matchmaking channels and update config lists directly
        await discover_matchmaking_channels(save_to_db=False)

        # Restore lineup snapshots (if any) and refresh embeds
        try:
            from ios_bot.signup_manager import restore_lineups_from_db
            await restore_lineups_from_db()
        except Exception as e:
            print(f"⚠️ Failed to restore lineup snapshots: {e}")

        # Clear any in-memory challenges on restart so teams can re-challenge
        try:
            from ios_bot.challenge_manager import active_challenges, broadcast_challenge_cooldowns
            active_challenges.clear()
            broadcast_challenge_cooldowns.clear()
            print("✅ Cleared in-memory challenges on startup")
        except Exception as e:
            print(f"⚠️ Failed to clear in-memory challenges: {e}")
        
        # Start webhook server for database notifications
        # try:
        #     from ios_bot.webhook_server import start_webhook_server
        #     webhook_port = int(os.getenv('WEBHOOK_PORT', '5000'))
        #     start_webhook_server(bot, host='0.0.0.0', port=webhook_port)
        # except Exception as webhook_error:
        #     print(f"⚠️ Could not start webhook server: {webhook_error}")
        #     print("   Match announcements will use polling instead")
        
        # Start all scheduled tasks
        setup_tasks()
        print("================ Bot fully initialized")
    except Exception as e:
        from .error_logger import log_error
        log_error(e, context={"event": "on_ready"}, command="bot_initialization")
        print(f"Error during bot initialization: {e}")

@bot.event
async def on_message(message):
    """Listen for eligibility trigger and start immediate SFTP sync."""
    if message.channel.id == 1465134796484382975 and "[ELIGIBLE]" in message.content:
        try:
            import ios_bot.tasks as tasks_module

            # Avoid overlapping runs
            if tasks_module.refresh_statistics_task and not tasks_module.refresh_statistics_task.done():
                pass
            else:
                tasks_module.refresh_statistics_task = asyncio.create_task(tasks_module._run_stats_compilation())
        except Exception as e:
            print(f"Error triggering SFTP sync from eligibility message: {e}")
    # discord.Bot doesn't implement process_commands (prefix commands).
    # Use process_application_commands if available; otherwise no-op.
    if hasattr(bot, "process_commands"):
        await bot.process_commands(message)
    elif hasattr(bot, "process_application_commands"):
        await bot.process_application_commands(message)

@bot.event
async def on_guild_remove(guild):
    """Clean up team data when the bot is removed from a guild."""
    try:
        team = await bot.db.teams.get_team(guild.id)
        if not team:
            return
        # Clear any in-memory signup states for this team's channels
        try:
            from ios_bot.signup_manager import clear_channel_state
            for ch_id in (team.get("eights_channels") or []):
                clear_channel_state(ch_id)
            for ch_id in (team.get("sixes_channels") or []):
                clear_channel_state(ch_id)
            for ch_id in (team.get("fives_channels") or []):
                clear_channel_state(ch_id)
        except Exception:
            pass
        await bot.db.teams.delete_team(guild.id)
        print(f"✅ Deleted team record for guild {guild.id} after bot removal.")
    except Exception as e:
        print(f"⚠️ Failed to delete team record for removed guild {guild.id}: {e}")
        
@bot.event
async def on_error(event, *args, **kwargs):
    """Handle errors to prevent bot crashes"""
    import traceback
    try:
        from .error_logger import log_error
        
        # Get the exception info
        exc_type, exc_value, exc_traceback = sys.exc_info()
        
        # Handle Discord API errors more gracefully
        if isinstance(exc_value, discord.HTTPException):
            if exc_value.status == 429:  # Rate limit
                print(f"[RATE LIMIT] Discord API rate limit hit in event {event}: HTTP {exc_value.status}")
                return  # Don't log rate limits as errors
            else:
                # Log other HTTP exceptions but don't crash
                print(f"[HTTP ERROR] Discord API error in event {event}: HTTP {exc_value.status} - {exc_value}")
                return
        
        # Log all other errors
        if exc_value:
            print(f"[GLOBAL ERROR] Unhandled error in event {event}: {exc_value}")
            log_error(exc_value, context={
                "event": event,
                "args": str(args),
                "kwargs": str(kwargs)
            }, command="global_error_handler")
        
    except Exception as e:
        print(f"[CRITICAL] Error in global error handler: {e}")
        print(f"Original error: {exc_value if 'exc_value' in locals() else 'Unknown'}")

def main():
    """Main function to start the bot"""
    from ios_bot.settings import settings
    print("Starting IOSCA Community Bot...")
    try:
        bot.run(settings.DISCORD_BOT_TOKEN.get_secret_value())
    except discord.errors.LoginFailure:
        print("ERROR: Invalid bot token. Please check your DISCORD_BOT_TOKEN environment variable.")
        raise
    except discord.errors.ConnectionClosed as e:
        print(f"Discord connection closed: {e}")
        raise
    except Exception as e:
        print(f"Critical error running bot: {e}")
        raise

# Only run if this file is executed directly
if __name__ == "__main__":
    main()
