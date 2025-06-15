from ios_bot.config import *
from ios_bot.database_manager import get_team, update_team_players, get_unique_player_ids, is_player_in_team_type

def paginate_options(members, already_registered_ids, page_size=25):
    # Only eligible members, not bots, not already registered
    eligible = [m for m in members if not m.bot and m.id not in already_registered_ids]
    return [eligible[i:i+page_size] for i in range(0, len(eligible), page_size)]

class PlayerSelect(Select):
    def __init__(self, members, selected_ids, page, total_pages):
        options = [
            SelectOption(label=member.display_name, value=str(member.id), default=(member.id in selected_ids))
            for member in members
        ]
        if not options:
            options.append(SelectOption(label="No eligible members on this page", value="no_eligible"))
        super().__init__(
            placeholder=f"Select players (Page {page+1}/{total_pages})",
            min_values=0,
            max_values=min(len(options), 25),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        # Update global selection set
        values = self.values or []
        current_page_ids = set(int(val) for val in values if val != "no_eligible")
        # Remove any players from this page from the global set, then add back the selected ones
        page_member_ids = set(member.id for member in self.view.eligible_pages[self.view.current_page])
        self.view.selected_player_ids -= page_member_ids
        self.view.selected_player_ids |= current_page_ids
        await self.view.update_message(interaction)

class RegisterPlayersView(View):
    def __init__(self, author_id, guild, team_data):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.guild = guild
        self.team_data = team_data

        # Use get_unique_player_ids to get ALL players currently part of the team (cap, vc, players list)
        self.all_current_player_ids_on_team = get_unique_player_ids(self.team_data)
        
        # Pass this comprehensive set to paginate_options so cap/vc/existing players aren't shown for re-selection
        self.eligible_pages = paginate_options(guild.members, self.all_current_player_ids_on_team)
        self.total_pages = len(self.eligible_pages)
        self.current_page = 0
        self.selected_player_ids = set() # Stores IDs selected from the UI

        self.update_select()

        if self.total_pages > 1:
            self.prev_button = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary)
            self.prev_button.callback = self.prev_page
            self.add_item(self.prev_button)
            self.next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            self.next_button.callback = self.next_page
            self.add_item(self.next_button)

        self.confirm_button = discord.ui.Button(label="Confirm Registration", style=discord.ButtonStyle.success)
        self.confirm_button.callback = self.confirm_callback
        self.add_item(self.confirm_button)

    def update_select(self):
        # Remove old select if present
        for item in self.children[:]:
            if isinstance(item, PlayerSelect):
                self.remove_item(item)
        # Add new select for current page
        members = self.eligible_pages[self.current_page] if self.eligible_pages else []
        selected_ids = self.selected_player_ids
        self.player_select = PlayerSelect(members, selected_ids, self.current_page, self.total_pages)
        self.add_item(self.player_select)

    async def update_message(self, interaction):
        # Build embed with selected players
        embed = discord.Embed(title="Register Players", color=discord.Color.blue())
        if self.selected_player_ids:
            selected_members = [self.guild.get_member(pid) for pid in self.selected_player_ids]
            selected_names = [m.display_name for m in selected_members if m]
            embed.add_field(name="Selected Players", value="\n".join(selected_names), inline=False)
        else:
            embed.add_field(name="Selected Players", value="None", inline=False)
        self.update_select()
        await interaction.response.edit_message(embed=embed, view=self)

    async def prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def next_page(self, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def confirm_callback(self, interaction: discord.Interaction):
        # The self.selected_player_ids set is already accurately maintained
        # by the PlayerSelect.callback whenever a selection is made or changed.
        ui_selected_ids = self.selected_player_ids # IDs selected in the current UI interaction

        if not ui_selected_ids:
            await interaction.response.send_message("No new players were selected for registration.", ephemeral=True)
            self.stop()
            return
        
        # --- NEW: Player Uniqueness Validation ---
        is_national_team = self.team_data.get("is_national_team", False)
        team_type_str = "National Team" if is_national_team else "Club Team"

        for player_id in ui_selected_ids:
            conflicting_team = await is_player_in_team_type(player_id, is_national_team)
            if conflicting_team:
                member = self.guild.get_member(player_id)
                await interaction.response.edit_message(
                    content=f"❌ Error: {member.mention} is already on another {team_type_str} ({conflicting_team}). "
                            "A player can only be on one Club Team and one National Team at a time.",
                    view=None
                )
                self.stop()
                return
            
        # --- Explicit Validation ---
        # Although the selection UI should prevent this, we add a server-side check for robustness.
        captain_id = self.team_data.get('captain_id')
        vice_captain_id = self.team_data.get('vice_captain_id')
        
        for player_id in ui_selected_ids:
            if player_id == captain_id:
                member = self.guild.get_member(player_id)
                await interaction.response.edit_message(content=f"❌ Error: The captain ({member.mention}) cannot be registered as a player.", view=None)
                self.stop()
                return
            if player_id == vice_captain_id:
                member = self.guild.get_member(player_id)
                await interaction.response.edit_message(content=f"❌ Error: The vice-captain ({member.mention}) cannot be registered as a player.", view=None)
                self.stop()
                return

        # Get the current state of unique players on the team
        current_unique_player_ids_on_team = get_unique_player_ids(self.team_data)
        current_player_count_on_team = len(current_unique_player_ids_on_team)
        
        # Determine which of the UI-selected players are genuinely new and eligible
        potential_new_additions_ids = set()
        for player_id in ui_selected_ids:
            # Check if the player is not already on the team in any capacity (cap, vc, or 'players' list)
            if player_id not in current_unique_player_ids_on_team:
                member = self.guild.get_member(player_id)
                if member and not member.bot: # Ensure member exists and is not a bot
                    potential_new_additions_ids.add(player_id)

        num_potential_new_players = len(potential_new_additions_ids)

        if not potential_new_additions_ids:
            # This case means players were selected, but they were all either already on the team or bots.
            # Provide more specific feedback based on ui_selected_ids and current_unique_player_ids_on_team
            already_on_team_selected_names = []
            for pid in ui_selected_ids:
                if pid in current_unique_player_ids_on_team:
                    member = self.guild.get_member(pid)
                    if member:
                         already_on_team_selected_names.append(member.display_name)
            
            if already_on_team_selected_names:
                msg = f"Selected players ({', '.join(already_on_team_selected_names)}) are already part of the team or ineligible. No new players to add."
            else: # Selected were likely bots not caught by initial paginate_options (if it allows selecting bots then filtering here)
                msg = "No new eligible players were selected for registration. They may already be on the team or are bots."

            await interaction.response.edit_message(content=msg, view=None)
            self.stop()
            return

        # Check 16-player limit
        if current_player_count_on_team + num_potential_new_players > 9:
            slots_available = 9 - current_player_count_on_team
            if slots_available <= 0:
                await interaction.response.edit_message(content="The team roster is already full (9 unique players). No new players can be added.", view=None)
            else:
                await interaction.response.edit_message(content=f"You are trying to add {num_potential_new_players} new player(s), but the team only has {slots_available} slot(s) available to reach the 16 unique player limit. Please revise your selection.", view=None)
            self.stop()
            return

        # Proceed with adding players to the 'players' list in the database
        # current_db_players_list is the list of dicts from team_data['players']
        current_db_players_list = list(self.team_data.get('players', []))
        
        newly_added_to_db_list_details = [] # Store dicts {"id": id, "name": name} for DB update
        
        for player_id in potential_new_additions_ids: # Iterate only over genuinely new players
            member = self.guild.get_member(player_id)
            if member: # Should exist and not be a bot from earlier checks
                newly_added_to_db_list_details.append({"id": member.id, "name": member.display_name})

        # Construct the final list for the 'players' field in the database
        # This explicitly adds new, unique individuals to the existing 'players' list.
        # Duplicates within current_db_players_list would remain if they exist, but get_unique_player_ids handles overall count.
        final_player_list_for_db = current_db_players_list + newly_added_to_db_list_details
        
        # Feedback on players who were selected in UI but not added (e.g. already on team)
        already_present_selected_names = []
        for pid in ui_selected_ids:
            if pid in current_unique_player_ids_on_team and pid not in potential_new_additions_ids: # Was selected, on team, but not part of this "new batch"
                member = self.guild.get_member(pid)
                if member:
                    already_present_selected_names.append(member.display_name)
        
        if await update_team_players(self.guild.id, final_player_list_for_db):
            newly_added_names = [p['name'] for p in newly_added_to_db_list_details]
            response_msg = f"**Successfully registered:** {', '.join(newly_added_names)} to the team's player list.\\n"
            if already_present_selected_names: # Feedback for players selected in UI but already on team (cap, vc, or players list)
                response_msg += f"**Selected but already on team (not added again):** {', '.join(already_present_selected_names)}."
            elif len(ui_selected_ids) > len(newly_added_names): # General case if some selected were not added for other reasons (e.g. bots if not pre-filtered)
                 response_msg += " Some selected members were not added as they are already on the team or ineligible."

            total_unique_players_after_add = len(get_unique_player_ids(await get_team(self.guild.id))) # Re-fetch for current count
            response_msg += f"\\nThe team now has {total_unique_players_after_add} unique players."

            await interaction.response.edit_message(content=response_msg, view=None)
        else:
            await interaction.response.edit_message(content="❌ Failed to update team players in the database.", view=None)
        self.stop()

@bot.slash_command(
    name="register_players",
    description="Register new players to your IOSCA team roster."
)
async def register_players(ctx: ApplicationContext):
    guild = ctx.guild
    if not guild:
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
        return

    # get_team is now async
    team_data = await get_team(guild.id) 
    if not team_data: # team_data would be None if get_team returns None
        await ctx.respond("This server is not registered as an IOSCA team. Use `/register_team` first.", ephemeral=True)
        return

    # Permission Check: Captain or Vice-Captain
    # Ensure team_data is a dict before calling .get()
    if not isinstance(team_data, dict):
        await ctx.respond("Error: Could not retrieve valid team data. Please try again.", ephemeral=True)
        return
        
    captain_id = team_data.get('captain_id')
    vice_captain_id = team_data.get('vice_captain_id')
    
    if not (ctx.user.id == captain_id or ctx.user.id == vice_captain_id):
        await ctx.respond("You must be the team Captain or Vice-Captain to register players.", ephemeral=True)
        return

    # Check 1: Team already full before starting registration, based on unique players
    current_unique_ids = get_unique_player_ids(team_data)
    if len(current_unique_ids) >= 9:
        await ctx.respond(f"Your team roster is full ({len(current_unique_ids)} unique players). No more players can be added.", ephemeral=True)
        return

    view = RegisterPlayersView(ctx.author.id, guild, team_data)
    embed = discord.Embed(title="Register Players", color=discord.Color.blue())
    embed.add_field(name="Selected Players", value="None", inline=False)
    await ctx.respond("Select players to add to the team:", embed=embed, view=view, ephemeral=True)

@bot.slash_command(
    name="remove_player",
    description="Remove a player from your IOSCA team roster."
)
async def remove_player(ctx: ApplicationContext, player: Option(Member, "Select the player to remove")):
    guild = ctx.guild
    if not guild:
        await ctx.respond("This command can only be used in a server.", ephemeral=True)
        return

    team_data = await get_team(guild.id) # Await this
    if not team_data:
        await ctx.respond("This server is not registered as an IOSCA team. Use `/register_team` first.", ephemeral=True)
        return
    
    if not isinstance(team_data, dict):
        await ctx.respond("Error: Could not retrieve valid team data for removing player.", ephemeral=True)
        return

    # Permission Check: Captain or Vice-Captain
    captain_id = team_data.get('captain_id')
    vice_captain_id = team_data.get('vice_captain_id')
    if not (ctx.user.id == captain_id or ctx.user.id == vice_captain_id):
        await ctx.respond("You must be the team Captain or Vice-Captain to remove players.", ephemeral=True)
        return
    
    if player.id == captain_id:
        await ctx.respond("The Captain cannot be removed from the team using this command.", ephemeral=True)
        return
    if player.id == vice_captain_id:
        await ctx.respond("The Vice-Captain cannot be removed using this command.", ephemeral=True)
        return 

    current_players = list(team_data.get('players', []))
    player_to_remove_found = False
    updated_players = []
    for p_data in current_players: # Renamed p to p_data to avoid conflict if p is used elsewhere
        if isinstance(p_data, dict) and p_data.get('id') == player.id:
            player_to_remove_found = True
        else:
            updated_players.append(p_data)
    
    if not player_to_remove_found:
        await ctx.respond(f"{player.display_name} is not registered on this team.", ephemeral=True)
        return

    if await update_team_players(guild.id, updated_players): # Await this
        await ctx.respond(f"Successfully removed {player.display_name} from the team.", ephemeral=True)
    else:
        await ctx.respond("❌ Failed to update team players in the database when removing player.", ephemeral=True) 