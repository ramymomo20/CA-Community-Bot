from ios_bot.config import *
from ios_bot.database_manager import (
    create_tournament, get_all_tournaments, get_tournament_by_id, get_tournament_leagues,
    get_tournament_teams, add_team_to_tournament, remove_team_from_tournament,
    update_tournament_details, delete_tournament, get_all_teams_with_details,
    get_teams_per_league_limit, add_match_to_tournament, get_tournament_matches,
    get_tournament_league_table, complete_tournament
)
from ios_bot.commands.view_match import get_matches

# === MODALS ===

class TournamentRegistrationModal(Modal):
    def __init__(self):
        super().__init__(title="Register New Tournament")
        
        self.add_item(InputText(
            label="Tournament Name",
            placeholder="Enter tournament name...",
            max_length=100,
            required=True
        ))
        
        self.add_item(InputText(
            label="Number of Teams",
            placeholder="Enter total number of teams...",
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Number of Leagues",
            placeholder="Enter number of leagues...",
            max_length=2,
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        name = self.children[0].value
        try:
            num_teams = int(self.children[1].value)
            num_leagues = int(self.children[2].value)
        except ValueError:
            await interaction.response.send_message("Number of teams and leagues must be valid integers.", ephemeral=True)
            return
        
        if num_teams < 2:
            await interaction.response.send_message("Tournament must have at least 2 teams.", ephemeral=True)
            return
            
        if num_leagues < 1:
            await interaction.response.send_message("Tournament must have at least 1 league.", ephemeral=True)
            return
            
        if num_teams % num_leagues != 0:
            await interaction.response.send_message(f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues}).", ephemeral=True)
            return
        
        try:
            tournament_id = await create_tournament(name, num_teams, num_leagues)
            if tournament_id:
                embed = discord.Embed(
                    title="Tournament Registered",
                    description=f"**{name}** has been successfully registered!",
                    color=discord.Color.green()
                )
                embed.add_field(name="Teams", value=str(num_teams), inline=True)
                embed.add_field(name="Leagues", value=str(num_leagues), inline=True)
                embed.add_field(name="Teams per League", value=str(num_teams // num_leagues), inline=True)
                
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message("Failed to create tournament. Please try again.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error creating tournament: {str(e)}", ephemeral=True)

class TournamentEditModal(Modal):
    def __init__(self, tournament):
        super().__init__(title=f"Edit {tournament['name']}")
        self.tournament = tournament
        
        self.add_item(InputText(
            label="Tournament Name",
            placeholder="Enter tournament name...",
            value=tournament['name'],
            max_length=100,
            required=True
        ))
        
        self.add_item(InputText(
            label="Number of Teams",
            placeholder="Enter total number of teams...",
            value=str(tournament['num_teams']),
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Number of Leagues",
            placeholder="Enter number of leagues...",
            value=str(tournament['num_leagues']),
            max_length=2,
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        name = self.children[0].value
        try:
            num_teams = int(self.children[1].value)
            num_leagues = int(self.children[2].value)
        except ValueError:
            await interaction.response.send_message("Number of teams and leagues must be valid integers.", ephemeral=True)
            return
        
        if num_teams < 2:
            await interaction.response.send_message("Tournament must have at least 2 teams.", ephemeral=True)
            return
            
        if num_leagues < 1:
            await interaction.response.send_message("Tournament must have at least 1 league.", ephemeral=True)
            return
            
        if num_teams % num_leagues != 0:
            await interaction.response.send_message(f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues}).", ephemeral=True)
            return
        
        try:
            success = await update_tournament_details(self.tournament['id'], name, num_teams, num_leagues)
            if success:
                embed = discord.Embed(
                    title="Tournament Updated",
                    description=f"**{name}** has been successfully updated!",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Teams", value=str(num_teams), inline=True)
                embed.add_field(name="Leagues", value=str(num_leagues), inline=True)
                embed.add_field(name="Teams per League", value=str(num_teams // num_leagues), inline=True)
                
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message("Failed to update tournament. Please try again.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error updating tournament: {str(e)}", ephemeral=True)

# === SELECT MENUS ===

class TournamentSelect(Select):
    def __init__(self, tournaments, action="view"):
        self.action = action
        options = []
        
        for tournament in tournaments[:25]:  # Discord limit
            status = "🏆" if tournament['is_completed'] else "🔄"
            description = f"{status} {tournament['num_teams']} teams, {tournament['num_leagues']} leagues"
            if tournament['champion']:
                description += f" | Champion: {tournament['champion']}"
                
            options.append(discord.SelectOption(
                label=tournament['name'][:100],
                description=description[:100],
                value=str(tournament['id'])
            ))
        
        if not options:
            options.append(discord.SelectOption(
                label="No tournaments available",
                description="Create a tournament first",
                value="none"
            ))
        
        placeholder = {
            "view": "Select a tournament to view...",
            "delete": "Select a tournament to delete..."
        }.get(action, "Select a tournament...")
        
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("No tournaments available.", ephemeral=True)
            return
            
        tournament_id = int(self.values[0])
        
        if self.action == "view":
            await self._handle_view_tournament(interaction, tournament_id)
        elif self.action == "delete":
            await self._handle_delete_tournament(interaction, tournament_id)

    async def _handle_view_tournament(self, interaction, tournament_id):
        """Handle viewing a tournament."""
        tournament = await get_tournament_by_id(tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        
        # Create tournament view embed
        embed = discord.Embed(
            title=tournament['name'],
            color=discord.Color.gold() if tournament['is_completed'] else discord.Color.blue()
        )
        
        # Set guild info as author
        guild = interaction.guild
        if guild:
            embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        
        # Add basic info
        embed.add_field(name="Teams", value=str(tournament['num_teams']), inline=True)
        embed.add_field(name="Leagues", value=str(tournament['num_leagues']), inline=True)
        embed.add_field(name="Teams per League", value=str(tournament['num_teams'] // tournament['num_leagues']), inline=True)
        
        # Add dates
        start_date = tournament['start_date'].strftime('%Y-%m-%d') if tournament['start_date'] else "Unknown"
        end_date = tournament['end_date'].strftime('%Y-%m-%d') if tournament['end_date'] else "Ongoing"
        embed.add_field(name="Start Date", value=start_date, inline=True)
        embed.add_field(name="End Date", value=end_date, inline=True)
        embed.add_field(name="Status", value="Completed" if tournament['is_completed'] else "Active", inline=True)
        
        # Add teams info
        teams = await get_tournament_teams(tournament_id)
        teams_by_league = {}
        for team in teams:
            league_name = team['league_name']
            if league_name not in teams_by_league:
                teams_by_league[league_name] = []
            teams_by_league[league_name].append(team['guild_name'])
        
        if teams_by_league:
            teams_text = ""
            for league_name, team_names in teams_by_league.items():
                teams_text += f"**{league_name}:** {', '.join(team_names)}\n"
            embed.add_field(name="Teams", value=teams_text[:1024], inline=False)
        else:
            embed.add_field(name="Teams", value="No teams registered yet", inline=False)
        
        # Add awards
        awards_text = ""
        if tournament['champion']:
            awards_text += f"🏆 **Champion:** {tournament['champion']}\n"
        if tournament['runner_up']:
            awards_text += f"🥈 **Runner-up:** {tournament['runner_up']}\n"
        if tournament['third_place']:
            awards_text += f"🥉 **Third Place:** {tournament['third_place']}\n"
        if tournament['top_scorer']:
            awards_text += f"⚽ **Top Scorer:** {tournament['top_scorer']}\n"
        if tournament['top_assister']:
            awards_text += f"👟 **Top Assister:** {tournament['top_assister']}\n"
        if tournament['top_defender']:
            awards_text += f"🛡️ **Top Defender:** {tournament['top_defender']}\n"
        if tournament['top_goalkeeper']:
            awards_text += f"🧤 **Top Goalkeeper:** {tournament['top_goalkeeper']}\n"
        
        if awards_text:
            embed.add_field(name="Awards", value=awards_text, inline=False)
        else:
            embed.add_field(name="Awards", value="None yet", inline=False)
        
        # Create buttons based on permissions
        view = TournamentManagementView(tournament)
        
        await interaction.response.send_message(embed=embed, view=view)

    async def _handle_delete_tournament(self, interaction, tournament_id):
        """Handle deleting a tournament."""
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id == ADMIN_ROLE_ID for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to delete tournaments.", ephemeral=True)
            return
        
        tournament = await get_tournament_by_id(tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found.", ephemeral=True)
            return
        
        # Create confirmation view
        view = DeleteConfirmationView(tournament)
        embed = discord.Embed(
            title="Delete Tournament",
            description=f"Are you sure you want to delete **{tournament['name']}**?\n\n⚠️ This action cannot be undone and will remove all tournament data.",
            color=discord.Color.red()
        )
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# === VIEWS ===

class TournamentManagementView(View):
    def __init__(self, tournament):
        super().__init__(timeout=300)
        self.tournament = tournament
        
        # Add buttons based on tournament status and user permissions
        if not tournament['is_completed']:
            self.add_item(AddTeamsButton())
            self.add_item(EditTournamentButton())
            self.add_item(AddMatchButton())
            self.add_item(EndTournamentButton())
        
        self.add_item(ViewLeagueTableButton())
        self.add_item(ViewStatsButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True  # Allow all users to interact, buttons will handle permissions

class DeleteConfirmationView(View):
    def __init__(self, tournament):
        super().__init__(timeout=60)
        self.tournament = tournament

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.red)
    async def confirm_delete(self, button: Button, interaction: discord.Interaction):
        try:
            success = await delete_tournament(self.tournament['id'])
            if success:
                embed = discord.Embed(
                    title="Tournament Deleted",
                    description=f"**{self.tournament['name']}** has been deleted successfully.",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                await interaction.response.send_message("Failed to delete tournament.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error deleting tournament: {str(e)}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel_delete(self, button: Button, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Deletion Cancelled",
            description="Tournament deletion has been cancelled.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=None)

# === BUTTONS ===

class AddTeamsButton(Button):
    def __init__(self):
        super().__init__(label="Add Teams", style=discord.ButtonStyle.green, emoji="👥")

    async def callback(self, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id == ADMIN_ROLE_ID for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to add teams.", ephemeral=True)
            return
        
        tournament = self.view.tournament
        leagues = await get_tournament_leagues(tournament['id'])
        
        if len(leagues) > 1:
            # Multiple leagues - show league selection
            view = LeagueSelectionView(tournament, leagues, "add_teams")
            embed = discord.Embed(
                title="Select League",
                description="Choose which league to add teams to:",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            # Single league - go directly to team selection
            league = leagues[0]
            view = TeamSelectionView(tournament, league)
            embed = discord.Embed(
                title=f"Add Teams to {league['league_name']}",
                description="Select teams to add to this league:",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class EditTournamentButton(Button):
    def __init__(self):
        super().__init__(label="Edit Details", style=discord.ButtonStyle.blurple, emoji="✏️")

    async def callback(self, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id == ADMIN_ROLE_ID for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to edit tournaments.", ephemeral=True)
            return
        
        modal = TournamentEditModal(self.view.tournament)
        await interaction.response.send_modal(modal)

class AddMatchButton(Button):
    def __init__(self):
        super().__init__(label="Add Match", style=discord.ButtonStyle.green, emoji="⚽")

    async def callback(self, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id == ADMIN_ROLE_ID for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to add matches.", ephemeral=True)
            return
        
        await interaction.response.send_message("Match addition feature coming soon!", ephemeral=True)

class ViewLeagueTableButton(Button):
    def __init__(self):
        super().__init__(label="View League Table", style=discord.ButtonStyle.blurple, emoji="📊")

    async def callback(self, interaction: discord.Interaction):
        tournament = self.view.tournament
        leagues = await get_tournament_leagues(tournament['id'])
        
        if len(leagues) > 1:
            # Multiple leagues - show pagination
            view = LeagueTablePaginationView(tournament, leagues)
            await view.show_league_table(interaction, 0)
        else:
            # Single league - show directly
            league = leagues[0]
            table = await get_tournament_league_table(tournament['id'], league['id'])
            embed = create_league_table_embed(tournament, league, table)
            await interaction.response.send_message(embed=embed, ephemeral=True)

class ViewStatsButton(Button):
    def __init__(self):
        super().__init__(label="View Stats", style=discord.ButtonStyle.blurple, emoji="📈")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("Tournament stats feature coming soon!", ephemeral=True)

class EndTournamentButton(Button):
    def __init__(self):
        super().__init__(label="End Tournament", style=discord.ButtonStyle.red, emoji="🏁")

    async def callback(self, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id == ADMIN_ROLE_ID for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to end tournaments.", ephemeral=True)
            return
        
        await interaction.response.send_message("Tournament completion feature coming soon!", ephemeral=True)

# === ADDITIONAL VIEWS ===

class LeagueSelectionView(View):
    def __init__(self, tournament, leagues, action):
        super().__init__(timeout=180)
        self.tournament = tournament
        self.leagues = leagues
        self.action = action
        
        options = []
        for league in leagues:
            options.append(discord.SelectOption(
                label=league['league_name'],
                description=f"League {league['league_order']}",
                value=str(league['id'])
            ))
        
        select = Select(placeholder="Choose a league...", options=options)
        select.callback = self.league_selected
        self.add_item(select)

    async def league_selected(self, interaction: discord.Interaction):
        league_id = int(interaction.values[0])
        league = next(l for l in self.leagues if l['id'] == league_id)
        
        if self.action == "add_teams":
            view = TeamSelectionView(self.tournament, league)
            embed = discord.Embed(
                title=f"Add Teams to {league['league_name']}",
                description="Select teams to add to this league:",
                color=discord.Color.blue()
            )
            await interaction.response.edit_message(embed=embed, view=view)

class TeamSelectionView(View):
    def __init__(self, tournament, league):
        super().__init__(timeout=300)
        self.tournament = tournament
        self.league = league
        
        # This will be populated in show_teams method
        self.current_page = 0
        self.teams = []
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    # We'll implement team selection in the next part

class LeagueTablePaginationView(View):
    def __init__(self, tournament, leagues):
        super().__init__(timeout=180)
        self.tournament = tournament
        self.leagues = leagues
        self.current_page = 0

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.grey, disabled=True)
    async def previous_page(self, button: Button, interaction: discord.Interaction):
        self.current_page -= 1
        await self.show_league_table(interaction, self.current_page)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.grey)
    async def next_page(self, button: Button, interaction: discord.Interaction):
        self.current_page += 1
        await self.show_league_table(interaction, self.current_page)

    async def show_league_table(self, interaction, page):
        if page >= len(self.leagues):
            page = len(self.leagues) - 1
        if page < 0:
            page = 0
            
        self.current_page = page
        league = self.leagues[page]
        
        # Update buttons
        self.children[0].disabled = page == 0
        self.children[1].disabled = page >= len(self.leagues) - 1
        
        # Update button labels
        self.children[0].label = f"← {page}/{len(self.leagues)}"
        self.children[1].label = f"{page + 2}/{len(self.leagues)} →"
        
        table = await get_tournament_league_table(self.tournament['id'], league['id'])
        embed = create_league_table_embed(self.tournament, league, table)
        
        if hasattr(interaction, 'response') and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.edit_original_response(embed=embed, view=self)

# === HELPER FUNCTIONS ===

def create_league_table_embed(tournament, league, table):
    """Create an embed for displaying a league table."""
    embed = discord.Embed(
        title=f"{tournament['name']} - {league['league_name']}",
        color=discord.Color.gold()
    )
    
    if not table:
        embed.description = "No matches played yet."
        return embed
    
    # Create table
    table_lines = [
        "```",
        f"{'Pos':<3} {'Team':<20} {'P':<2} {'W':<2} {'D':<2} {'L':<2} {'GF':<3} {'GA':<3} {'GD':<4} {'Pts':<3}",
        "-" * 60
    ]
    
    for i, team in enumerate(table, 1):
        pos = str(i)
        name = team['guild_name'][:20]  # Truncate if too long
        played = str(team['matches_played'])
        won = str(team['wins'])
        drawn = str(team['draws'])
        lost = str(team['losses'])
        gf = str(team['goals_for'])
        ga = str(team['goals_against'])
        gd = str(team['goal_difference'])
        pts = str(team['points'])
        
        table_lines.append(
            f"{pos:<3} {name:<20} {played:<2} {won:<2} {drawn:<2} {lost:<2} {gf:<3} {ga:<3} {gd:<4} {pts:<3}"
        )
    
    table_lines.append("```")
    embed.description = "\n".join(table_lines)
    
    return embed

# === SLASH COMMANDS ===

@bot.slash_command(name="register_tournament", description="Register a new tournament.")
async def register_tournament(interaction: discord.Interaction):
    """Allows admins to register a new tournament."""
    # Check admin permissions
    member = interaction.guild.get_member(interaction.user.id)
    if not (member and any(role.id == ADMIN_ROLE_ID for role in member.roles)):
        await interaction.response.send_message("You need admin permissions to register tournaments.", ephemeral=True)
        return
    
    modal = TournamentRegistrationModal()
    await interaction.response.send_modal(modal)

@bot.slash_command(name="view_tournament", description="View tournament details and manage tournaments.")
async def view_tournament(interaction: discord.Interaction):
    """Display tournaments and allow management."""
    try:
        tournaments = await get_all_tournaments()
        
        if not tournaments:
            embed = discord.Embed(
                title="No Tournaments",
                description="No tournaments have been created yet. Use `/register_tournament` to create one.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        embed = discord.Embed(
            title="Select Tournament",
            description="Choose a tournament to view details and manage:",
            color=discord.Color.blue()
        )
        
        view = View(timeout=180)
        view.add_item(TournamentSelect(tournaments, "view"))
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        await interaction.response.send_message(f"Error loading tournaments: {str(e)}", ephemeral=True)

@bot.slash_command(name="delete_tournament", description="Delete a tournament and all its data.")
async def delete_tournament_command(interaction: discord.Interaction):
    """Allows admins to delete tournaments."""
    # Check admin permissions
    member = interaction.guild.get_member(interaction.user.id)
    if not (member and any(role.id == ADMIN_ROLE_ID for role in member.roles)):
        await interaction.response.send_message("You need admin permissions to delete tournaments.", ephemeral=True)
        return
    
    try:
        tournaments = await get_all_tournaments()
        
        if not tournaments:
            await interaction.response.send_message("No tournaments available to delete.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="Delete Tournament",
            description="⚠️ Select a tournament to delete permanently:",
            color=discord.Color.red()
        )
        
        view = View(timeout=180)
        view.add_item(TournamentSelect(tournaments, "delete"))
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        await interaction.response.send_message(f"Error loading tournaments: {str(e)}", ephemeral=True) 