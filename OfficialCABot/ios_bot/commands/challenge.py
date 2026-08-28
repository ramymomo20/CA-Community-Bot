from ios_bot.config import *
import ios_bot.config as config_module
import time as clock
from ios_bot.signup_manager import get_channel_state, init_state, format_lineup, refresh_lineup as sm_refresh_lineup, get_channel_context # MODIFIED
from ios_bot.challenge_manager import active_challenges, broadcast_challenge_cooldowns
from datetime import datetime
from ios_bot.semaphores import challenge_semaphore


async def _safe_challenge_message(interaction: discord.Interaction, content: str, *, ephemeral: bool = False, view=None):
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(content, ephemeral=ephemeral, view=view)
        return await interaction.response.send_message(content, ephemeral=ephemeral, view=view)
    except (discord.InteractionResponded, discord.NotFound):
        pass
    except Exception:
        pass

    try:
        if interaction.channel:
            return await interaction.channel.send(content)
    except Exception:
        pass
    return None


def _challenge_initiating_guild_id(challenge_data: dict) -> int | None:
    value = challenge_data.get("initiating_guild_id")
    if value is None:
        value = challenge_data.get("initiating_team_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

# --- Helper function to check if a team's lineup is full --- #
def is_lineup_full(state: dict, context_type: str) -> bool:
    if not state or not state.get("teams"):
        return False
    
    team_lineup = state["teams"][0]
    
    if context_type == "team_8s":
        positions_to_check = EIGHTS_POSITIONS
    elif context_type == "team_6s":
        positions_to_check = SIXES_POSITIONS
    elif context_type == "team_5s":
        positions_to_check = FIVES_POSITIONS
    else:
        return False

    for pos in positions_to_check:
        if pos == "GK":
            continue
        player_data = team_lineup.get(pos)
        if player_data is None:
            return False
    return True

class ChallengeTargetSelect(Select):
    def __init__(self, placeholder: str, options: list[SelectOption], custom_id_prefix: str):
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=1,
            custom_id=f"{custom_id_prefix}_target_select"
        )
    # Callback will be handled by the view that uses this select

# --- Helper function to check if a main channel is already challenged --- #
def is_main_channel_challenged(main_channel_id: int) -> tuple[bool, str]:
    """
    Check if a main channel is already challenged.
    Returns (is_challenged, challenger_name)
    """
    # Check active challenges
    for ch_id, ch_data in active_challenges.items():
        # Check if this channel is the opponent in an accepted challenge
        if (ch_data.get("opponent_guild_id") == config_module.MAIN_GUILD_ID and 
            ch_data.get("opponent_channel_id") == main_channel_id and 
            ch_data.get("status") == "accepted"):
            return True, ch_data.get("initiating_team_name", "another team")
        
        # Check if this channel is the target of a pending challenge
        if (ch_data.get("target_id") == config_module.MAIN_GUILD_ID and 
            ch_data.get("target_channel_id_for_main", 0) == main_channel_id and 
            ch_data.get("status") == "pending_direct"):
            return True, ch_data.get("initiating_team_name", "another team")
    
    return False, ""

# --- Helper function to clear challenge flags from a main channel --- #
def clear_main_channel_challenge_flags(main_channel_state: dict) -> None:
    """
    Clear challenge flags from a main channel state.
    """
    if "is_challenged_by_team_name" in main_channel_state:
        del main_channel_state["is_challenged_by_team_name"]
    if "active_challenge_game_type" in main_channel_state:
        del main_channel_state["active_challenge_game_type"]


def _empty_positions_for_game_type(game_type: str) -> dict:
    if game_type == "8s":
        positions = EIGHTS_POSITIONS
    elif game_type == "6s":
        positions = SIXES_POSITIONS
    else:
        positions = FIVES_POSITIONS
    return {position: None for position in positions}


def _snapshot_team_slots(team_entries: list | None) -> list:
    snapshot = []
    for entry in team_entries or []:
        snapshot.append(dict(entry) if isinstance(entry, dict) else entry)
    return snapshot

class ChallengeAcceptView(View):
    """View for teams receiving a challenge, allowing them to Accept or Ignore."""
    def __init__(self, challenge_id: str, challenged_team_id: int, game_type: str):
        super().__init__(timeout=3600) # Challenge stands for 1 hour for acceptance
        self.challenge_id = challenge_id
        self.challenged_team_id = challenged_team_id # The ID of the team receiving this view
        self.game_type = game_type

        self.accept_button = Button(label="Accept Challenge", style=discord.ButtonStyle.success, custom_id=f"accept_challenge_{challenge_id}")
        self.accept_button.callback = self.accept_callback_safe
        self.add_item(self.accept_button)

        self.ignore_button = Button(label="Ignore Challenge", style=discord.ButtonStyle.secondary, custom_id=f"ignore_challenge_{challenge_id}")
        self.ignore_button.callback = self.ignore_callback
        self.add_item(self.ignore_button)
    
    async def accept_callback_safe(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded:
            return
        except discord.NotFound:
            return

        if interaction.guild_id != self.challenged_team_id:
            await interaction.followup.send("This acceptance is not valid for your team.", ephemeral=True)
            return

        accepting_channel_context = await get_channel_context(interaction.guild_id, interaction.channel_id)
        if accepting_channel_context["type"] != f"team_{self.game_type}":
            await interaction.followup.send(
                f"Please accept from one of your team's registered {self.game_type.upper()} matchmaking channel.",
                ephemeral=True,
            )
            return

        accepting_lineup_state = get_channel_state(interaction.channel_id)
        if not accepting_lineup_state:
            accepting_lineup_state = await init_state(interaction.guild_id, interaction.channel_id)

        if not is_lineup_full(accepting_lineup_state, accepting_channel_context["type"]):
            await interaction.followup.send(
                f"Your team's {self.game_type.upper()} lineup in this channel must be full (excluding GK) to accept this challenge.",
                ephemeral=True,
            )
            return

        async with challenge_semaphore:
            challenge_data = active_challenges.get(self.challenge_id)
            if not challenge_data or challenge_data.get("status") not in ["pending_broadcast", "pending_direct"]:
                try:
                    await interaction.message.edit(
                        content="This challenge is no longer active or has already been accepted.",
                        view=None,
                    )
                except discord.HTTPException:
                    pass
                await _safe_challenge_message(interaction, "Challenge is no longer active.", ephemeral=True)
                return

            try:
                await interaction.message.edit(content="Challenge accepted! Processing...", view=None)
            except discord.HTTPException:
                pass

            previous_status = challenge_data.get("status")
            previous_opponent_guild_id = challenge_data.get("opponent_guild_id")
            previous_opponent_channel_id = challenge_data.get("opponent_channel_id")
            previous_opponent_team_name = challenge_data.get("opponent_team_name")
            previous_accepted_timestamp = challenge_data.get("accepted_timestamp")

            challenge_data["status"] = "accepted"
            challenge_data["accepted_timestamp"] = datetime.now()
            challenge_data["opponent_guild_id"] = interaction.guild_id
            challenge_data["opponent_channel_id"] = interaction.channel_id

            accepting_team_details = await bot.db.teams.get_team(interaction.guild_id)
            if not accepting_team_details:
                challenge_data["status"] = previous_status
                challenge_data["opponent_guild_id"] = previous_opponent_guild_id
                challenge_data["opponent_channel_id"] = previous_opponent_channel_id
                challenge_data["opponent_team_name"] = previous_opponent_team_name
                if previous_accepted_timestamp is None:
                    challenge_data.pop("accepted_timestamp", None)
                else:
                    challenge_data["accepted_timestamp"] = previous_accepted_timestamp
                active_challenges[self.challenge_id] = challenge_data
                await interaction.followup.send(
                    "Error: Could not retrieve your team's details to accept the challenge. Please try again.",
                    ephemeral=False,
                )
                return

            challenge_data["opponent_team_name"] = accepting_team_details["guild_name"]
            initiating_team_name = challenge_data["initiating_team_name"]
            accepting_team_name = challenge_data["opponent_team_name"]
            game_type_display = challenge_data["game_type"].upper()
            broadcast_messages = dict(challenge_data.get("broadcast_messages") or {})
            challenge_data["broadcast_messages"] = {}
            active_challenges[self.challenge_id] = challenge_data

        try:
            initiating_guild = bot.get_guild(challenge_data["initiating_guild_id"])
            initiating_channel = initiating_guild.get_channel(challenge_data["initiating_channel_id"]) if initiating_guild else None
            if initiating_channel:
                await initiating_channel.send(
                    f"Your {game_type_display} challenge has been accepted by **{accepting_team_name}**. Match on!"
                )
                await sm_refresh_lineup(initiating_channel, author_override=interaction.user, force_new_message=True)
        except Exception as e:
            print(f"Error notifying/refreshing initiating team on accept: {e}")

        await interaction.followup.send(
            f"Challenge accepted. You are now playing against **{initiating_team_name}** in a {game_type_display} match. Good luck!",
            ephemeral=False,
        )

        try:
            await sm_refresh_lineup(interaction.channel, author_override=interaction.user, force_new_message=True)
        except Exception as e:
            print(f"Error refreshing accepting team lineup on accept: {e}")

        if challenge_data.get("target_type") == "broadcast":
            for ch_id, msg_id in broadcast_messages.items():
                if ch_id == interaction.channel_id:
                    continue
                try:
                    broadcast_channel = bot.get_channel(ch_id)
                    if broadcast_channel:
                        msg_to_edit = await broadcast_channel.fetch_message(msg_id)
                        await msg_to_edit.edit(
                            content=(
                                f"The {game_type_display} challenge from **{initiating_team_name}** "
                                f"was accepted by **{accepting_team_name}**."
                            ),
                            embed=None,
                            view=None,
                        )
                except discord.NotFound:
                    pass
                except Exception as e:
                    print(f"Error editing broadcast message {msg_id} in channel {ch_id}: {e}")

        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass

    async def ignore_callback(self, interaction: discord.Interaction):
        try:
            # Try to defer publicly if we intend to send a public message, or ephemerally if not.
            # Given "Challenge ignored." can be public, let's try public defer.
            await interaction.response.defer(ephemeral=False) 
        except discord.InteractionResponded:
            # If already responded, perhaps another action (like accept) happened, or double click.
            # We might not need to do anything further or could send a followup.
            try:
                await interaction.followup.send("This interaction was already processed.", ephemeral=True)
            except: pass # Best effort
            return
        except discord.NotFound: # Original interaction gone
            return

        challenge_data = active_challenges.get(self.challenge_id)
        
        if not challenge_data or challenge_data["status"] not in ["pending_broadcast", "pending_direct"]:
            try: 
                await interaction.message.edit(content="This challenge is no longer active or has already been addressed.", view=None)
            except discord.HTTPException:
                pass
            await _safe_challenge_message(interaction, "This challenge is no longer active or has already been addressed.", ephemeral=True)
            return
        
        initiating_team_name = challenge_data["initiating_team_name"]
        game_type_display = challenge_data["game_type"].upper()
        ignored_by_team_name = interaction.guild.name

        message_deleted = False
        try:
            await interaction.message.delete()
            message_deleted = True
        except discord.HTTPException:
            print(f"Warning: Could not delete original challenge message {interaction.message.id} on ignore.")

        if challenge_data["target_type"] == "team" and challenge_data["target_id"] == interaction.guild_id:
            challenge_data["status"] = "declined"
            try:
                initiating_guild = bot.get_guild(challenge_data["initiating_guild_id"])
                initiating_channel = initiating_guild.get_channel(challenge_data["initiating_channel_id"])
                if initiating_channel:
                    await initiating_channel.send(f"ℹ️ Team **{ignored_by_team_name}** has **DECLINED** your {game_type_display} challenge.")
                    await sm_refresh_lineup(initiating_channel, author_override=interaction.user, force_new_message=True)
            except Exception as e:
                print(f"Error notifying initiator of declined challenge {self.challenge_id}: {e}")
            
            await _safe_challenge_message(
                interaction,
                f"Challenge from **{initiating_team_name}** has been **DECLINED** by your team.",
                ephemeral=False,
            )
            
            if self.challenge_id in active_challenges:
                del active_challenges[self.challenge_id]
        
        elif challenge_data["target_type"] == "broadcast":
            await _safe_challenge_message(
                interaction,
                f"Your team has chosen to **IGNORE** the {game_type_display} broadcast challenge from **{initiating_team_name}**. Other teams may still accept.",
                ephemeral=False,
            )
        else:
            await _safe_challenge_message(interaction, "Challenge ignored.", ephemeral=False)
        
        # If original message wasn't deleted and we didn't use edit_original_response for the final outcome,
        # ensure view is cleaned up on the original message.
        # However, the logic above now uses edit_original_response for the final state.
        # If message_deleted is False, it means interaction.message.delete() failed.
        # The interaction.edit_original_response above should handle updating the (now buttonless) message.

class ChallengeView(View):
    def __init__(self, author_id: int, initiating_team_id: int, initiating_channel_id: int, game_type: str):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.initiating_team_id = initiating_team_id
        self.initiating_channel_id = initiating_channel_id
        self.game_type = game_type # "6s" or "8s"
        self.selected_target_type: str = None 
        self.selected_target_id: int | str = None
        self.selected_target_name: str = None
        self.specific_main_channel_select = None # Placeholder for the new select
        self.team_target_select = None

        # Only show options for the current game type
        options = [
            SelectOption(label=f"Challenge a Specific {self.game_type.upper()} Team", value="direct_team", description=f"Send this challenge to one registered team."),
            SelectOption(label=f"Broadcast to all {self.game_type.upper()} Teams", value="broadcast_all", description=f"Challenge any available registered team."),
            SelectOption(label=f"Challenge Main {self.game_type.upper()} Channel", value=f"main_channel_{self.game_type}", description=f"Challenge the main guild's matchmaking channel.")
        ]
        self.target_type_select = Select(placeholder=f"Choose challenge target type for {self.game_type.upper()}...", options=options, custom_id="challenge_target_type")
        self.target_type_select.callback = self.on_target_type_selected
        self.add_item(self.target_type_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def on_target_type_selected(self, interaction: discord.Interaction):
        chosen_type_value = interaction.data["values"][0]
        await interaction.response.defer()

        # Remove confirm button and specific main channel select if they exist from a previous selection
        if hasattr(self, 'confirm_challenge_button') and self.confirm_challenge_button in self.children:
            self.remove_item(self.confirm_challenge_button)
        if self.specific_main_channel_select and self.specific_main_channel_select in self.children:
            self.remove_item(self.specific_main_channel_select)
            self.specific_main_channel_select = None
        if self.team_target_select and self.team_target_select in self.children:
            self.remove_item(self.team_target_select)
            self.team_target_select = None

        if chosen_type_value == "direct_team":
            self.selected_target_type = "team"
            self.selected_target_id = None
            self.selected_target_name = None
            self.target_type_select.disabled = True

            all_teams = await bot.db.teams.get_all_teams_with_channels()
            eligible_teams = []
            for team_data in all_teams:
                guild_id = team_data.get("guild_id")
                if guild_id in (None, self.initiating_team_id, config_module.MAIN_GUILD_ID):
                    continue

                if self.game_type == "8s":
                    team_channels = team_data.get("eights_channels", [])
                elif self.game_type == "6s":
                    team_channels = team_data.get("sixes_channels", [])
                else:
                    team_channels = team_data.get("fives_channels", [])

                if not team_channels:
                    continue

                eligible_teams.append(team_data)

            eligible_teams.sort(key=lambda team: str(team.get("guild_name") or "").lower())
            if not eligible_teams:
                self.target_type_select.disabled = False
                self.selected_target_type = None
                await interaction.edit_original_response(
                    content=f"No registered {self.game_type.upper()} teams are available for direct challenge.",
                    view=self,
                )
                return

            options = []
            for team_data in eligible_teams[:25]:
                if self.game_type == "8s":
                    team_channels = team_data.get("eights_channels", [])
                elif self.game_type == "6s":
                    team_channels = team_data.get("sixes_channels", [])
                else:
                    team_channels = team_data.get("fives_channels", [])

                channel_count = len(team_channels)
                options.append(
                    SelectOption(
                        label=str(team_data.get("guild_name") or f"Team {team_data.get('guild_id')}")[:100],
                        value=str(int(team_data["guild_id"])),
                        description=f"{channel_count} registered {self.game_type.upper()} channel(s)"[:100],
                    )
                )

            self.team_target_select = Select(
                placeholder=f"Select a {self.game_type.upper()} team...",
                options=options,
                custom_id="specific_team_challenge_select",
            )
            self.team_target_select.callback = self.on_team_target_selected
            self.add_item(self.team_target_select)

            overflow_note = ""
            if len(eligible_teams) > 25:
                overflow_note = f" Showing the first 25 of {len(eligible_teams)} eligible teams."
            await interaction.edit_original_response(
                content=f"Select the team you want to challenge directly.{overflow_note}",
                view=self,
            )
        elif chosen_type_value == "broadcast_all":
            self.selected_target_type = "broadcast"
            self.selected_target_id = None # No specific ID for broadcast
            self.selected_target_name = f"All {self.game_type.upper()} Teams (Broadcast)"
            self.target_type_select.disabled = True
            self.add_confirm_button(f"Confirm Broadcast {self.game_type.upper()} Challenge?")
            await interaction.edit_original_response(content=f"You've selected to broadcast the {self.game_type.upper()} challenge.", view=self)
        elif chosen_type_value.startswith("main_channel_"):
            self.selected_target_type = "main_channel"
            main_channels_ids = (
                config_module.EIGHTS_MAIN_MATCHMAKING_CHANNELS
                if self.game_type == "8s"
                else config_module.SIXES_MAIN_MATCHMAKING_CHANNELS
                if self.game_type == "6s"
                else config_module.FIVES_MAIN_MATCHMAKING_CHANNELS
            )
            if not main_channels_ids:
                await interaction.edit_original_response(content=f"❌ Error: No main channels configured for {self.game_type.upper()}.", view=None)
                return
            self.target_type_select.disabled = True
            if len(main_channels_ids) == 1:
                self.selected_target_id = main_channels_ids[0]
                try:
                    target_channel_obj = bot.get_channel(self.selected_target_id)
                    self.selected_target_name = target_channel_obj.name if target_channel_obj else f"Main Channel ID {self.selected_target_id}"
                except Exception as e:
                    print(f"Error fetching main channel name: {e}")
                    self.selected_target_name = f"Main {self.game_type.upper()} Channel (ID: {self.selected_target_id})"
                self.add_confirm_button(f"Challenge {self.selected_target_name}?")
                await interaction.edit_original_response(content=f"You've selected to challenge: **{self.selected_target_name}**.", view=self)
            else:
                # Remove duplicates and ensure unique channel IDs
                unique_channel_ids = list(dict.fromkeys(main_channels_ids))  # Preserves order while removing duplicates
                
                options = []
                used_values = set()  # Track used values to prevent duplicates
                
                for ch_id in unique_channel_ids:
                    channel_obj = bot.get_channel(ch_id)
                    channel_name = channel_obj.name if channel_obj else f"Channel ID {ch_id}"
                    option_value = str(ch_id)
                    
                    # Ensure unique option values
                    if option_value in used_values:
                        # If duplicate value, append a suffix to make it unique
                        counter = 1
                        while f"{option_value}_{counter}" in used_values:
                            counter += 1
                        option_value = f"{option_value}_{counter}"
                    
                    options.append(SelectOption(label=channel_name, value=option_value))
                    used_values.add(option_value)
                
                if not options:
                    await interaction.edit_original_response(content=f"❌ Error: Could not find details for configured main channels.", view=None)
                    return
                self.specific_main_channel_select = Select(
                    placeholder=f"Select specific Main {self.game_type.upper()} channel...",
                    options=options,
                    custom_id="specific_main_channel_select"
                )
                self.specific_main_channel_select.callback = self.on_specific_main_channel_selected
                self.add_item(self.specific_main_channel_select)
                await interaction.edit_original_response(content=f"Multiple Main {self.game_type.upper()} channels found. Please choose one:", view=self)

    async def on_team_target_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_value = interaction.data["values"][0]
        self.selected_target_id = int(selected_value)

        target_team = await bot.db.teams.get_team(self.selected_target_id)
        self.selected_target_name = (
            target_team.get("guild_name")
            if target_team
            else f"Team ID {self.selected_target_id}"
        )

        if self.team_target_select:
            self.team_target_select.disabled = True
        self.add_confirm_button(f"Challenge {self.selected_target_name}?")
        await interaction.edit_original_response(
            content=f"You've selected to challenge: **{self.selected_target_name}**.",
            view=self,
        )

    async def on_specific_main_channel_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_value = interaction.data["values"][0]
        print(f"[CHALLENGE DEBUG] Selected value: {selected_value}")
        
        # Handle potential suffix in option values (e.g., "123456_1")
        if "_" in selected_value:
            # Extract the actual channel ID before the suffix
            self.selected_target_id = int(selected_value.split("_")[0])
        else:
            self.selected_target_id = int(selected_value)
            
        try:
            target_channel_obj = bot.get_channel(self.selected_target_id)
            self.selected_target_name = target_channel_obj.name if target_channel_obj else f"Main Channel ID {self.selected_target_id}"
        except Exception as e:
            print(f"Error fetching specific main channel name: {e}")
            self.selected_target_name = f"Main {self.game_type.upper()} Channel (ID: {self.selected_target_id})"
        if self.specific_main_channel_select:
            self.specific_main_channel_select.disabled = True
        self.add_confirm_button(f"Challenge {self.selected_target_name}?")
        await interaction.edit_original_response(content=f"You've selected to challenge: **{self.selected_target_name}**.", view=self)

    def add_confirm_button(self, label: str = "Confirm Challenge"):
        if hasattr(self, 'confirm_challenge_button') and self.confirm_challenge_button in self.children:
            self.remove_item(self.confirm_challenge_button)
        self.confirm_challenge_button = Button(label=label, style=discord.ButtonStyle.success, custom_id="confirm_issue_challenge")
        self.confirm_challenge_button.callback = self.confirm_issue_challenge_safe
        self.add_item(self.confirm_challenge_button)
    
    async def confirm_issue_challenge_safe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not self.selected_target_type:
            await interaction.followup.send("Please select a valid challenge target first.", ephemeral=True)
            return

        initiating_team_id = self.initiating_team_id

        async with challenge_semaphore:
            if self.selected_target_type == "broadcast":
                cooldown_period = 600
                if initiating_team_id in broadcast_challenge_cooldowns:
                    last_broadcast_time = broadcast_challenge_cooldowns[initiating_team_id]
                    time_since_last_broadcast = clock.time() - last_broadcast_time
                    if time_since_last_broadcast < cooldown_period:
                        remaining_time = cooldown_period - time_since_last_broadcast
                        await interaction.followup.send(
                            f"You must wait {int(remaining_time // 60)} minutes and {int(remaining_time % 60)} seconds "
                            "before broadcasting another challenge.",
                            ephemeral=True,
                        )
                        return

            initiating_team_details = await bot.db.teams.get_team(self.initiating_team_id)
            if not initiating_team_details:
                await interaction.followup.send("Error: Could not retrieve your team data.", ephemeral=True)
                return

            initiating_team_name = initiating_team_details.get("guild_name", "Your Team")

            for ch_data in active_challenges.values():
                if (
                    _challenge_initiating_guild_id(ch_data) == self.initiating_team_id
                    and ch_data.get("game_type") == self.game_type
                    and ch_data.get("status") in ["pending_broadcast", "pending_direct", "accepted"]
                ):
                    status_text = "outgoing" if ch_data["status"] in ["pending_broadcast", "pending_direct"] else "active"
                    await interaction.followup.send(
                        f"Your team already has a {status_text} {self.game_type.upper()} challenge. "
                        "Please `/unchallenge` first or wait for it to resolve.",
                        ephemeral=False,
                    )
                    return

            for ch_data in active_challenges.values():
                if (
                    ch_data.get("opponent_guild_id") == self.initiating_team_id
                    and ch_data.get("game_type") == self.game_type
                    and ch_data.get("status") == "accepted"
                ):
                    opponent_team = ch_data.get("initiating_team_name", "another team")
                    await interaction.followup.send(
                        f"Your team is already in an active {self.game_type.upper()} challenge against **{opponent_team}**. "
                        "Please `/unchallenge` first or wait for it to resolve.",
                        ephemeral=False,
                    )
                    return

            initiating_channel_obj = bot.get_channel(self.initiating_channel_id)
            initiating_lineup_state = get_channel_state(self.initiating_channel_id)
            if not initiating_lineup_state:
                initiating_lineup_state = await init_state(self.initiating_team_id, self.initiating_channel_id)

            await get_channel_context(self.initiating_team_id, self.initiating_channel_id)

            challenge_id = f"challenge_{self.initiating_team_id}_{int(datetime.now().timestamp())}"
            new_challenge_data = {
                "challenge_id": challenge_id,
                "initiating_team_id": self.initiating_team_id,
                "initiating_guild_id": self.initiating_team_id,
                "initiating_channel_id": self.initiating_channel_id,
                "initiating_team_name": initiating_team_name,
                "game_type": self.game_type,
                "target_type": self.selected_target_type,
                "target_id": self.selected_target_id,
                "target_name": self.selected_target_name,
                "status": "pending_direct",
                "timestamp": datetime.now(),
                "broadcast_messages": {},
                "opponent_guild_id": None,
                "opponent_channel_id": None,
                "opponent_team_name": None,
            }

            final_followup_message = ""
            view_for_target = ChallengeAcceptView(
                challenge_id,
                self.selected_target_id if self.selected_target_type == "team" else None,
                self.game_type,
            )

            if self.selected_target_type == "broadcast":
                new_challenge_data["status"] = "pending_broadcast"
                active_challenges[challenge_id] = new_challenge_data

                all_teams = await bot.db.teams.get_all_teams_with_channels()
                broadcast_count = 0
                challenge_embed = Embed(
                    title=f"Open {self.game_type.upper()} Challenge!",
                    description=f"Team **{initiating_team_name}** is issuing an open challenge for a {self.game_type.upper()} match!",
                    color=discord.Color.blue(),
                )
                challenge_embed.set_footer(text=f"Challenge ID: {challenge_id}. Your team can accept.")

                for team_data in all_teams:

                    if active_challenges.get(challenge_id, {}).get("status") != "pending_broadcast":
                        break

                    if team_data["guild_id"] == self.initiating_team_id:
                        continue

                    if any(
                        c.get("status") == "accepted"
                        and (
                            _challenge_initiating_guild_id(c) == team_data["guild_id"]
                            or c.get("opponent_guild_id") == team_data["guild_id"]
                        )
                        for c in active_challenges.values()
                    ):
                        continue

                    if self.game_type == "8s":
                        team_channels = team_data.get("eights_channels", [])
                    elif self.game_type == "6s":
                        team_channels = team_data.get("sixes_channels", [])
                    elif self.game_type == "5s":
                        team_channels = team_data.get("fives_channels", [])
                    else:
                        team_channels = []

                    for target_ch_id in team_channels:
                        if active_challenges.get(challenge_id, {}).get("status") != "pending_broadcast":
                            break

                        target_channel_obj = bot.get_channel(target_ch_id)
                        if not target_channel_obj:
                            continue

                        try:
                            sent_msg = await target_channel_obj.send(
                                content=f"Attention Captains/VCs of **{team_data['guild_name']}**!",
                                embed=challenge_embed,
                                view=ChallengeAcceptView(challenge_id, team_data["guild_id"], self.game_type),
                            )
                            new_challenge_data["broadcast_messages"][target_ch_id] = sent_msg.id
                            active_challenges[challenge_id] = new_challenge_data
                            broadcast_count += 1
                        except Exception as e:
                            print(f"Error broadcasting challenge to {team_data['guild_name']} in channel {target_ch_id}: {e}")

                if broadcast_count > 0:
                    broadcast_challenge_cooldowns[initiating_team_id] = clock.time()
                    final_followup_message = f"Challenge broadcast to {broadcast_count} eligible channels."
                else:
                    active_challenges.pop(challenge_id, None)
                    final_followup_message = (
                        "No eligible teams found for broadcast "
                        "(they might be in active challenges or have no suitable channels)."
                    )

            elif self.selected_target_type == "team":
                target_team = await bot.db.teams.get_team(self.selected_target_id)
                if not target_team:
                    await interaction.followup.send("Error: Could not retrieve target team data.", ephemeral=False)
                    return

                if self.game_type == "8s":
                    target_channels = target_team.get("eights_channels", [])
                elif self.game_type == "6s":
                    target_channels = target_team.get("sixes_channels", [])
                elif self.game_type == "5s":
                    target_channels = target_team.get("fives_channels", [])
                else:
                    target_channels = []

                if not target_channels:
                    await interaction.followup.send(
                        f"Error: Target team has no registered {self.game_type.upper()} matchmaking channel.",
                        ephemeral=False,
                    )
                    return

                target_channel_id = target_channels[0]
                target_channel_obj = bot.get_channel(target_channel_id)
                if not target_channel_obj:
                    await interaction.followup.send("Error: Could not find target team matchmaking channel.", ephemeral=False)
                    return

                new_challenge_data["status"] = "pending_direct"
                new_challenge_data["opponent_guild_id"] = self.selected_target_id
                new_challenge_data["opponent_channel_id"] = target_channel_id
                new_challenge_data["opponent_team_name"] = target_team.get("guild_name", "Opponent")
                active_challenges[challenge_id] = new_challenge_data

                challenge_embed = Embed(
                    title=f"{self.game_type.upper()} Challenge Received",
                    description=f"Team **{initiating_team_name}** has challenged your team to a {self.game_type.upper()} match.",
                    color=discord.Color.orange(),
                )
                challenge_embed.set_footer(text=f"Challenge ID: {challenge_id}.")

                await target_channel_obj.send(
                    content=f"Attention Captains/VCs of **{target_team.get('guild_name', 'Unknown Team')}**!",
                    embed=challenge_embed,
                    view=view_for_target,
                )

                if initiating_channel_obj:
                    await initiating_channel_obj.send(
                        f"Challenge sent to **{target_team.get('guild_name', 'Unknown Team')}** in {target_channel_obj.mention}."
                    )

                final_followup_message = f"Challenge issued to **{target_team.get('guild_name', 'Unknown Team')}**."

            elif self.selected_target_type == "main_channel":
                main_channel_id = self.selected_target_id
                if not main_channel_id:
                    await interaction.followup.send(
                        f"Error: No main {self.game_type.upper()} channel selected or configured.",
                        ephemeral=False,
                    )
                    return

                main_channel_obj = bot.get_channel(main_channel_id)
                if not main_channel_obj:
                    await interaction.followup.send(
                        f"Error: Could not find main {self.game_type.upper()} channel.",
                        ephemeral=False,
                    )
                    return

                is_challenged, challenger_name = is_main_channel_challenged(main_channel_id)
                main_channel_state = await init_state(config_module.MAIN_GUILD_ID, main_channel_id)
                if not main_channel_state:
                    await interaction.followup.send(
                        f"Error initializing state for main channel {main_channel_obj.mention}.",
                        ephemeral=False,
                    )
                    return

                state_challenger = main_channel_state.get("is_challenged_by_team_name")
                if state_challenger and not is_challenged:
                    clear_main_channel_challenge_flags(main_channel_state)
                    print(f"Cleared lingering challenge flags for main channel {main_channel_id}")

                main_channel_already_challenged = is_challenged or (state_challenger is not None)
                if main_channel_already_challenged:
                    current_challenger = challenger_name if is_challenged else state_challenger
                    print(
                        f"[CHALLENGE BLOCKED] Team {initiating_team_name} tried to challenge main channel {main_channel_id} "
                        f"but it's already challenged by {current_challenger}"
                    )
                    await interaction.followup.send(
                        f"The Main Guild {self.game_type.upper()} channel ({main_channel_obj.mention}) is already challenged by "
                        f"**{current_challenger}**. Please wait for the current challenge to resolve.",
                        ephemeral=False,
                    )
                    return

                previous_main_teams = _snapshot_team_slots(main_channel_state.get("teams"))
                previous_main_challenger = main_channel_state.get("is_challenged_by_team_name")
                previous_main_game_type = main_channel_state.get("active_challenge_game_type")
                empty_team = _empty_positions_for_game_type(self.game_type)

                if len(main_channel_state["teams"]) > 1:
                    main_channel_state["teams"][1] = dict(empty_team)
                elif len(main_channel_state["teams"]) == 1:
                    main_channel_state["teams"].append(dict(empty_team))
                else:
                    main_channel_state["teams"] = [dict(empty_team), dict(empty_team)]

                main_channel_state["is_challenged_by_team_name"] = initiating_team_name
                main_channel_state["active_challenge_game_type"] = self.game_type

                new_challenge_data["status"] = "accepted"
                new_challenge_data["opponent_guild_id"] = config_module.MAIN_GUILD_ID
                new_challenge_data["opponent_channel_id"] = main_channel_id
                new_challenge_data["opponent_team_name"] = "IOSCA"
                new_challenge_data["target_channel_id_for_main"] = main_channel_id
                active_challenges[challenge_id] = new_challenge_data

                try:
                    if initiating_channel_obj:
                        await sm_refresh_lineup(
                            initiating_channel_obj,
                            author_override=interaction.user,
                            force_new_message=True,
                        )
                    await main_channel_obj.send(
                        f"Your channel has been challenged by **{initiating_team_name}** for a {self.game_type.upper()} match! Prepare your lineup!"
                    )
                    await sm_refresh_lineup(
                        main_channel_obj,
                        author_override=interaction.user,
                        force_new_message=True,
                    )
                except Exception as challenge_error:
                    active_challenges.pop(challenge_id, None)
                    main_channel_state["teams"] = previous_main_teams
                    if previous_main_challenger is None:
                        main_channel_state.pop("is_challenged_by_team_name", None)
                    else:
                        main_channel_state["is_challenged_by_team_name"] = previous_main_challenger
                    if previous_main_game_type is None:
                        main_channel_state.pop("active_challenge_game_type", None)
                    else:
                        main_channel_state["active_challenge_game_type"] = previous_main_game_type

                    if initiating_channel_obj:
                        try:
                            await sm_refresh_lineup(
                                initiating_channel_obj,
                                author_override=interaction.user,
                                force_new_message=True,
                            )
                        except Exception as rollback_error:
                            print(
                                f"[CHALLENGE ROLLBACK ERROR] Failed to restore initiator channel "
                                f"{self.initiating_channel_id}: {rollback_error}"
                            )

                    try:
                        await sm_refresh_lineup(
                            main_channel_obj,
                            author_override=interaction.user,
                            force_new_message=True,
                        )
                    except Exception as rollback_error:
                        print(
                            f"[CHALLENGE ROLLBACK ERROR] Failed to restore main channel "
                            f"{main_channel_id}: {rollback_error}"
                        )

                    print(
                        f"[CHALLENGE ERROR] Failed to finalize main-channel challenge {challenge_id}: {challenge_error}"
                    )
                    await interaction.followup.send(
                        "Error finalizing the challenge. The bot rolled the channel state back; please try again.",
                        ephemeral=False,
                    )
                    return

                final_followup_message = (
                    f"Challenge issued to and auto-accepted by **IOSCA Channel** ({main_channel_obj.mention})."
                )
                print(f"[CHALLENGE SUCCESS] Team {initiating_team_name} successfully challenged main channel {main_channel_id}")

            else:
                final_followup_message = "Error: Unknown target type selected."

        await interaction.followup.send(final_followup_message, ephemeral=False)

        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        # Implement timeout logic if needed
        pass

@bot.slash_command(name="challenge", description="Issue a challenge to another team or the main guild.")
async def challenge_command(ctx: ApplicationContext):
    try:
        await ctx.defer(ephemeral=True)
        # Get the context of the channel the command was used in
        context = await get_channel_context(ctx.guild_id, ctx.channel_id)
        if context.get("type") not in ["team_5s", "team_6s", "team_8s"]:
            await ctx.respond("❌ This command must be used from one of your team's registered 5v5, 6v6, or 8v8 matchmaking channels.", ephemeral=True)
            return
        game_type = "5s" if context.get("type") == "team_5s" else "6s" if context.get("type") == "team_6s" else "8s"
        view = ChallengeView(author_id=ctx.author.id, initiating_team_id=ctx.guild_id, initiating_channel_id=ctx.channel_id, game_type=game_type)
        await ctx.respond(f"Starting a new {game_type.upper()} challenge... Please select the type of target:", view=view, ephemeral=True)
    except Exception as e:
        from ios_bot.error_logger import log_error
        log_error(e, context={
            "guild_id": ctx.guild_id,
            "channel_id": ctx.channel_id,
            "context": str(context) if 'context' in locals() else "Not retrieved"
        }, user_id=ctx.author.id, guild_id=ctx.guild_id, command="challenge")
        await ctx.respond("❌ Error starting challenge. Please try again.", ephemeral=True)
