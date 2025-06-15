from ios_bot.config import *
import time as clock
from ios_bot.database_manager import get_team, get_all_teams_with_channels
from ios_bot.signup_manager import get_channel_state, init_state, format_lineup, refresh_lineup as sm_refresh_lineup, get_channel_context # MODIFIED
from ios_bot.challenge_manager import active_challenges, broadcast_challenge_cooldowns
from datetime import datetime

# --- Helper function to check if a team's lineup is full --- #
def is_lineup_full(state: dict, context_type: str) -> bool:
    if not state or not state.get("teams"):
        return False
    
    team_lineup = state["teams"][0]
    
    if context_type == "team_8s":
        positions_to_check = EIGHTS_POSITIONS
    elif context_type == "team_6s":
        positions_to_check = SIXES_POSITIONS
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

class ChallengeAcceptView(View):
    """View for teams receiving a challenge, allowing them to Accept or Ignore."""
    def __init__(self, challenge_id: str, challenged_team_id: int, game_type: str):
        super().__init__(timeout=3600) # Challenge stands for 1 hour for acceptance
        self.challenge_id = challenge_id
        self.challenged_team_id = challenged_team_id # The ID of the team receiving this view
        self.game_type = game_type

        self.accept_button = Button(label="Accept Challenge", style=discord.ButtonStyle.success, custom_id=f"accept_challenge_{challenge_id}")
        self.accept_button.callback = self.accept_callback
        self.add_item(self.accept_button)

        self.ignore_button = Button(label="Ignore Challenge", style=discord.ButtonStyle.secondary, custom_id=f"ignore_challenge_{challenge_id}")
        self.ignore_button.callback = self.ignore_callback
        self.add_item(self.ignore_button)
    
    async def accept_callback(self, interaction: discord.Interaction):
        # Defer ephemerally at the start to handle early exits gracefully
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded: 
            return 
        except discord.NotFound: 
            return 

        challenge_data = active_challenges.get(self.challenge_id)
        if not challenge_data or challenge_data["status"] not in ["pending_broadcast", "pending_direct"]:
            try:
                await interaction.message.edit(content="This challenge is no longer active or has already been accepted.", view=None)
            except discord.HTTPException:
                pass 
            try:
                 if not interaction.is_done(): await interaction.followup.send("Challenge is no longer active.", ephemeral=True)
            except: pass 
            return

        if interaction.guild_id != self.challenged_team_id:
             await interaction.followup.send("This acceptance is not valid for your team.", ephemeral=True); return

        accepting_channel_context = await get_channel_context(interaction.guild_id, interaction.channel_id)
        if not (accepting_channel_context["type"] == f"team_{self.game_type}"):
            await interaction.followup.send(f"Please accept from one of your team's registered {self.game_type.upper()} matchmaking channel.", ephemeral=True); return
        
        accepting_lineup_state = get_channel_state(interaction.channel_id)
        if not accepting_lineup_state: accepting_lineup_state = await init_state(interaction.guild_id, interaction.channel_id)

        # Check if accepting team lineup is full
        if not is_lineup_full(accepting_lineup_state, accepting_channel_context["type"]):
            await interaction.followup.send(f"Your team's {self.game_type.upper()} lineup in this channel must be full (excluding GK) to accept this challenge.", ephemeral=True)
            return

        # Make the interaction response public and update content for the main processing path
        try:
            # We edit the message from which the button was clicked, not the original interaction response
            await interaction.message.edit(content="Challenge accepted! Processing...", view=None)
        except discord.HTTPException as e:
            # print(f"Failed to edit original challenge message on accept: {e}") # Optional logging
            pass # Continue even if edit fails

        challenge_data["status"] = "accepted"
        challenge_data["accepted_timestamp"] = datetime.now() # Record acceptance time
        challenge_data["opponent_guild_id"] = interaction.guild_id
        challenge_data["opponent_channel_id"] = interaction.channel_id
        
        accepting_team_details = await get_team(interaction.guild_id)
        if not accepting_team_details: # DB error check
            await interaction.followup.send("Error: Could not retrieve your team's details to accept the challenge. Please try again.", ephemeral=False)
            # Revert status if possible, or clear interaction
            challenge_data["status"] = "pending_direct" # Or original status before acceptance attempt
            if "accepted_timestamp" in challenge_data: del challenge_data["accepted_timestamp"]
            # Might need to remove opponent details too
            return
        challenge_data["opponent_team_name"] = accepting_team_details["guild_name"]

        initiating_team_name = challenge_data["initiating_team_name"]
        accepting_team_name = challenge_data["opponent_team_name"]
        game_type_display = challenge_data["game_type"].upper()

        # Notify initiating team (publicly)
        initiating_channel = None
        try:
            initiating_guild = bot.get_guild(challenge_data["initiating_guild_id"])
            initiating_channel = initiating_guild.get_channel(challenge_data["initiating_channel_id"])
            if initiating_channel:
                 await initiating_channel.send(f"🎉 Your {game_type_display} challenge has been **ACCEPTED** by **{accepting_team_name}**! Match on!")
            # Refresh initiating team's lineup embed (will show VS.)
            await sm_refresh_lineup(initiating_channel, author_override=interaction.user, force_new_message=True)
        except Exception as e:
            print(f"Error notifying/refreshing initiating team on accept: {e}")

        # Notify accepting team's channel (publicly)
        await interaction.followup.send(f"✅ Challenge **ACCEPTED**! You are now playing against **{initiating_team_name}** in a {game_type_display} match. Good luck!")
        # Refresh accepting team's lineup embed
        await sm_refresh_lineup(interaction.channel, author_override=interaction.user, force_new_message=True)
        
        # If it was a broadcast, edit other broadcast messages to show accepted
        if challenge_data.get("target_type") == "broadcast":
            for ch_id, msg_id in challenge_data.get("broadcast_messages", {}).items():
                if ch_id != interaction.channel_id: # Don't edit the one just accepted from
                    try:
                        broadcast_channel = bot.get_channel(ch_id)
                        if broadcast_channel:
                            msg_to_edit = await broadcast_channel.fetch_message(msg_id)
                            await msg_to_edit.edit(content=f"ℹ️ The {game_type_display} challenge from **{initiating_team_name}** was accepted by **{accepting_team_name}**.", embed=None, view=None)
                    except Exception as e:
                        print(f"Error editing broadcast message {msg_id} in channel {ch_id}: {e}")
        challenge_data["broadcast_messages"] = {} # Clear them after processing

        # Delete the original challenge message with buttons
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass # Message might have been deleted by other means

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
                # Since we deferred, we edit the original response or the message view is attached to
                await interaction.edit_original_response(content="This challenge is no longer active or has already been addressed.", view=None)
                await interaction.message.delete(delay=5) 
            except discord.HTTPException: pass 
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
                    await sm_refresh_lineup(initiating_channel, force_new_message=True) 
            except Exception as e:
                print(f"Error notifying initiator of declined challenge {self.challenge_id}: {e}")
            
            await interaction.edit_original_response(content=f"Challenge from **{initiating_team_name}** has been **DECLINED** by your team.", view=None) # view=None since message with buttons is deleted
            
            if self.challenge_id in active_challenges:
                del active_challenges[self.challenge_id]
        
        elif challenge_data["target_type"] == "broadcast":
            await interaction.edit_original_response(content=f"Your team has chosen to **IGNORE** the {game_type_display} broadcast challenge from **{initiating_team_name}**. Other teams may still accept.", view=None)
        else:
            await interaction.edit_original_response(content="Challenge ignored.", view=None) # Fallback
        
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

        # Only show options for the current game type
        options = [
            SelectOption(label=f"Broadcast to all {self.game_type.upper()} Teams", value="broadcast_all", description=f"Challenge any available registered {self.game_type.upper()} team."),
            SelectOption(label=f"Challenge Main {self.game_type.upper()} Channel", value=f"main_channel_{self.game_type}", description=f"Challenge the main guild's {self.game_type.upper()} matchmaking channel.")
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

        if chosen_type_value == "broadcast_all":
            self.selected_target_type = "broadcast"
            self.selected_target_id = None # No specific ID for broadcast
            self.selected_target_name = f"All {self.game_type.upper()} Teams (Broadcast)"
            self.target_type_select.disabled = True
            self.add_confirm_button(f"Confirm Broadcast {self.game_type.upper()} Challenge?")
            await interaction.edit_original_response(content=f"You've selected to broadcast the {self.game_type.upper()} challenge.", view=self)
        elif chosen_type_value.startswith("main_channel_"):
            self.selected_target_type = "main_channel"
            main_channels_ids = SIXES_MAIN_MATCHMAKING_CHANNELS if self.game_type == "6s" else EIGHTS_MAIN_MATCHMAKING_CHANNELS
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
                options = []
                for ch_id in main_channels_ids:
                    channel_obj = bot.get_channel(ch_id)
                    options.append(SelectOption(label=channel_obj.name if channel_obj else f"Channel ID {ch_id}", value=str(ch_id)))
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

    async def on_specific_main_channel_selected(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.selected_target_id = int(interaction.data["values"][0])
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
        self.confirm_challenge_button.callback = self.confirm_issue_challenge
        self.add_item(self.confirm_challenge_button)
    
    async def confirm_issue_challenge(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not self.selected_target_type:
            await interaction.followup.send("Please select a valid challenge target first.", ephemeral=True)
            return

        initiating_team_id = self.initiating_team_id
        
        # --- COOLDOWN LOGIC ---
        if self.selected_target_type == "broadcast":
            cooldown_period = 600 # 10 minutes
            if initiating_team_id in broadcast_challenge_cooldowns:
                last_broadcast_time = broadcast_challenge_cooldowns[initiating_team_id]
                time_since_last_broadcast = clock.time() - last_broadcast_time

                if time_since_last_broadcast < cooldown_period:
                    remaining_time = cooldown_period - time_since_last_broadcast
                    await interaction.followup.send(
                        f"You must wait {int(remaining_time // 60)} minutes and {int(remaining_time % 60)} seconds "
                        "before broadcasting another challenge.",
                        ephemeral=True
                    )
                    return
            
            # Update cooldown timestamp after successful broadcast
            broadcast_challenge_cooldowns[initiating_team_id] = clock.time()

        initiating_team_details = await get_team(self.initiating_team_id)
        if not initiating_team_details:
            await interaction.followup.send("Error: Could not retrieve your team data.", ephemeral=True)
            return
        
        initiating_team_name = initiating_team_details.get("guild_name", "Your Team")

        # Check for existing outgoing challenges from this team for this game type
        for ch_id, ch_data in active_challenges.items():
            if ch_data.get("initiating_team_id") == self.initiating_team_id and ch_data.get("game_type") == self.game_type and ch_data["status"] in ["pending_broadcast", "pending_direct"]:
                await interaction.followup.send(f"❌ Your team already has an outgoing {self.game_type.upper()} challenge. Please `/unchallenge` first or wait for it to resolve.", ephemeral=False)
                return

        # Initial lineup check for the initiating team (commented out for testing)
        initiating_channel_obj = bot.get_channel(self.initiating_channel_id)
        initiating_lineup_state = get_channel_state(self.initiating_channel_id)
        if not initiating_lineup_state: initiating_lineup_state = await init_state(self.initiating_team_id, self.initiating_channel_id)
        
        initiating_channel_context = await get_channel_context(self.initiating_team_id, self.initiating_channel_id)
        # if not is_lineup_full(initiating_lineup_state, initiating_channel_context.get("type")): # REMOVED FOR TESTING
        #    await interaction.followup.send(f"❌ Your team's {self.game_type.upper()} lineup in {initiating_channel_obj.mention} must be full to issue a challenge.", ephemeral=False) # REMOVED FOR TESTING
        #    return # REMOVED FOR TESTING


        challenge_id = f"challenge_{self.initiating_team_id}_{int(datetime.now().timestamp())}"
        new_challenge_data = {
            "challenge_id": challenge_id,
            "initiating_guild_id": self.initiating_team_id,
            "initiating_channel_id": self.initiating_channel_id,
            "initiating_team_name": initiating_team_name,
            "game_type": self.game_type, # "8s"
            "target_type": self.selected_target_type, # "team", "main_channel", or "broadcast"
            "target_id": self.selected_target_id,     # Guild ID for team/main, None for broadcast
            "target_name": self.selected_target_name, # Name of target team/main_channel, "All Teams" for broadcast
            "status": "pending_direct", # Default, will change for broadcast or main
            "timestamp": datetime.now(),
            "broadcast_messages": {}, # Stores {channel_id: message_id} for broadcast cleanup
            "opponent_guild_id": None,
            "opponent_channel_id": None,
            "opponent_team_name": None
        }

        final_followup_message = ""
        view_for_target = ChallengeAcceptView(challenge_id, self.selected_target_id if self.selected_target_type == "team" else None, self.game_type)
        # The challenged_team_id for ChallengeAcceptView is tricky for main_channel and broadcast
        # For main_channel, it will be MAIN_GUILD_ID
        # For broadcast, each receiving team uses their own guild_id for the check in accept_callback

        if self.selected_target_type == "broadcast":
            new_challenge_data["status"] = "pending_broadcast"
            all_teams = await get_all_teams_with_channels()
            broadcast_count = 0
            challenge_embed = Embed(
                title=f"Open {self.game_type.upper()} Challenge!",
                description=f"Team **{initiating_team_name}** is issuing an open challenge for a {self.game_type.upper()} match!",
                color=discord.Color.blue()
            )
            challenge_embed.set_footer(text=f"Challenge ID: {challenge_id}. Your team can accept.")

            for team_data in all_teams:
                if team_data["guild_id"] == self.initiating_team_id: continue # Don't broadcast to self

                # Check if team is already in an accepted challenge
                if any(c.get("status") == "accepted" and (c.get("initiating_team_id") == team_data["guild_id"] or c.get("opponent_guild_id") == team_data["guild_id"]) for c_id, c in active_challenges.items()):
                    continue # Skip teams in active matches

                if self.game_type == "8s":
                    team_channels = team_data.get("eights_channels", [])
                elif self.game_type == "6s":
                    team_channels = team_data.get("sixes_channels", [])
                else:
                    team_channels = []
                
                # Send to ALL registered channels for the game type
                if team_channels:
                    for target_ch_id in team_channels:
                        target_channel_obj = bot.get_channel(target_ch_id)
                        if target_channel_obj:
                            try:
                                # Pass the specific team's ID to ChallengeAcceptView for its internal check
                                sent_msg = await target_channel_obj.send(
                                    content=f"Attention Captains/VCs of **{team_data['guild_name']}**!",
                                    embed=challenge_embed, 
                                    view=ChallengeAcceptView(challenge_id, team_data["guild_id"], self.game_type)
                                )
                                # Store one message ID per channel for potential cleanup, though broadcast_messages might need rethinking for multi-channel send
                                # For now, let's store the last one, or a list if unchallenge needs to handle multiple.
                                # Simplest for now: still store one, assuming unchallenge would clear based on challenge_id rather than specific msg_id for broadcasts.
                                new_challenge_data["broadcast_messages"][target_ch_id] = sent_msg.id 
                                broadcast_count += 1
                            except Exception as e:
                                print(f"Error broadcasting challenge to {team_data['guild_name']} in channel {target_ch_id}: {e}")
                        else:
                            print(f"[CHALLENGE BROADCAST] Could not find channel object for ID: {target_ch_id}")
            
            if broadcast_count > 0:
                final_followup_message = f"✅ Challenge broadcast to {broadcast_count} eligible teams!"
                active_challenges[challenge_id] = new_challenge_data
            else:
                final_followup_message = "ℹ️ No eligible teams found for broadcast (they might be in active challenges or have no suitable channels)."
                # No need to add to active_challenges if not sent anywhere
        
        elif self.selected_target_type == "main_channel":
            main_channel_id = self.selected_target_id
            if not main_channel_id:
                await interaction.followup.send(f"Error: No main {self.game_type.upper()} channel selected or configured.", ephemeral=False)
                return

            main_channel_obj = bot.get_channel(main_channel_id)
            if not main_channel_obj:
                await interaction.followup.send(f"Error: Could not find main {self.game_type.upper()} channel.", ephemeral=False)
                return

            # Check if main channel is already part of an accepted challenge or is the target of a pending direct one
            main_guild_is_opponent = any(
                c.get("opponent_guild_id") == MAIN_GUILD_ID and c.get("opponent_channel_id") == main_channel_id and c.get("status") == "accepted"
                for c_id, c in active_challenges.items()
            )
            main_guild_is_target_of_pending = any(
                c.get("target_id") == MAIN_GUILD_ID and c.get("target_channel_id_for_main", 0) == main_channel_id and c.get("status") == "pending_direct"
                for c_id, c in active_challenges.items()
            )

            if main_guild_is_opponent or main_guild_is_target_of_pending:
                await interaction.followup.send(f"❌ The Main Guild {self.game_type.upper()} channel ({main_channel_obj.mention}) is currently involved in another challenge. Try again later.", ephemeral=False)
                return

            # For main channel challenges, it's an auto-accept model.
            # Update main channel state directly.
            main_channel_state = await init_state(MAIN_GUILD_ID, main_channel_id)
            if not main_channel_state:
                await interaction.followup.send(f"Error initializing state for main channel {main_channel_obj.mention}.", ephemeral=False)
                return
            
            # Clear main channel's second team and subs (if any) as they are now "Team Main Guild" vs challenger
            if len(main_channel_state["teams"]) > 1:
                main_channel_state["teams"][1] = {p: None for p in (SIXES_POSITIONS if self.game_type == "6s" else EIGHTS_POSITIONS)}
            main_channel_state["subs"].clear()
            
            # Set flags in main channel state for refresh_lineup to use
            main_channel_state["is_challenged_by_team_name"] = initiating_team_name
            main_channel_state["active_challenge_game_type"] = self.game_type
            
            # Update challenge data
            new_challenge_data["status"] = "accepted" # Auto-accepted by main guild
            new_challenge_data["opponent_guild_id"] = MAIN_GUILD_ID
            new_challenge_data["opponent_channel_id"] = main_channel_id
            new_challenge_data["opponent_team_name"] = f"Main Guild {self.game_type.upper()} Team" # Placeholder name
            # Store the specific main channel ID that was targeted, as target_id is MAIN_GUILD_ID
            new_challenge_data["target_channel_id_for_main"] = main_channel_id
            active_challenges[challenge_id] = new_challenge_data
            
            # Refresh initiator's lineup (now VS Main Guild)
            await sm_refresh_lineup(initiating_channel_obj, author_override=interaction.user, force_new_message=True)
            # Refresh main channel's lineup (now VS Initiator)
            await sm_refresh_lineup(main_channel_obj, author_override=interaction.user, force_new_message=True)

            final_followup_message = f"✅ Challenge issued to and auto-accepted by **Main Guild {self.game_type.upper()} Channel** ({main_channel_obj.mention})! Your embeds are updated."
            await main_channel_obj.send(f"⚔️ Your channel has been challenged by **{initiating_team_name}** for a {self.game_type.upper()} match! Prepare your lineup!")

        else: # Should not be reached if selections are handled
            final_followup_message = "Error: Unknown target type selected."

        await interaction.followup.send(final_followup_message, ephemeral=False) # Public confirmation
        
        # Remove the original interaction message that had the ChallengeView
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass # message might be gone

    async def on_timeout(self):
        # Implement timeout logic if needed
        pass

@bot.slash_command(name="challenge", description="Issue a challenge to another team or the main guild.")
async def challenge_command(ctx: ApplicationContext):
    await ctx.defer(ephemeral=True)
    # Get the context of the channel the command was used in
    context = await get_channel_context(ctx.guild_id, ctx.channel_id)
    if context.get("type") not in ["team_6s", "team_8s"]:
        await ctx.respond("❌ This command must be used from one of your team's registered 6v6 or 8v8 matchmaking channels.", ephemeral=True)
        return
    game_type = "6s" if context.get("type") == "team_6s" else "8s"
    view = ChallengeView(author_id=ctx.author.id, initiating_team_id=ctx.guild_id, initiating_channel_id=ctx.channel_id, game_type=game_type)
    await ctx.respond(f"Starting a new {game_type.upper()} challenge... Please select the type of target:", view=view, ephemeral=True)