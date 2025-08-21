from ios_bot.config import *
from ios_bot.signup_manager import (
    init_state, 
    get_channel_state, 
    format_ready_message, 
    is_text_player,
    clear_channel_state,
    refresh_lineup as sm_refresh_lineup, # Aliased
    TextPlayer,
    get_player_position,
    get_channel_context,
    check_notification_cooldown, # Added import
    format_lineup, # Added import
    update_state
)
from ios_bot.commands.utils import delete_after_delay, move_sub_to_position # Added move_sub_to_position import
from ios_bot.challenge_manager import active_challenges
from ios_bot.announcements import announce_match_ready # Added import
from ios_bot.database_manager import add_active_match, get_team_by_name, get_all_servers, get_server_by_name
from ios_bot.commands.request_sub import get_server_status, get_server_status_sync

import time as clock

# The hard-coded RCON info is now removed.
# The bot will use the RCON_SERVERS list from config.py,
# which can be dynamically updated by the new /edit_main_server command.

# Available maps per format
MAP_POOLS = {"8v8": ["8v8_coral","8v8_italia", "8v8_london", "8v8_vienna"]}

# --- Global variable to control lineup checks for testing ---
SKIP_LINEUP_CHECKS_FOR_TESTING = False

# ─── VIEWS ──────────────────────────────────────────────────────────────

class ReadyView(View):
    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=None) # Persistent view
        self.guild_id = guild_id
        self.channel_id = channel_id

def check_match_readiness(initiator_state: dict, opponent_state: dict = None, game_type: str = "8s") -> tuple[bool, str]:
    """
    Checks if involved teams are ready for a match.
    New GK Rule: At least one GK is required overall.
    Fullness Rule: Each team must have all field positions filled.
    Returns (are_teams_ready, error_or_status_message)
    """
    positions = EIGHTS_POSITIONS if game_type == "8s" else SIXES_POSITIONS
    teams_to_check = []
    team_names_for_error_msgs = [] # For more precise error messages

    # Validate input states
    if not initiator_state:
        return False, "Error: Initiator state is missing or invalid."
    
    if not isinstance(initiator_state, dict):
        return False, "Error: Initiator state is not a valid dictionary."
    
    if opponent_state and not isinstance(opponent_state, dict):
        return False, "Error: Opponent state is not a valid dictionary."

    # This check is now handled inside ready_slash
    # if SKIP_LINEUP_CHECKS_FOR_TESTING:
    #     return True, "Teams considered ready for testing (checks skipped)."

    # 1. Populate teams_to_check and team_names_for_error_msgs
    if opponent_state: # Challenge mode or any match with a defined opponent
        if not initiator_state.get("teams") or not initiator_state["teams"][0]:
            return False, "Error: Initiator team data is missing."
        teams_to_check.append(initiator_state["teams"][0])
        team_names_for_error_msgs.append(initiator_state.get("team_name", "Initiator"))

        # Determine opponent's name for messages first
        opponent_name_for_msg = opponent_state.get("team_name", "Opponent")
        if opponent_state.get("context_type") in ["main_8s", "main_6s"] and opponent_state.get("is_challenged_by_team_name"):
            opponent_name_for_msg = f"Main Guild Team (vs {opponent_state.get('is_challenged_by_team_name')})"
        
        if not opponent_state.get("teams") or not opponent_state["teams"][0]:
            return False, f"Error: {opponent_name_for_msg} lineup data is missing."
        teams_to_check.append(opponent_state["teams"][0]) # Assumes team[0] of opponent_state is always correct per user's other fix
        team_names_for_error_msgs.append(opponent_name_for_msg)

    else: # Standard main channel (1 or 2 teams from initiator_state)
        if not initiator_state.get("teams"):
             return False, "Error: No team data in channel state for standard match."
        for i, team_data in enumerate(initiator_state["teams"]):
            teams_to_check.append(team_data)
            if len(initiator_state["teams"]) > 1: # Two teams in main channel
                team_names_for_error_msgs.append(f"Team {i+1}")
            else: # Single team context (team channel, or main channel with only one team signup active)
                team_names_for_error_msgs.append(initiator_state.get("team_name", "Team"))
    
    # Basic validation for internal logic
    if not teams_to_check:
        return False, "Error: No team data could be assembled for readiness check."
    if len(teams_to_check) != len(team_names_for_error_msgs):
        # Fallback to generic naming if lists don't match, to prevent crash. This indicates an issue in above logic.
        print(f"[CRITICAL WARNING] check_match_readiness: Mismatch between teams_to_check ({len(teams_to_check)}) and team_names_for_error_msgs ({len(team_names_for_error_msgs)})")
        team_names_for_error_msgs = [f"Team {i+1}" for i in range(len(teams_to_check))]

    total_gks_found_across_all_teams = 0

    # 2. Check each team for full field positions AND count their GKs
    for idx, team_lineup in enumerate(teams_to_check):
        # Ensure team_name is valid, fallback if names list didn't align (shouldn't happen with new logic)
        team_name = team_names_for_error_msgs[idx] if idx < len(team_names_for_error_msgs) else f"Team {idx+1}"
        
        gk_in_this_team = False
        field_positions_in_this_team = 0
        total_field_positions_for_gametype = len([p for p in positions if p != "GK"])

        if not isinstance(team_lineup, dict): # Ensure team_lineup is a dictionary
            return False, f"Error: Lineup data for {team_name} is invalid (not a dictionary)."

        for pos in positions:
            player_data = team_lineup.get(pos)
            if player_data is not None:
                if pos == "GK":
                    gk_in_this_team = True
                else: # Field position
                    field_positions_in_this_team += 1
        
        if gk_in_this_team:
            total_gks_found_across_all_teams += 1

        # Check if THIS team is full (field positions only; GK is checked globally later)
        if field_positions_in_this_team < total_field_positions_for_gametype:
            missing_pos_example = ""
            for p_check in positions: # Find an example of a missing field position
                if p_check != "GK" and team_lineup.get(p_check) is None:
                    missing_pos_example = p_check
                    break
            return False, f"❌ {team_name} is not full. Missing field players (e.g., {missing_pos_example})."
            
    # 3. Apply new aggregate GK rule (after checking all teams are individually full field-wise)
    # This check applies if there's at least one team to evaluate.
    if len(teams_to_check) > 0 and total_gks_found_across_all_teams == 0:
        if len(teams_to_check) == 1:
             # Single team scenario (e.g. team channel doing /ready by itself)
             return False, f"❌ {team_names_for_error_msgs[0]} needs a Goalkeeper (GK)."
        else:
             # Two team scenario (challenge, main vs main)
             return False, "❌ At least one Goalkeeper (GK) is required between the teams to start the match."

    return True, "Teams are ready to proceed!"

# Use the get_server_status functions from request_sub.py to avoid duplication

def get_all_member_objects_from_state(channel_state: dict) -> list[discord.Member]:
    """Extract unique Discord Member objects from a channel state."""
    members = []
    if not channel_state:
        return members
    for team in channel_state.get("teams", []):
        for player_data in team.values():
            if player_data and not is_text_player(player_data['player']) and hasattr(player_data['player'], 'send'):
                members.append(player_data['player'])
    for sub_obj in channel_state.get("subs", []):
        if sub_obj and not is_text_player(sub_obj) and hasattr(sub_obj, 'send'):
            members.append(sub_obj)
    unique_members = []
    seen_ids = set()
    for m in members:
        if m.id not in seen_ids:
            unique_members.append(m)
            seen_ids.add(m.id)
    return unique_members

class MapSelect(View):
    def __init__(self, fmt: str, region_key: str, mentions: list[str], server_name: str, server_addr: str, guild_name: str, requester: discord.Member, guild: discord.Guild, subs: list[discord.Member], opponent_guild_name: str = None, challenge_data: dict = None):
        super().__init__(timeout=None)
        self.original_fmt = fmt # Should be "8s"
        self.region_key = region_key # This is the server's 'name' key, e.g., "NA East 1"
        self.mentions = mentions
        self.server_name = server_name
        self.server_addr = server_addr
        self.guild_name = guild_name
        self.opponent_guild_name = opponent_guild_name
        self.requester = requester
        self.guild = guild
        self.subs = subs
        self.challenge_data = challenge_data # Store challenge data

        # Convert "8s" to "8v8" for MAP_POOLS lookup
        if self.original_fmt == "8s":
            map_pool_key_fmt = "8v8"
            display_fmt = "8v8"
        elif self.original_fmt == "6s":
            map_pool_key_fmt = "6v6"
            display_fmt = "6v6"
        else:
            # Fallback or error if fmt is unexpected, though ready_slash should ensure "8s"
            map_pool_key_fmt = self.original_fmt 
            display_fmt = self.original_fmt
            # Consider logging a warning or raising an error if fmt is not "8s"
            print(f"[Warning] MapSelect received unexpected fmt: {self.original_fmt}")

        raw_list = MAP_POOLS.get(map_pool_key_fmt, [])
        if not raw_list:
            # This error is more specific now if map_pool_key_fmt is wrong
            raise ValueError(f"No maps defined for game format key: `{map_pool_key_fmt}` (derived from input `{self.original_fmt}`). Check MAP_POOLS in ready.py.")

        options = [SelectOption(label=m, value=m) for m in raw_list]
        sel = Select(
            placeholder=f"Select a {display_fmt} map…",
            min_values=1, max_values=1,
            options=options,
            custom_id="map_select"
        )
        sel.callback = self.on_map_selected
        self.add_item(sel)

    def _rcon_change_map_and_exec_cfg_sync(self, server_addr_str: str, server_passwd: str, selected_map: str, cfg_name: str):
        # Synchronous RCON operations
        host, port_str = server_addr_str.split(':')
        port = int(port_str)
        with Client(host, port, passwd=server_passwd) as r:
            r.run("map", selected_map)
        # Consider if time.sleep is truly needed here or if RCON server handles rapid commands
        # If it's blocking, it should also be part of a to_thread call if absolutely necessary,
        # but ideally, the RCON server itself manages command processing.
        # For now, assuming it's short enough or can be part of the threaded execution.
        clock.sleep(0.5) # This sleep will also happen in the thread
        with Client(host, port, passwd=server_passwd) as r:
            r.run("exec", cfg_name)

    async def on_map_selected(self, interaction: discord.Interaction):
        """When a map is selected, this is called."""
        await interaction.response.defer()
        
        # --- Store active match context for non-official teams ---
        if self.opponent_guild_name:
            try:
                initiator_is_official = await get_team_by_name(self.guild_name)
                opponent_is_official = await get_team_by_name(self.opponent_guild_name)
                
                # If both teams are not in the IOSCA_TEAMS table, we track the match
                if not initiator_is_official and not opponent_is_official:
                    await add_active_match(
                        team1_name=self.guild_name,
                        team2_name=self.opponent_guild_name,
                        channel_id=interaction.channel_id
                    )
                    print(f"[Match Tracker] Logged active non-IOSPL match: {self.guild_name} vs {self.opponent_guild_name} in channel {interaction.channel_id}")
            except Exception as e:
                print(f"[ERROR] Database check for active match failed: {e}")
        
        selected_map = interaction.data["values"][0]
        
        # Find the correct RCON password for the selected server
        server_details = await get_server_by_name(self.region_key)

        if not server_details:
            await interaction.followup.send(f"❌ Critical Error: Could not find the details for server '{self.region_key}'. Please try again.", ephemeral=True)
            return
            
        server_passwd = server_details.get("password")
        
        cfg_name = "8v8"

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, 
                self._rcon_change_map_and_exec_cfg_sync, 
                self.server_addr, 
                server_passwd, 
                selected_map, 
                cfg_name
            )
            map_change_msg = f"🗺️ Map changed to **{selected_map}** on **{self.server_name}** by {self.requester.mention}."
        except RequestException as e: # This exception might be from the sync rcon library
            map_change_msg = f"⚠️ Could not change map on **{self.server_name}** (RCON Error: {e}). Please change manually to **{selected_map}**."

        # Edit the original interaction message (which showed the MapSelect view) to confirm map change.
        # The subsequent match embed will be a new message.
        await interaction.edit_original_response(content=map_change_msg, view=None)

        current_channel_state = get_channel_state(interaction.channel_id)
        current_guild_id = interaction.guild_id
        current_channel_id = interaction.channel_id

        if self.challenge_data: # Challenge mode
            initiating_guild_obj = bot.get_guild(self.challenge_data.get("initiating_guild_id"))
            opponent_guild_obj = bot.get_guild(self.challenge_data.get("opponent_guild_id"))
            
            # For challenges, start_and_clear_challenge_match sends its own embed and handles DMs
            await start_and_clear_challenge_match(
                interaction, 
                self.challenge_data,
                self.server_name,
                self.server_addr, # Pass server_addr_str for connect info
                initiating_guild_obj,
                opponent_guild_obj,
                self.requester
            )
        else: # Standard matchmaking - create and send the detailed embed
            if not current_channel_state or not current_channel_state.get("teams"):
                try:
                    await interaction.channel.send("Error: Could not retrieve current team lineups for standard match. State might be missing or corrupted.")
                except discord.HTTPException:
                    pass # Channel might be gone
                return

            team1_name = "Team 1"
            team2_name = "Team 2"
            channel_ctx = await get_channel_context(current_guild_id, current_channel_id)
            game_type_display = self.original_fmt.upper()

            embed = discord.Embed(
                title="⚔️ Match Starting! ⚔️",
                description=f"**{team1_name}** vs **{team2_name}** ({game_type_display}) is starting on **{self.server_name}**!",
                color=discord.Color.blue() # Using blue for standard matches to differentiate slightly
            )

            embed_author_guild = self.guild 
            if embed_author_guild and embed_author_guild.icon:
                embed.set_author(name=f"{embed_author_guild.name} - Match Starting", icon_url=embed_author_guild.icon.url)
            else:
                embed.set_author(name="Match Starting")

            embed.add_field(
                name="🔗 Connect Info",
                value=f"Connect to [{self.server_addr}](https://iosoccer.com/connect/#{self.server_addr}) | Password is `iosmatch`",
                inline=False
            )

            # Team 1 Lineup
            team1_lineup_data = current_channel_state["teams"][0] if len(current_channel_state["teams"]) > 0 else {}
            team1_lineup_str = await format_lineup(team1_lineup_data, current_channel_id, current_guild_id)
            embed.add_field(name=f"{team1_name}'s Lineup", value=f"```{team1_lineup_str}```", inline=True)

            # Team 2 Lineup
            if len(current_channel_state.get("teams", [])) > 1:
                team2_lineup_data = current_channel_state["teams"][1]
                team2_lineup_str = await format_lineup(team2_lineup_data, current_channel_id, current_guild_id)
                embed.add_field(name=f"{team2_name}'s Lineup", value=f"```{team2_lineup_str}```", inline=True)
            else:
                embed.add_field(name=f"{team2_name}'s Lineup", value="```Lineup not available.```", inline=True)
            
            subs_list = current_channel_state.get("subs", [])
            subs_display_list = []
            if subs_list:
                for sub in subs_list:
                    if hasattr(sub, 'display_name'): subs_display_list.append(sub.display_name)
                    elif isinstance(sub, str): subs_display_list.append(sub) # Handle text player names if stored as str
            subs_text = ", ".join(subs_display_list) if subs_display_list else "No subs"
            embed.add_field(name="Subs", value=subs_text, inline=False)

            embed.set_footer(
                text=f"Match finalized by {self.requester.display_name}. Good luck to both teams!",
                icon_url=self.requester.display_avatar.url if self.requester.display_avatar else None
            )
            embed.timestamp = datetime.now(timezone.utc)
            
            try:
                await interaction.channel.send(embed=embed)
                # self.mentions (player list) is not explicitly added here to match challenge embed style
                # If individual player pings are needed, they could be sent as a separate message if self.mentions is populated
            except discord.HTTPException as e:
                print(f"Error sending standard match start embed: {e}")

            # Call state clearing and DM function
            await finish_standard_match_setup(interaction, current_channel_state, channel_ctx.get("type"), self.server_addr)

        self.stop() # Stop this view

    async def on_timeout(self):
        if self.children and isinstance(self.children[0], Select):
            self.children[0].disabled = True
        # Try to edit the original message if possible, otherwise ignore (it might have been handled)
        # This timeout might happen if user never selects a map.
        try:
            # This assumes 'self.message' is set by the view, which might not be the case
            # if the view is added to an existing message not sent by interaction.response.send_message(view=self).
            # For views attached to interaction.edit_original_response, the original interaction.message is the target.
            if hasattr(self, 'message') and self.message: # Check if self.message exists
                 await self.message.edit(content="Map selection timed out.", view=None)
            # elif hasattr(self, 'interaction') and self.interaction: # Fallback if self.interaction is stored
            #    await self.interaction.edit_original_response(content="Map selection timed out.", view=None)

        except discord.NotFound:
            pass # Message might have been deleted
        except Exception as e:
            pass # print(f"Error editing message on MapSelect timeout: {e}")

class RegionSelect(View):
    def __init__(self, fmt: str, mentions: list[str], guild_name: str, requester: discord.Member, guild: discord.Guild, subs: list[discord.Member], opponent_guild_name: str = None, challenge_data: dict = None):
        super().__init__(timeout=180)
        self.fmt = fmt
        self.mentions = mentions
        self.guild_name = guild_name
        self.opponent_guild_name = opponent_guild_name
        self.requester = requester
        self.guild = guild
        self.subs = subs
        self.challenge_data = challenge_data

    @classmethod
    async def create(cls, fmt: str, mentions: list[str], guild_name: str, requester: discord.Member, guild: discord.Guild, subs: list[discord.Member], opponent_guild_name: str = None, challenge_data: dict = None):
        view = cls(fmt, mentions, guild_name, requester, guild, subs, opponent_guild_name, challenge_data)
        
        options = []
        
        # Get servers from database instead of hardcoded list
        rcon_servers = await get_all_servers()

        if not rcon_servers:
            options.append(SelectOption(label="No servers available", value="no_servers_available", disabled=True))
        else:
            tasks = [get_server_status(s['address'], s['password']) for s in rcon_servers]
            results = await asyncio.gather(*tasks)

            for i, s_config in enumerate(rcon_servers):
                status = results[i]
                
                if not status.get("offline") and status['players'] <= 8:
                    label = f"{s_config['name']} ({status['players']}/{status['max_players']})"
                    description = "Ready to host a match."
                    options.append(SelectOption(
                        label=label,
                        value=s_config['name'],
                        description=description
                    ))

            if not options:
                options.append(SelectOption(label="No servers available", value="no_servers_available", disabled=True))

        sel = Select(
            placeholder="Select a game server region…",
            min_values=1, max_values=1,
            options=options,
            custom_id="region_select_dynamic"
        )
        sel.callback = view.on_region_selected
        view.add_item(sel)
        return view

    async def on_region_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()

        selected_region_key = self.children[0].values[0]
        if selected_region_key == "no_servers_available":
             await interaction.followup.send("There are no game servers configured.", ephemeral=True)
             return
        
        # Get server details from database instead of hardcoded list
        server_details = await get_server_by_name(selected_region_key)
        if not server_details:
            await interaction.followup.send(f"❌ Error: Could not find details for server '{selected_region_key}'.", ephemeral=True)
            return
            
        server_addr = server_details.get("address")
        server_passwd = server_details.get("password")

        try:
            status = await get_server_status(server_addr, server_passwd)
            if status.get("offline"):
                await interaction.followup.send(f"❌ Server '{selected_region_key}' is offline. Please choose another.", ephemeral=True)
                return
            if status['players'] > 8:
                await interaction.followup.send(
                    f"❌ **{selected_region_key}** has a match in progress ({status['players']}/{status['max_players']}). Please choose another server.",
                    ephemeral=True
                )
                return
            server_name = status.get('name', selected_region_key)
        except Exception as e:
            await interaction.followup.send(f"❌ **Error connecting to {selected_region_key}**: {e}", ephemeral=True)
            return

        map_select_view = MapSelect(
            self.fmt,
            selected_region_key,
            self.mentions,
            server_name,
            server_addr,
            self.guild_name,
            self.requester,
            self.guild,
            self.subs,
            self.opponent_guild_name,
            self.challenge_data
        )

        await interaction.followup.send(
            f"✅ Server `{server_name}` selected. Now, please select a map:", 
            view=map_select_view, 
            ephemeral=True
        )
        self.stop()

# ─── /ready COMMAND ──────────────────────────────────────────────────────

@bot.slash_command(
    name="ready",
    description="Check if teams are ready and proceed to server/map selection if so."
)
async def ready_slash(ctx: ApplicationContext):
    await ctx.defer(ephemeral=False) # Initial response can be edited
    guild_id = ctx.guild_id
    channel_id = ctx.channel_id
    state = get_channel_state(channel_id) # Current channel's state
    channel_context = await get_channel_context(guild_id, channel_id)
    context_type = channel_context.get("type")

    if not state:
        state = await init_state(guild_id, channel_id)
        if not state:
            # Changed to edit_original_response as defer is not ephemeral
            await ctx.interaction.edit_original_response(content="❌ Error initializing channel state. Cannot ready.")
            return

    # --- Determine Authoritative Game Type & Challenge Context ---
    authoritative_game_type = None
    active_challenge_for_this_channel = None
    is_initiator = False
    
    # These will be populated if a challenge is found and applies
    opponent_state_for_challenge = None 
    # state_for_readiness_check will hold the initiator's state if current channel is opponent in a challenge
    state_for_readiness_check = state # Default to current channel's state

    for ch_id, ch_data_item in active_challenges.items():
        if ch_data_item.get("status") == "accepted":
            if ch_data_item.get("initiating_channel_id") == channel_id:
                active_challenge_for_this_channel = ch_data_item
                authoritative_game_type = active_challenge_for_this_channel.get("game_type")
                is_initiator = True
                opponent_state_for_challenge = get_channel_state(active_challenge_for_this_channel.get("opponent_channel_id"))
                # state_for_readiness_check remains 'state' (current channel's state)
                break
            elif ch_data_item.get("opponent_channel_id") == channel_id:
                active_challenge_for_this_channel = ch_data_item
                authoritative_game_type = active_challenge_for_this_channel.get("game_type")
                is_initiator = False
                opponent_state_for_challenge = state # Current channel (opponent) state
                
                # Try to get initiator's state, and initialize it if it doesn't exist
                initiator_channel_id = active_challenge_for_this_channel.get("initiating_channel_id")
                state_for_readiness_check = get_channel_state(initiator_channel_id)
                
                if not state_for_readiness_check:
                    # Log the error for debugging
                    print(f"[READY CRITICAL ERROR] Could not retrieve initiator's state for challenge:")
                    print(f"  - Challenge ID: {active_challenge_for_this_channel.get('challenge_id', 'Unknown')}")
                    print(f"  - Initiator Channel ID: {initiator_channel_id}")
                    print(f"  - Initiator Guild ID: {active_challenge_for_this_channel.get('initiating_guild_id', 'Unknown')}")
                    print(f"  - Current Channel ID: {channel_id}")
                    print(f"  - Current Guild ID: {ctx.guild_id}")
                    
                    # Try to initialize the initiator's state if it doesn't exist
                    try:
                        initiator_guild_id = active_challenge_for_this_channel.get("initiating_guild_id")
                        if initiator_guild_id:
                            state_for_readiness_check = await init_state(initiator_guild_id, initiator_channel_id)
                            if not state_for_readiness_check:
                                await ctx.interaction.edit_original_response(
                                    content="❌ Critical Error: Could not retrieve or initialize initiator's state for an accepted challenge. "
                                    "The challenging team may need to sign up players first."
                                )
                                return
                        else:
                            await ctx.interaction.edit_original_response(
                                content="❌ Critical Error: Could not retrieve initiator's state for an accepted challenge. "
                                "Challenge data is missing initiator guild ID."
                            )
                            return
                    except Exception as e:
                        print(f"[READY ERROR] Failed to initialize initiator state for challenge: {e}")
                        await ctx.interaction.edit_original_response(
                            content="❌ Critical Error: Could not retrieve initiator's state for an accepted challenge. "
                            "The challenging team may need to sign up players first."
                        )
                        return
                break
    
    if active_challenge_for_this_channel:
        if not authoritative_game_type: # Handles None or empty string
            await ctx.interaction.edit_original_response(content=f"❌ Error: Game type is missing or invalid in the active challenge data (Challenge ID: {active_challenge_for_this_channel.get('challenge_id','N/A')}). Cannot proceed.")
            return
    else: # Standard matchmaking (not a challenge)
        if context_type in ["main_8s", "team_8s"]:
            authoritative_game_type = "8s"
        elif context_type in ["main_6s", "team_6s"]:
            authoritative_game_type = "6s"
        else: # Not a known channel type for matchmaking and not a challenge
            await ctx.interaction.edit_original_response(content=f"❌ This command can only be used in a designated 8s or 6s matchmaking channel, or as part of an active challenge. This channel's current type is '{context_type}'.")
            return

    # Final explicit check
    if not authoritative_game_type:
        await ctx.interaction.edit_original_response(content="❌ Critical Error: Could not determine the game type for the match. Please contact an admin.")
        print(f"[CRITICAL ERROR] ready_slash: authoritative_game_type is None/empty. channel_id: {channel_id}, context_type: {context_type}, active_challenge_id: {active_challenge_for_this_channel.get('challenge_id', 'None') if active_challenge_for_this_channel else 'None'}")
        return

    # --- Perform Readiness Checks ---
    are_teams_ready = False
    readiness_message = ""

    # Determine the correct initiator state for check_match_readiness
    # If it's a challenge and current channel is opponent, state_for_readiness_check is initiator's.
    # Otherwise (not a challenge, or current channel is initiator), state_for_readiness_check is current channel's state ('state').
    actual_initiator_state_for_check = state_for_readiness_check

    if SKIP_LINEUP_CHECKS_FOR_TESTING:
        readiness_message = "Teams considered ready for testing (checks skipped)."
        are_teams_ready = True
    elif active_challenge_for_this_channel:
        # 'actual_initiator_state_for_check' should be the true initiator's state.
        # 'opponent_state_for_challenge' should be the true opponent's state.
        
        # If current channel is initiator, actual_initiator_state_for_check is 'state'
        # opponent_state_for_challenge is opponent_state_for_challenge (fetched)
        
        # If current channel is opponent, actual_initiator_state_for_check is 'state_for_readiness_check' (fetched initiator)
        # opponent_state_for_challenge is 'state' (current channel state)
        
        # Ensure the variables passed to check_match_readiness are correctly assigned based on 'is_initiator'
        check_initiator = actual_initiator_state_for_check if not is_initiator else state
        check_opponent = state if not is_initiator else opponent_state_for_challenge
        
        # Additional validation and debugging
        print(f"[READY DEBUG] Challenge readiness check:")
        print(f"  - is_initiator: {is_initiator}")
        print(f"  - check_initiator exists: {check_initiator is not None}")
        print(f"  - check_opponent exists: {check_opponent is not None}")
        print(f"  - check_initiator teams: {len(check_initiator.get('teams', [])) if check_initiator else 0}")
        print(f"  - check_opponent teams: {len(check_opponent.get('teams', [])) if check_opponent else 0}")
        
        if not check_initiator:
             print(f"[READY ERROR] Initiator state missing for challenge {active_challenge_for_this_channel.get('challenge_id', 'Unknown')}")
             await ctx.interaction.edit_original_response(content="❌ Error: Initiator state data is missing for challenge readiness check.")
             return
        if not check_opponent:
             print(f"[READY ERROR] Opponent state missing for challenge {active_challenge_for_this_channel.get('challenge_id', 'Unknown')}")
             await ctx.interaction.edit_original_response(content="❌ Error: Opponent state data is missing for challenge readiness check. They may need to use /ready or sign up players.")
             return
        are_teams_ready, readiness_message = check_match_readiness(check_initiator, check_opponent, authoritative_game_type)
    else: # Standard matchmaking (not a challenge, not skipping checks)
         are_teams_ready, readiness_message = check_match_readiness(actual_initiator_state_for_check, None, authoritative_game_type)

    if not are_teams_ready:
        await ctx.interaction.edit_original_response(content=readiness_message)
        return
    
    # Proceeding message was ephemeral with initial defer, now edit original for public view
    # No, ctx.defer(ephemeral=False) means the original response is public and can be edited.
    # The "Proceeding..." message is effectively replaced by the RegionSelect view or an error.
    # --- Collect Player Mentions and Subs ---
    all_player_mentions_list = []
    subs_list_members = [] 

    home_guild_name_for_embed = ctx.guild.name 
    opponent_guild_name_for_embed = None
    teams_for_player_collection = []

    # Fetch states again for player collection to ensure freshness, especially for challenges
    s_initiator = None
    s_opponent = None

    if active_challenge_for_this_channel:
        initiator_channel_id = active_challenge_for_this_channel.get("initiating_channel_id")
        opponent_channel_id = active_challenge_for_this_channel.get("opponent_channel_id")
        
        s_initiator = get_channel_state(initiator_channel_id)
        s_opponent = get_channel_state(opponent_channel_id)
        
        # Debug logging for state retrieval
        print(f"[READY DEBUG] Challenge {active_challenge_for_this_channel.get('challenge_id', 'Unknown')}:")
        print(f"  - Initiator channel: {initiator_channel_id}, state exists: {s_initiator is not None}")
        print(f"  - Opponent channel: {opponent_channel_id}, state exists: {s_opponent is not None}")
        print(f"  - Current channel: {channel_id}, is_initiator: {is_initiator}")
        
        # Try to initialize missing states
        if not s_initiator and initiator_channel_id:
            try:
                initiator_guild_id = active_challenge_for_this_channel.get("initiating_guild_id")
                if initiator_guild_id:
                    s_initiator = await init_state(initiator_guild_id, initiator_channel_id)
                    print(f"  - Initialized initiator state: {s_initiator is not None}")
            except Exception as e:
                print(f"  - Failed to initialize initiator state: {e}")
        
        if not s_opponent and opponent_channel_id:
            try:
                opponent_guild_id = active_challenge_for_this_channel.get("opponent_guild_id")
                if opponent_guild_id:
                    s_opponent = await init_state(opponent_guild_id, opponent_channel_id)
                    print(f"  - Initialized opponent state: {s_opponent is not None}")
            except Exception as e:
                print(f"  - Failed to initialize opponent state: {e}")

        if s_initiator and s_initiator.get("teams"):
            teams_for_player_collection.append(s_initiator["teams"][0])
        if s_opponent and s_opponent.get("teams"):
            opp_team_idx = 0
            if s_opponent.get("context_type") in ["main_8s", "main_6s"] and \
               len(s_opponent["teams"]) > 1 and \
               s_opponent.get("is_challenged_by_team_name"): # Main guild accepted challenge as team 2
                opp_team_idx = 1
            if len(s_opponent["teams"]) > opp_team_idx: # Check index bounds
                teams_for_player_collection.append(s_opponent["teams"][opp_team_idx])
        
        if s_initiator and s_initiator.get("subs"):
            subs_list_members.extend(s_initiator.get("subs", []))
        if s_opponent and s_opponent.get("subs"):
            subs_list_members.extend(s_opponent.get("subs", []))

        initiator_guild_id = active_challenge_for_this_channel.get("initiating_guild_id")
        challenger_name_from_data = active_challenge_for_this_channel.get("initiating_team_name")
        opponent_guild_id = active_challenge_for_this_channel.get("opponent_guild_id")
        challenged_name_from_data = active_challenge_for_this_channel.get("opponent_team_name")

        guild_obj_initiator = bot.get_guild(initiator_guild_id) if initiator_guild_id else None
        guild_obj_opponent = bot.get_guild(opponent_guild_id) if opponent_guild_id else None

        # Names for embed based on who ran /ready (ctx.guild)
        if ctx.guild_id == initiator_guild_id: # Initiator's server ran /ready
            home_guild_name_for_embed = challenger_name_from_data or (guild_obj_initiator.name if guild_obj_initiator else "Challenger")
            opponent_guild_name_for_embed = challenged_name_from_data or (guild_obj_opponent.name if guild_obj_opponent else "Opponent")
        else: # Opponent's server ran /ready
            home_guild_name_for_embed = challenged_name_from_data or (guild_obj_opponent.name if guild_obj_opponent else "Challenger") # Home is current guild
            opponent_guild_name_for_embed = challenger_name_from_data or (guild_obj_initiator.name if guild_obj_initiator else "Opponent")

    else: # Standard matchmaking
        current_channel_full_state = get_channel_state(channel_id) 
        if current_channel_full_state and current_channel_full_state.get("teams"):
            teams_for_player_collection.extend(current_channel_full_state.get("teams"))
        if current_channel_full_state and current_channel_full_state.get("subs"):
            subs_list_members.extend(current_channel_full_state.get("subs", []))
        
        if len(teams_for_player_collection) > 1 : 
            home_guild_name_for_embed = "Team 1" 
            opponent_guild_name_for_embed = "Team 2"
        elif teams_for_player_collection: 
            home_guild_name_for_embed = channel_context.get("team_name", ctx.guild.name)
            opponent_guild_name_for_embed = None 
        else: 
            home_guild_name_for_embed = ctx.guild.name
            opponent_guild_name_for_embed = None
    
    for team_lineup in teams_for_player_collection:
        for player_data in team_lineup.values():
            if player_data:
                player_obj = player_data['player']
                if not is_text_player(player_obj) and hasattr(player_obj, 'mention'):
                    all_player_mentions_list.append(player_obj.mention)
                elif is_text_player(player_obj):
                    all_player_mentions_list.append(player_obj.display_name)
    
    all_player_mentions_list = list(dict.fromkeys(all_player_mentions_list))
    unique_subs_members = list(dict.fromkeys(subs_list_members)) 
    mentions_str = " ".join(all_player_mentions_list)
    
    # The initial response (from ctx.defer) will be edited to show this view.
    # We must use RegionSelect.create to correctly build the view with server statuses
    region_select_view = await RegionSelect.create(
        fmt=authoritative_game_type, 
        mentions=mentions_str, 
        guild_name=home_guild_name_for_embed, 
        requester=ctx.author, 
        guild=ctx.guild, 
        subs=unique_subs_members,
        opponent_guild_name=opponent_guild_name_for_embed,
        challenge_data=active_challenge_for_this_channel
    )
    
    await ctx.interaction.edit_original_response(
        content=f"✅ Match is Ready! {readiness_message}", 
        view=region_select_view
    )

    # Notify other participant in a challenge
    if active_challenge_for_this_channel:
        notification_channel_id = None
        notification_message = ""
        current_party_name = challenger_name_from_data if is_initiator else challenged_name_from_data
        other_party_name = challenged_name_from_data if is_initiator else challenger_name_from_data

        if not current_party_name: current_party_name = ctx.guild.name # Fallback for current party
        
        if is_initiator: # Current user's channel is initiator, notify opponent
            notification_channel_id = active_challenge_for_this_channel.get("opponent_channel_id")
            if not other_party_name: other_party_name = "Your team"
            notification_message = f"Team **{current_party_name}** (who challenged you) has used `/ready` and is now selecting server/map."
        else: # Current user's channel is opponent, notify initiator
            notification_channel_id = active_challenge_for_this_channel.get("initiating_channel_id")
            if not other_party_name: other_party_name = "The challenging team"
            notification_message = f"Team **{current_party_name}** (who you challenged) has used `/ready` and is now selecting server/map."

        if notification_channel_id and notification_message:
            try:
                notify_channel_obj = bot.get_channel(notification_channel_id)
                if notify_channel_obj:
                    await notify_channel_obj.send(f"📢 Heads up **{other_party_name}**! {notification_message} Please coordinate.")
            except Exception as e:
                print(f"[READY DEBUG] Error sending ready notification to other challenge participant: {e}")

# Removed handle_ready_logic function - it was deprecated and bypassed by ready_slash

async def check_lineup_readiness(team_lineup_dict: dict, positions_to_check: list, game_type: str) -> tuple[bool, int]:
    """Check if a single team's lineup is ready. Returns (is_ready, filled_count). GK is mandatory."""
    filled_count = 0
    gk_filled = False
    field_positions_needed = len([pos for pos in positions_to_check if pos != "GK"])
    field_positions_filled = 0

    for pos in positions_to_check:
        player_data = team_lineup_dict.get(pos)
        if player_data is not None:
            filled_count += 1
            if pos == "GK":
                gk_filled = True
            else:
                field_positions_filled += 1
    
    # For a team to be ready, GK must be filled and all field positions must be filled.
    # (This was the original stricter rule for /ready, /challenge had a more lenient version temporarily)
    is_ready = gk_filled and (field_positions_filled == field_positions_needed)
    return is_ready, filled_count

async def send_or_edit_ready_message(interaction: discord.Interaction, state: dict, content: str, embed: discord.Embed):
    """Sends or edits the persistent ready message with buttons."""
    ready_message_id = state.get("ready_message_id")
    # Pass guild_id and channel_id correctly to ReadyView from the interaction or state context
    view = ReadyView(interaction.guild_id, interaction.channel.id)
    channel_to_send_in = interaction.channel

    try:
        if ready_message_id:
            msg = await channel_to_send_in.fetch_message(ready_message_id)
            await msg.edit(content=content, embed=embed, view=view)
        else:
            msg = await channel_to_send_in.send(content=content, embed=embed, view=view)
            state["ready_message_id"] = msg.id
    except discord.NotFound:
        msg = await channel_to_send_in.send(content=content, embed=embed, view=view)
        state["ready_message_id"] = msg.id
    except discord.HTTPException as e:
        # Fallback: try to send a new message if edit fails for reasons other than NotFound
        if ready_message_id: # only try if we intended to edit
            try:
                msg = await channel_to_send_in.send(content=content, embed=embed, view=view)
                state["ready_message_id"] = msg.id            
            except discord.HTTPException as e2:
                pass # print(f"Fallback send also failed for ready message: {e2}")

# Removed start_and_clear_standard_match function - it was orphaned dead code with placeholder values
# The new system uses finish_standard_match_setup which correctly receives server_addr parameter

async def finish_standard_match_setup(interaction: discord.Interaction, initial_state: dict, context_type: str, server_addr: str):
    """Helper to finalize standard match: DM players, clear state, refresh lineup.
    Assumes the main match embed has already been sent."""
    channel = interaction.channel
    guild_id = channel.guild.id
    channel_id = channel.id

    team_name_display = "Team 1"
    opponent_display = "Team 2"
    if context_type in ["team_8s", "team_6s"]:
        # This part fetches team data, ensure get_team is available or adjust if not
        # from ios_bot.database_manager import get_team # Assuming get_team is available
        # team_data = get_team(guild_id) 
        # team_name_display = team_data.get("team_name", "Your Team") if team_data else "Your Team"
        # For simplicity if get_team is problematic to call here, use state's team_name or guild name
        team_name_display = initial_state.get("team_name", channel.guild.name) # team_name might be in state for team channels
        opponent_display = "CPU/Waiting"

    dms_sent_ids = set()
    all_member_objects = []
    for team_lineup in initial_state.get("teams", []):
        for player_data in team_lineup.values():
            if player_data and not is_text_player(player_data['player']) and hasattr(player_data['player'], 'send'):
                all_member_objects.append(player_data['player'])
    for sub_obj in initial_state.get("subs", []):
        if sub_obj and not is_text_player(sub_obj) and hasattr(sub_obj, 'send'):
            all_member_objects.append(sub_obj)
    
    unique_member_objects = list(dict.fromkeys(all_member_objects))

    connect_info_dm = f"Connect to [{server_addr}](https://iosoccer.com/connect/#{server_addr}) | Password is `iosmatch`"

    for member_obj in unique_member_objects:
        if member_obj.id not in dms_sent_ids:
            try:
                dm_embed = discord.Embed(
                    title="🏁 Your match is ready!",
                    description=(
                        f"Your match ({team_name_display} vs {opponent_display}) is now live.\n\n"
                        f"{connect_info_dm}"
                    ),
                    color=discord.Color.green()
                )
                await member_obj.send(embed=dm_embed)
                dms_sent_ids.add(member_obj.id)
            except Exception:
                pass 

    ready_message_id = initial_state.get("ready_message_id")
    clear_channel_state(channel_id)
    new_state = await init_state(guild_id, channel_id)
    try:
        await sm_refresh_lineup(channel, force_new_message=True if not new_state or not new_state.get("lineup_message_id") else False)
    except Exception as e:
        print(f"Error refreshing lineup in finish_standard_match_setup: {e}")

    if ready_message_id:
        try:
            old_msg = await channel.fetch_message(ready_message_id)
            await old_msg.delete()
        except: pass

async def start_and_clear_challenge_match(
    interaction_or_channel,
    challenge_data: dict,
    server_name: str,
    server_addr: str,
    initiating_guild_obj: discord.Guild,
    opponent_guild_obj: discord.Guild,
    requester_member: discord.Member
):
    """Handles match starting and state clearing for CHALLENGES, ensuring all parties are notified."""

    initiator_name = challenge_data["initiating_team_name"]
    opponent_name = challenge_data.get("opponent_team_name", "Opponent")
    game_type_display = challenge_data["game_type"].upper()

    main_embed = discord.Embed(
        title="⚔️ Challenge Match Starting! ⚔️",
        description=f"**{initiator_name}** vs **{opponent_name}** ({game_type_display}) is starting on **{server_name}**!",
        color=discord.Color.gold()
    )

    # Determine author icon based on the context of interaction_or_channel
    # If interaction_or_channel is an Interaction, use its guild. Otherwise, default to initiating_guild_obj.
    embed_author_guild = initiating_guild_obj # Default
    if isinstance(interaction_or_channel, discord.Interaction):
        # If the interaction happened in the opponent's guild, use opponent's icon
        if opponent_guild_obj and interaction_or_channel.guild_id == opponent_guild_obj.id:
            embed_author_guild = opponent_guild_obj
        # Else if interaction happened in initiator's guild (or guild_id matches initiator), use initiator's icon
        elif initiating_guild_obj and interaction_or_channel.guild_id == initiating_guild_obj.id:
            embed_author_guild = initiating_guild_obj
    
    if embed_author_guild and embed_author_guild.icon:
        main_embed.set_author(name=f"{embed_author_guild.name} - Match Starting", icon_url=embed_author_guild.icon.url)
    else:
        main_embed.set_author(name="Match Starting")

    main_embed.add_field(
        name="🔗 Connect Info",
        value=f"Connect to [{server_addr}](https://iosoccer.com/connect/#{server_addr}) | Password is `iosmatch`",
        inline=False
    )
    main_embed.set_footer(
        text=f"Match finalized by {requester_member.display_name}. Good luck to both teams!",
        icon_url=requester_member.display_avatar.url if requester_member.display_avatar else None
    )
    main_embed.timestamp = datetime.now(timezone.utc)

    initiator_channel_id = challenge_data["initiating_channel_id"]
    initiator_channel = bot.get_channel(initiator_channel_id) if initiator_channel_id else None
    initiator_state = get_channel_state(initiator_channel_id) if initiator_channel_id else None
    
    opponent_channel_id = challenge_data.get("opponent_channel_id")
    opponent_channel = bot.get_channel(opponent_channel_id) if opponent_channel_id else None
    opponent_state = get_channel_state(opponent_channel_id) if opponent_channel_id else None

    # Add Lineups to Embed
    if initiator_state and initiator_state.get("teams"):
        initiator_lineup_str = await format_lineup(
            initiator_state["teams"][0],
            initiator_channel_id,
            challenge_data.get("initiating_guild_id")
        )
        main_embed.add_field(name=f"{initiator_name}'s Lineup", value=f"```{initiator_lineup_str}```", inline=True)
    else:
        main_embed.add_field(name=f"{initiator_name}'s Lineup", value="```Lineup not available.```", inline=True)

    if opponent_state and opponent_state.get("teams"):
        # Determine which team in opponent_state is relevant (usually teams[0], but could be teams[1] if Main Guild is opponent)
        opponent_team_lineup_data = opponent_state["teams"][0]
        if challenge_data.get("opponent_guild_id") == MAIN_GUILD_ID and len(opponent_state.get("teams", [])) > 1:
            # If opponent is Main Guild and has a Team 2 structure from a challenge context
            if opponent_state.get("is_challenged_by_team_name"): # This flag indicates main guild is T2 in this state
                 opponent_team_lineup_data = opponent_state["teams"][0]


        opponent_lineup_str = await format_lineup(
            opponent_team_lineup_data,
            opponent_channel_id,
            challenge_data.get("opponent_guild_id")
        )
        main_embed.add_field(name=f"{opponent_name}'s Lineup", value=f"```{opponent_lineup_str}```", inline=True)
    else:
        main_embed.add_field(name=f"{opponent_name}'s Lineup", value="```Lineup not available.```", inline=True)

    # Notify Initiator's Channel & DM Initiator's Team
    if initiator_channel:
        initiator_mentions = get_all_member_objects_from_state(initiator_state)
        mention_str_init = " ".join(m.mention for m in initiator_mentions)
        try:
            await initiator_channel.send(content=f"Match Starting! {mention_str_init}", embed=main_embed)
            for member_obj in initiator_mentions:
                try: await member_obj.send(embed=main_embed)
                except: pass # Ignore DM errors
        except Exception as e:
            print(f"[Challenge Start] Error notifying initiator channel/DMs: {e}")
        clear_channel_state(initiator_channel_id)

    # Notify Opponent's Channel & DM Opponent's Team (including Main Guild)
    if opponent_channel and opponent_channel_id != MAIN_GUILD_ID:
        opponent_mentions = get_all_member_objects_from_state(opponent_state)
        mention_str_opp = " ".join(m.mention for m in opponent_mentions)
        try:
            await opponent_channel.send(content=f"Match Starting! {mention_str_opp}", embed=main_embed)
            for member_obj in opponent_mentions:
                try: await member_obj.send(embed=main_embed)
                except: pass # Ignore DM errors
        except Exception as e:
            print(f"[Challenge Start] Error notifying opponent channel/DMs: {e}")
        clear_channel_state(opponent_channel_id)

    # Global Announcement
    initiating_channel_mention = initiator_channel.mention if initiator_channel else f"Channel ID: {initiator_channel_id}"
    try:
        await announce_match_ready(
            home_team_name=initiator_name,
            opponent_team_name=opponent_name,
            game_type=challenge_data["game_type"],
            initiating_channel_mention=initiating_channel_mention,
            embed_to_send=main_embed,
            content_to_send=None
        )
    except Exception as e:
        print(f"[Challenge Start] Error in announce_match_ready: {e}")

    # Clear challenge from active_challenges
    challenge_id_to_remove = challenge_data.get("challenge_id")
    if challenge_id_to_remove and challenge_id_to_remove in active_challenges:
        del active_challenges[challenge_id_to_remove]
        print(f"Challenge {challenge_id_to_remove} cleared after match start.")
    else:
        print(f"Could not find challenge ID {challenge_id_to_remove} in active_challenges to clear.")

    # Delete the MapSelect view message if this was triggered by an Interaction
    if isinstance(interaction_or_channel, discord.Interaction):
        try:
            # Check if message object exists on interaction. If interaction was deferred and then a new message sent,
            # interaction.message might be None. The MapSelect view is typically on the message that was edited.
            if interaction_or_channel.message: 
                await interaction_or_channel.message.delete()
        except discord.NotFound:
            pass # Message already deleted
        except Exception as e:
            # print(f"[Challenge Start] Error deleting original MapSelect message: {e}")
            pass
