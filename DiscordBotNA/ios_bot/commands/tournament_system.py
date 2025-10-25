from ios_bot.config import *
from ios_bot.database_manager import (
    create_tournament, get_all_tournaments, get_tournament_by_id, get_tournament_leagues,
    get_tournament_teams, add_team_to_tournament, remove_team_from_tournament,
    update_tournament_details, delete_tournament, get_all_teams_with_details,
    get_teams_per_league_limit, add_match_to_tournament, get_tournament_matches,
    get_tournament_league_table, complete_tournament, get_filtered_matches_for_tournament,
    get_matches_by_team, get_matches_between_teams, get_matches_by_team_with_dynamic_linking,
    get_matches_between_teams_with_dynamic_linking, bulk_auto_link_csv_team_names,
    add_manual_match_result, add_forfeit_match, update_match_result, delete_match_result, get_team_by_name,
    update_team_tournament_stats, get_team_tournament_stats
)
from ios_bot.tournament_completion import complete_tournament_with_awards
from ios_bot.commands.view_match import get_matches
from datetime import datetime
import json

# Test mode - set to True to bypass date filters for testing
TOURNAMENT_TEST_MODE = False

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
        # Defer immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        name = self.children[0].value
        try:
            num_teams = int(self.children[1].value)
            num_leagues = int(self.children[2].value)
        except ValueError:
            await interaction.edit_original_response(content="Number of teams and leagues must be valid integers.")
            return
        
        if num_teams < 2:
            await interaction.edit_original_response(content="Tournament must have at least 2 teams.")
            return
            
        if num_leagues < 1:
            await interaction.edit_original_response(content="Tournament must have at least 1 league.")
            return
            
        if num_teams % num_leagues != 0:
            await interaction.edit_original_response(content=f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues}).")
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
                
                try:
                    await interaction.edit_original_response(embed=embed)
                except discord.errors.NotFound:
                    # If the original response is not found, send a followup
                    await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                try:
                    await interaction.edit_original_response(content="Failed to create tournament. Please try again.")
                except discord.errors.NotFound:
                    await interaction.followup.send(content="Failed to create tournament. Please try again.", ephemeral=True)
        except Exception as e:
            try:
                await interaction.edit_original_response(content=f"Error creating tournament: {str(e)}")
            except discord.errors.NotFound:
                await interaction.followup.send(content=f"Error creating tournament: {str(e)}", ephemeral=True)

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
        # Defer immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        name = self.children[0].value
        try:
            num_teams = int(self.children[1].value)
            num_leagues = int(self.children[2].value)
        except ValueError:
            await interaction.edit_original_response(content="Number of teams and leagues must be valid integers.")
            return
        
        if num_teams < 2:
            await interaction.edit_original_response(content="Tournament must have at least 2 teams.")
            return
            
        if num_leagues < 1:
            await interaction.edit_original_response(content="Tournament must have at least 1 league.")
            return
            
        if num_teams % num_leagues != 0:
            await interaction.edit_original_response(content=f"Number of teams ({num_teams}) must be evenly divisible by number of leagues ({num_leagues}).")
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
                
                try:
                    await interaction.edit_original_response(embed=embed)
                except discord.errors.NotFound:
                    await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                try:
                    await interaction.edit_original_response(content="Failed to update tournament. Please try again.")
                except discord.errors.NotFound:
                    await interaction.followup.send(content="Failed to update tournament. Please try again.", ephemeral=True)
        except Exception as e:
            try:
                await interaction.edit_original_response(content=f"Error updating tournament: {str(e)}")
            except discord.errors.NotFound:
                await interaction.followup.send(content=f"Error updating tournament: {str(e)}", ephemeral=True)

class AddMatchResultModal(Modal):
    def __init__(self, tournament_id: int, home_team_name: str, away_team_name: str):
        super().__init__(title="Add Match Result")
        self.tournament_id = tournament_id
        self.home_team_name = home_team_name
        self.away_team_name = away_team_name
        
        self.add_item(InputText(
            label="Home Team Score",
            placeholder="Enter home team score...",
            max_length=2,
            required=True
        ))
        
        self.add_item(InputText(
            label="Away Team Score",
            placeholder="Enter away team score...",
            max_length=2,
            required=True
        ))
        
        self.add_item(InputText(
            label="Notes (Optional)",
            placeholder="Enter any notes about the match...",
            max_length=200,
            required=False
        ))

    async def callback(self, interaction: discord.Interaction):
        try:
            home_score = int(self.children[0].value)
            away_score = int(self.children[1].value)
            notes = self.children[2].value if self.children[2].value else None
        except ValueError:
            await interaction.response.send_message("Scores must be valid integers.", ephemeral=True)
            return
        
        if home_score < 0 or away_score < 0:
            await interaction.response.send_message("Scores cannot be negative.", ephemeral=True)
            return
        
        # Look up teams by name to get their guild IDs
        home_team = await get_team_by_name(self.home_team_name)
        away_team = await get_team_by_name(self.away_team_name)
        
        if not home_team:
            await interaction.response.send_message(f"Home team '{self.home_team_name}' not found.", ephemeral=True)
            return
        if not away_team:
            await interaction.response.send_message(f"Away team '{self.away_team_name}' not found.", ephemeral=True)
            return
        
        success, result = await add_manual_match_result(
            tournament_id=self.tournament_id,
            home_team_guild_id=home_team['guild_id'],
            away_team_guild_id=away_team['guild_id'],
            home_score=home_score,
            away_score=away_score,
            notes=notes
        )
        
        if success:
            embed = discord.Embed(
                title="Match Result Added",
                description=f"**{self.home_team_name}** vs **{self.away_team_name}** result has been added.",
                color=discord.Color.green()
            )
            embed.add_field(name="Score", value=f"{home_score} - {away_score}", inline=True)
            if notes:
                embed.add_field(name="Notes", value=notes, inline=False)
            
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"Failed to add match result: {result}", ephemeral=True)

class AddForfeitModal(Modal):
    def __init__(self, tournament_id: int, forfeiting_team_name: str, opponent_team_name: str):
        super().__init__(title="Add Forfeit")
        self.tournament_id = tournament_id
        self.forfeiting_team_name = forfeiting_team_name
        self.opponent_team_name = opponent_team_name
        
        self.add_item(InputText(
            label="Forfeit Reason",
            placeholder="Enter reason for forfeit...",
            max_length=200,
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        # Defer immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        reason = self.children[0].value
        
        if not reason.strip():
            try:
                await interaction.edit_original_response(content="Please provide a reason for the forfeit.")
            except discord.errors.NotFound:
                await interaction.followup.send(content="Please provide a reason for the forfeit.", ephemeral=True)
            return
        
        try:
            # Look up teams by name to get their guild IDs
            forfeiting_team = await get_team_by_name(self.forfeiting_team_name)
            opponent_team = await get_team_by_name(self.opponent_team_name)
            
            if not forfeiting_team:
                await interaction.response.send_message(f"Forfeiting team '{self.forfeiting_team_name}' not found.", ephemeral=True)
                return
            if not opponent_team:
                await interaction.response.send_message(f"Opponent team '{self.opponent_team_name}' not found.", ephemeral=True)
                return
            
            success, result = await add_forfeit_match(
                tournament_id=self.tournament_id,
                forfeiting_team_guild_id=forfeiting_team['guild_id'],
                opponent_team_guild_id=opponent_team['guild_id'],
                forfeit_reason=reason
            )
            
            if success:
                embed = discord.Embed(
                    title="Forfeit Added",
                    description=f"**{self.forfeiting_team_name}** has forfeited against **{self.opponent_team_name}**.",
                    color=discord.Color.orange()
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.add_field(name="Score", value="6-0 (default forfeit score)", inline=True)
                
                try:
                    await interaction.edit_original_response(embed=embed)
                except discord.errors.NotFound:
                    await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                try:
                    await interaction.edit_original_response(content=f"Failed to add forfeit: {result}")
                except discord.errors.NotFound:
                    await interaction.followup.send(content=f"Failed to add forfeit: {result}", ephemeral=True)
        except Exception as e:
            try:
                await interaction.edit_original_response(content=f"Error adding forfeit: {str(e)}")
            except discord.errors.NotFound:
                await interaction.followup.send(content=f"Error adding forfeit: {str(e)}", ephemeral=True)

class EditMatchResultModal(Modal):
    def __init__(self, tournament_id: int, match_id: str, current_home_score: int, current_away_score: int, current_notes: str = None):
        super().__init__(title="Edit Match Result")
        self.tournament_id = tournament_id
        self.match_id = match_id
        
        self.add_item(InputText(
            label="Home Team Score",
            placeholder="Enter home team score...",
            value=str(current_home_score),
            max_length=2,
            required=True
        ))
        
        self.add_item(InputText(
            label="Away Team Score",
            placeholder="Enter away team score...",
            value=str(current_away_score),
            max_length=2,
            required=True
        ))
        
        self.add_item(InputText(
            label="Notes (Optional)",
            placeholder="Enter any notes about the match...",
            value=current_notes or "",
            max_length=200,
            required=False
        ))

    async def callback(self, interaction: discord.Interaction):
        try:
            home_score = int(self.children[0].value)
            away_score = int(self.children[1].value)
            notes = self.children[2].value if self.children[2].value else None
        except ValueError:
            await interaction.response.send_message("Scores must be valid integers.", ephemeral=True)
            return
        
        if home_score < 0 or away_score < 0:
            await interaction.response.send_message("Scores cannot be negative.", ephemeral=True)
            return
        
        success, result = await update_match_result(
            tournament_id=self.tournament_id,
            match_id=self.match_id,
            home_score=home_score,
            away_score=away_score,
            notes=notes
        )
        
        if success:
            embed = discord.Embed(
                title="Match Result Updated",
                description=f"Match result has been updated successfully.",
                color=discord.Color.green()
            )
            embed.add_field(name="New Score", value=f"{home_score} - {away_score}", inline=True)
            if notes:
                embed.add_field(name="Notes", value=notes, inline=False)
            
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"Failed to update match result: {result}", ephemeral=True)

class EditTeamStatsModal(Modal):
    def __init__(self, tournament_id: int, guild_id: int, league_name: str, team_name: str, current_stats: dict):
        super().__init__(title=f"Edit {team_name} Stats")
        self.tournament_id = tournament_id
        self.guild_id = guild_id
        self.league_name = league_name
        self.team_name = team_name
        
        self.add_item(InputText(
            label="Matches Played",
            placeholder="Enter matches played...",
            value=str(current_stats.get('matches_played', 0)),
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Wins",
            placeholder="Enter wins...",
            value=str(current_stats.get('wins', 0)),
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Draws",
            placeholder="Enter draws...",
            value=str(current_stats.get('draws', 0)),
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Losses",
            placeholder="Enter losses...",
            value=str(current_stats.get('losses', 0)),
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Goals For",
            placeholder="Enter goals for...",
            value=str(current_stats.get('goals_for', 0)),
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Goals Against",
            placeholder="Enter goals against...",
            value=str(current_stats.get('goals_against', 0)),
            max_length=3,
            required=True
        ))
        
        self.add_item(InputText(
            label="Points",
            placeholder="Enter points...",
            value=str(current_stats.get('points', 0)),
            max_length=3,
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        try:
            matches_played = int(self.children[0].value)
            wins = int(self.children[1].value)
            draws = int(self.children[2].value)
            losses = int(self.children[3].value)
            goals_for = int(self.children[4].value)
            goals_against = int(self.children[5].value)
            points = int(self.children[6].value)
        except ValueError:
            await interaction.response.send_message("All values must be valid integers.", ephemeral=True)
            return
        
        # Validate stats
        if matches_played < 0 or wins < 0 or draws < 0 or losses < 0 or goals_for < 0 or goals_against < 0 or points < 0:
            await interaction.response.send_message("All values must be non-negative.", ephemeral=True)
            return
        
        if wins + draws + losses != matches_played:
            await interaction.response.send_message("Wins + Draws + Losses must equal Matches Played.", ephemeral=True)
            return
        
        success, result = await update_team_tournament_stats(
            tournament_id=self.tournament_id,
            guild_id=self.guild_id,
            league_name=self.league_name,
            matches_played=matches_played,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=goals_for,
            goals_against=goals_against,
            points=points
        )
        
        if success:
            embed = discord.Embed(
                title="Team Stats Updated",
                description=f"**{self.team_name}** stats have been updated successfully.",
                color=discord.Color.green()
            )
            embed.add_field(name="Matches", value=f"P: {matches_played} | W: {wins} | D: {draws} | L: {losses}", inline=True)
            embed.add_field(name="Goals", value=f"GF: {goals_for} | GA: {goals_against} | GD: {goals_for - goals_against}", inline=True)
            embed.add_field(name="Points", value=str(points), inline=True)
            
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"Failed to update team stats: {result}", ephemeral=True)

# === SELECT MENUS ===

class TournamentSelect(Select):
    def __init__(self, tournaments, action="view"):
        self.action = action
        options = []
        
        for tournament in tournaments[:25]:  # Discord limit
            status = "🏆" if tournament['is_completed'] else "🔄"
            description = f"{status} {tournament['num_teams']} teams, {tournament['num_leagues']} leagues"
            
            # Safely access champion field
            champion = tournament.get('champion')
            if champion:
                description += f" | Champion: {champion}"
                
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
        # Defer the response immediately to avoid timeout
        await interaction.response.defer()
        
        tournament = await get_tournament_by_id(tournament_id)
        if not tournament:
            await interaction.followup.send("Tournament not found.", ephemeral=True)
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
        if tournament.get('champion'):
            awards_text += f"🏆 **Champion:** {tournament['champion']}\n"
        if tournament.get('runner_up'):
            awards_text += f"🥈 **Runner-up:** {tournament['runner_up']}\n"
        if tournament.get('third_place'):
            awards_text += f"🥉 **Third Place:** {tournament['third_place']}\n"
        if tournament.get('top_scorer'):
            awards_text += f"⚽ **Top Scorer:** {tournament['top_scorer']}\n"
        if tournament.get('top_assister'):
            awards_text += f"👟 **Top Assister:** {tournament['top_assister']}\n"
        if tournament.get('top_defender'):
            awards_text += f"🛡️ **Top Defender:** {tournament['top_defender']}\n"
        if tournament.get('top_goalkeeper'):
            awards_text += f"🧤 **Top Goalkeeper:** {tournament['top_goalkeeper']}\n"
        
        if awards_text:
            embed.add_field(name="Awards", value=awards_text, inline=False)
        else:
            embed.add_field(name="Awards", value="None yet", inline=False)
        
        # Create buttons based on permissions
        view = TournamentManagementView(tournament)
        
        # Edit the original message with the tournament view
        await interaction.edit_original_response(embed=embed, view=view)

    async def _handle_delete_tournament(self, interaction, tournament_id):
        """Handle deleting a tournament."""
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
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
        
        # Check if user has admin permissions to show admin buttons
        # We'll check this in each button's callback since we don't have interaction here
        
        # Always show these buttons
        self.add_item(ViewLeagueTableButton())
        self.add_item(ViewStatsButton())
        
        # Only show admin buttons for active tournaments
        if not tournament['is_completed']:
            self.add_item(AddTeamsButton())
            self.add_item(EditTournamentButton())
            self.add_item(AddMatchButton())
            self.add_item(EndTournamentButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True  # Allow all users to interact, buttons will handle permissions
    
    async def on_timeout(self):
        """Handle timeout by deleting the message."""
        try:
            # Try to delete the message
            if hasattr(self, 'message') and self.message:
                await self.message.delete()
            else:
                # If no message reference, disable all items
                for item in self.children:
                    item.disabled = True
        except Exception:
            # If deletion fails, disable all items
            for item in self.children:
                item.disabled = True

class DeleteConfirmationView(View):
    def __init__(self, tournament):
        super().__init__(timeout=60)
        self.tournament = tournament

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.red)
    async def confirm_delete(self, button: Button, interaction: discord.Interaction):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer()
        
        try:
            success = await delete_tournament(self.tournament['id'])
            if success:
                embed = discord.Embed(
                    title="Tournament Deleted",
                    description=f"**{self.tournament['name']}** has been deleted successfully.",
                    color=discord.Color.green()
                )
                await interaction.edit_original_response(embed=embed, view=None)
            else:
                await interaction.edit_original_response(content="Failed to delete tournament.", embed=None, view=None)
        except Exception as e:
            await interaction.edit_original_response(content=f"Error deleting tournament: {str(e)}", embed=None, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, button: Button, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Deletion Cancelled",
            description="Tournament deletion has been cancelled.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=None)
    
    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True

# === BUTTONS ===

class AddTeamsButton(Button):
    def __init__(self):
        super().__init__(label="Add Teams", style=discord.ButtonStyle.success, emoji="👥")

    async def callback(self, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
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
            league = leagues[0] if leagues else None
            if not league:
                await interaction.response.send_message("No leagues found for this tournament.", ephemeral=True)
                return
                
            view = TeamSelectionView(tournament, league)
            await view.setup_team_selection(interaction)

class EditTournamentButton(Button):
    def __init__(self):
        super().__init__(label="Edit Details", style=discord.ButtonStyle.blurple, emoji="✏️")

    async def callback(self, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to edit tournaments.", ephemeral=True)
            return
        
        modal = TournamentEditModal(self.view.tournament)
        await interaction.response.send_modal(modal)

class AddMatchButton(Button):
    def __init__(self):
        super().__init__(label="Add Match", style=discord.ButtonStyle.success, emoji="⚽")

    async def callback(self, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to add matches.", ephemeral=True)
            return
        
        # Defer the response immediately to prevent timeout during database operations
        await interaction.response.defer(ephemeral=True)
        
        try:
            tournament = self.view.tournament
            
            # Get filtered matches for this tournament
            from datetime import datetime
            
            # Remove tournament start date constraint for testing purposes
            start_date = None  # Allow matches from any time period
            
            # Send a progress message
            await interaction.edit_original_response(content="🔍 Searching for eligible matches...")
            
            filtered_matches = await get_filtered_matches_for_tournament(tournament['id'], start_date)
            
            if not filtered_matches:
                error_msg = f"❌ No eligible matches found for this tournament.\n\n**Possible reasons:**\n• Teams haven't played matches yet\n• No matches between teams in the same league\n• All eligible matches already added to tournament"
                error_msg += f"\n\n💡 **Note:** Now searching matches from all time periods for testing."
                
                await interaction.edit_original_response(content=error_msg)
                return
            
            # Create match selection view
            view = MatchSelectionView(tournament, filtered_matches)
            test_mode_note = f" ({len(filtered_matches)} matches from all time periods)"
            embed = discord.Embed(
                title=f"Add Matches to {tournament['name']}",
                description=f"✅ Found {len(filtered_matches)} eligible matches between tournament teams.{test_mode_note}\n\n**Only showing matches between teams in the same league.**\nSelect matches to add:",
                color=discord.Color.green()
            )
            
            # Add some details about the matches
            if len(filtered_matches) > 0:
                sample_matches = filtered_matches[:3]  # Show first 3 matches as examples
                match_examples = []
                for match in sample_matches:
                    match_examples.append(f"• {match['home_team']} vs {match['away_team']} ({match['scoreline']}) - {match['datetime'][:10]}")
                
                embed.add_field(
                    name="Sample Matches",
                    value="\n".join(match_examples) + (f"\n... and {len(filtered_matches) - 3} more" if len(filtered_matches) > 3 else ""),
                    inline=False
                )
            
            await interaction.edit_original_response(embed=embed, view=view)
            
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Error loading matches: {str(e)}")

class ViewLeagueTableButton(Button):
    def __init__(self):
        super().__init__(label="View League Table", style=discord.ButtonStyle.blurple, emoji="📊")

    async def callback(self, interaction: discord.Interaction):
        tournament = self.view.tournament
        leagues = await get_tournament_leagues(tournament['id'])
        
        if not leagues:
            await interaction.response.send_message("No leagues found for this tournament.", ephemeral=True)
            return
        
        if len(leagues) > 1:
            # Multiple leagues - show pagination with management
            view = LeagueTablePaginationView(tournament, leagues)
            await view.show_league_table(interaction, 0)
        else:
            # Single league - show directly with management
            league = leagues[0]
            table = await get_tournament_league_table(tournament['id'], league['id'])
            embed = create_league_table_embed(tournament, league, table)
            
            # Add management buttons
            view = LeagueTableManagementView(tournament, league, table)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to end tournaments.", ephemeral=True)
            return
        
        # Check if tournament is already completed
        tournament = self.view.tournament
        if tournament.get('is_completed'):
            await interaction.response.send_message("This tournament is already completed.", ephemeral=True)
            return
        
        # Create confirmation view
        confirm_view = TournamentCompletionConfirmationView(tournament)
        
        embed = discord.Embed(
            title="⚠️ Complete Tournament",
            description=f"Are you sure you want to complete **{tournament['name']}**?\n\n"
                       "This will:\n"
                       "• Calculate final league standings\n"
                       "• Award tournament trophies (1st, 2nd, 3rd place)\n"
                       "• Calculate player awards (MVP, Top Scorer, etc.)\n"
                       "• Mark the tournament as completed\n\n"
                       "**This action cannot be undone!**",
            color=discord.Color.red()
        )
        
        await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)

class TournamentCompletionConfirmationView(View):
    def __init__(self, tournament):
        super().__init__(timeout=300)
        self.tournament = tournament
    
    @discord.ui.button(label="✅ Complete Tournament", style=discord.ButtonStyle.success)
    async def confirm_completion(self, button: Button, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            # Complete the tournament and calculate awards
            result = await complete_tournament_with_awards(self.tournament['id'])
            
            if result.get('success'):
                # Create success embed with all results
                embed = discord.Embed(
                    title="🏆 Tournament Completed!",
                    description=f"**{self.tournament['name']}** has been completed successfully!",
                    color=discord.Color.gold()
                )
                
                # Add league results
                for league_name, league_result in result.get('league_results', {}).items():
                    standing_text = ""
                    for i, team in enumerate(league_result.get('standings', [])[:3], 1):
                        emoji = ["🏆", "🥈", "🥉"][i-1]
                        standing_text += f"{emoji} {team['guild_name']} ({team['points']} pts)\n"
                    
                    embed.add_field(
                        name=f"🏟️ {league_name} Final Standings",
                        value=standing_text if standing_text else "No standings available",
                        inline=False
                    )
                
                # Add player awards
                awards = result.get('player_awards', {})
                if awards:
                    awards_text = ""
                    if awards.get('mvp'):
                        awards_text += f"👑 **MVP:** {awards['mvp']['name']} ({awards['mvp']['team']})\n"
                    if awards.get('top_scorer'):
                        awards_text += f"⚽ **Top Scorer:** {awards['top_scorer']['name']} ({awards['top_scorer']['goals']} goals)\n"
                    if awards.get('top_assister'):
                        awards_text += f"👟 **Top Assister:** {awards['top_assister']['name']} ({awards['top_assister']['assists']} assists)\n"
                    if awards.get('top_defender'):
                        awards_text += f"🛡️ **Top Defender:** {awards['top_defender']['name']} ({awards['top_defender']['team']})\n"
                    if awards.get('top_goalkeeper'):
                        awards_text += f"🧤 **Top Goalkeeper:** {awards['top_goalkeeper']['name']} ({awards['top_goalkeeper']['team']})\n"
                    
                    embed.add_field(
                        name="🌟 Player Awards",
                        value=awards_text if awards_text else "No awards calculated",
                        inline=False
                    )
                
                # Post announcement to fixtures channel if configured
                if FIXTURES_CHANNEL_ID:
                    try:
                        fixtures_channel = interaction.guild.get_channel(FIXTURES_CHANNEL_ID)
                        if fixtures_channel:
                            await fixtures_channel.send(embed=embed)
                    except Exception as e:
                        print(f"Error posting to fixtures channel: {e}")
                
                await interaction.edit_original_response(content=None, embed=embed, view=None)
            else:
                await interaction.edit_original_response(
                    content=f"❌ Failed to complete tournament: {result.get('error', 'Unknown error')}", 
                    embed=None, 
                    view=None
                )
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ Error completing tournament: {str(e)}", 
                embed=None, 
                view=None
            )
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_completion(self, button: Button, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="Tournament completion cancelled.", 
            embed=None, 
            view=None
        )
    
    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True

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
        
        # Add back button to return to main tournament view
        back_button = Button(label="← Back to Tournament", style=discord.ButtonStyle.secondary, emoji="🔙")
        back_button.callback = self.back_to_tournament
        self.add_item(back_button)

    async def league_selected(self, interaction: discord.Interaction):
        league_id = int(interaction.data['values'][0])
        league = next(l for l in self.leagues if l['id'] == league_id)
        
        if self.action == "add_teams":
            view = TeamSelectionView(self.tournament, league)
            await view.setup_team_selection(interaction)
    
    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True
    
    async def back_to_tournament(self, interaction: discord.Interaction):
        """Return to the main tournament view."""
        from ios_bot.commands.tournament_system import TournamentSelect
        
        # Recreate the main tournament view
        embed = discord.Embed(
            title=self.tournament['name'],
            color=discord.Color.gold() if self.tournament['is_completed'] else discord.Color.blue()
        )
        
        # Set guild info as author
        guild = interaction.guild
        if guild:
            embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        
        # Add basic info
        embed.add_field(name="Teams", value=str(self.tournament['num_teams']), inline=True)
        embed.add_field(name="Leagues", value=str(self.tournament['num_leagues']), inline=True)
        embed.add_field(name="Teams per League", value=str(self.tournament['num_teams'] // self.tournament['num_leagues']), inline=True)
        
        # Add dates
        start_date = self.tournament['start_date'].strftime('%Y-%m-%d') if self.tournament['start_date'] else "Unknown"
        end_date = self.tournament['end_date'].strftime('%Y-%m-%d') if self.tournament['end_date'] else "Ongoing"
        embed.add_field(name="Start Date", value=start_date, inline=True)
        embed.add_field(name="End Date", value=end_date, inline=True)
        embed.add_field(name="Status", value="Completed" if self.tournament['is_completed'] else "Active", inline=True)
        
        # Add teams info
        teams = await get_tournament_teams(self.tournament['id'])
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
        if self.tournament['champion']:
            awards_text += f"🏆 **Champion:** {self.tournament['champion']}\n"
        if self.tournament['runner_up']:
            awards_text += f"🥈 **Runner-up:** {self.tournament['runner_up']}\n"
        if self.tournament['third_place']:
            awards_text += f"🥉 **Third Place:** {self.tournament['third_place']}\n"
        if self.tournament['top_scorer']:
            awards_text += f"⚽ **Top Scorer:** {self.tournament['top_scorer']}\n"
        if self.tournament['top_assister']:
            awards_text += f"👟 **Top Assister:** {self.tournament['top_assister']}\n"
        if self.tournament['top_defender']:
            awards_text += f"🛡️ **Top Defender:** {self.tournament['top_defender']}\n"
        if self.tournament['top_goalkeeper']:
            awards_text += f"🧤 **Top Goalkeeper:** {self.tournament['top_goalkeeper']}\n"
        
        if awards_text:
            embed.add_field(name="Awards", value=awards_text, inline=False)
        else:
            embed.add_field(name="Awards", value="None yet", inline=False)
        
        # Create buttons based on permissions
        view = TournamentManagementView(self.tournament)
        
        await interaction.response.edit_message(embed=embed, view=view)

class TeamSelectionView(View):
    def __init__(self, tournament, league):
        super().__init__(timeout=300)
        self.tournament = tournament
        self.league = league

    async def setup_team_selection(self, interaction):
        """Setup the team selection interface."""
        try:
            # Get all available teams
            all_teams = await get_all_teams_with_details()
            if not all_teams:
                await interaction.response.send_message("No teams available in the database.", ephemeral=True)
                return
            
            # Filter to only club teams (not national teams)
            club_teams = [team for team in all_teams if not team.get('is_national_team', False)]
            
            # Get teams already in this tournament
            tournament_teams = await get_tournament_teams(self.tournament['id'])
            tournament_team_ids = {team['guild_id'] for team in tournament_teams}
            
            # Filter out teams already in tournament
            available_teams = [team for team in club_teams if team['guild_id'] not in tournament_team_ids]
            
            if not available_teams:
                await interaction.response.send_message("No teams available to add. All teams are already in the tournament.", ephemeral=True)
                return
            
            # Check league capacity
            teams_per_league = await get_teams_per_league_limit(self.tournament['id'])
            current_teams_in_league = await get_tournament_teams(self.tournament['id'], self.league['id'])
            remaining_spots = teams_per_league - len(current_teams_in_league)
            
            if remaining_spots <= 0:
                await interaction.response.send_message(f"League {self.league['league_name']} is full.", ephemeral=True)
                return
            
            # Create team selection options
            options = []
            for team in available_teams[:25]:  # Discord limit of 25
                options.append(discord.SelectOption(
                    label=team['guild_name'][:100],
                    description=f"Guild ID: {team['guild_id']}",
                    value=str(team['guild_id'])
            ))
            
            select = Select(
                placeholder=f"Add teams to {self.league['league_name']} ({remaining_spots} spots remaining)...",
                options=options,
                min_values=0,
                max_values=min(len(options), remaining_spots)
            )
            select.callback = self.team_selected
            
            self.clear_items()
            self.add_item(select)
            self.add_item(Button(label="Done", style=discord.ButtonStyle.success, custom_id="done"))
            
            # Add back button
            back_button = Button(label="← Back to Tournament", style=discord.ButtonStyle.secondary, emoji="🔙", custom_id="back")
            self.add_item(back_button)
            
            # Store available teams for later use
            self.available_teams = {str(team['guild_id']): team for team in available_teams}
            self.selected_teams = []
            
            embed = discord.Embed(
                title=f"Add Teams to {self.league['league_name']}",
                description=f"📋 **How to use:** Select teams from the dropdown below, then click 'Done' to add them.\n\n**League Info:**\n• Current teams: {len(current_teams_in_league)}/{teams_per_league}\n• Available spots: {remaining_spots}\n• You can select up to {remaining_spots} teams at once",
                color=discord.Color.blue()
            )
            
            if hasattr(interaction, 'response') and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
                
        except Exception as e:
            await interaction.response.send_message(f"Error setting up team selection: {str(e)}", ephemeral=True)

    async def team_selected(self, interaction):
        """Handle team selection."""
        # Store selected teams
        self.selected_teams = []
        for guild_id in interaction.data['values']:
            if guild_id in self.available_teams:
                self.selected_teams.append(self.available_teams[guild_id])
        
        # Update the embed to show selected teams
        if self.selected_teams:
            team_names = [team['guild_name'] for team in self.selected_teams]
            description = f"✅ **Selected teams:** {', '.join(team_names)}\n\nClick 'Done' to add these teams to {self.league['league_name']}."
        else:
            # Get current league info for display
            teams_per_league = await get_teams_per_league_limit(self.tournament['id'])
            current_teams_in_league = await get_tournament_teams(self.tournament['id'], self.league['id'])
            remaining_spots = teams_per_league - len(current_teams_in_league)
            
            description = f"📋 **How to use:** Select teams from the dropdown below, then click 'Done' to add them.\n\n**League Info:**\n• Current teams: {len(current_teams_in_league)}/{teams_per_league}\n• Available spots: {remaining_spots}\n• You can select up to {remaining_spots} teams at once"
        
        embed = discord.Embed(
            title=f"Add Teams to {self.league['league_name']}",
            description=description,
            color=discord.Color.blue()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Handle the Done button
        if interaction.data.get('custom_id') == 'done':
            await self.finish_team_selection(interaction)
            return False
        # Handle the Back button
        elif interaction.data.get('custom_id') == 'back':
            await self.back_to_tournament(interaction)
            return False
        return True

    async def finish_team_selection(self, interaction):
        """Finish the team selection process."""
        if not hasattr(self, 'selected_teams') or not self.selected_teams:
            await interaction.response.send_message("No teams selected.", ephemeral=True)
            return
        
        # Defer the response immediately to prevent timeout
        await interaction.response.defer()
        
        try:
            added_teams = []
            for team in self.selected_teams:
                try:
                    await add_team_to_tournament(
                        self.tournament['id'],
                        self.league['id'],
                        team['guild_id'],
                        team['guild_name']
                    )
                    added_teams.append(team['guild_name'])
                except Exception as e:
                    pass
            
            if added_teams:
                embed = discord.Embed(
                    title="Teams Added Successfully",
                    description=f"Added {len(added_teams)} teams to {self.league['league_name']}:",
                    color=discord.Color.green()
                )
                embed.add_field(name="Teams", value='\n'.join(f"• {name}" for name in added_teams), inline=False)
                await interaction.edit_original_response(embed=embed, view=None)
            else:
                await interaction.edit_original_response(content="No teams were added. Please try again.", embed=None, view=None)
                
        except Exception as e:
            await interaction.edit_original_response(content=f"Error adding teams: {str(e)}", embed=None, view=None)
    
    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True
    
    async def back_to_tournament(self, interaction: discord.Interaction):
        """Return to the main tournament view."""
        # Recreate the main tournament view
        embed = discord.Embed(
            title=self.tournament['name'],
            color=discord.Color.gold() if self.tournament['is_completed'] else discord.Color.blue()
        )
        
        # Set guild info as author
        guild = interaction.guild
        if guild:
            embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        
        # Add basic info
        embed.add_field(name="Teams", value=str(self.tournament['num_teams']), inline=True)
        embed.add_field(name="Leagues", value=str(self.tournament['num_leagues']), inline=True)
        embed.add_field(name="Teams per League", value=str(self.tournament['num_teams'] // self.tournament['num_leagues']), inline=True)
        
        # Add dates
        start_date = self.tournament['start_date'].strftime('%Y-%m-%d') if self.tournament['start_date'] else "Unknown"
        end_date = self.tournament['end_date'].strftime('%Y-%m-%d') if self.tournament['end_date'] else "Ongoing"
        embed.add_field(name="Start Date", value=start_date, inline=True)
        embed.add_field(name="End Date", value=end_date, inline=True)
        embed.add_field(name="Status", value="Completed" if self.tournament['is_completed'] else "Active", inline=True)
        
        # Add teams info
        teams = await get_tournament_teams(self.tournament['id'])
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
        if self.tournament['champion']:
            awards_text += f"🏆 **Champion:** {self.tournament['champion']}\n"
        if self.tournament['runner_up']:
            awards_text += f"🥈 **Runner-up:** {self.tournament['runner_up']}\n"
        if self.tournament['third_place']:
            awards_text += f"🥉 **Third Place:** {self.tournament['third_place']}\n"
        if self.tournament['top_scorer']:
            awards_text += f"⚽ **Top Scorer:** {self.tournament['top_scorer']}\n"
        if self.tournament['top_assister']:
            awards_text += f"👟 **Top Assister:** {self.tournament['top_assister']}\n"
        if self.tournament['top_defender']:
            awards_text += f"🛡️ **Top Defender:** {self.tournament['top_defender']}\n"
        if self.tournament['top_goalkeeper']:
            awards_text += f"🧤 **Top Goalkeeper:** {self.tournament['top_goalkeeper']}\n"
        
        if awards_text:
            embed.add_field(name="Awards", value=awards_text, inline=False)
        else:
            embed.add_field(name="Awards", value="None yet", inline=False)
        
        # Create buttons based on permissions
        view = TournamentManagementView(self.tournament)
        
        await interaction.response.edit_message(embed=embed, view=view)

class LeagueTablePaginationView(View):
    def __init__(self, tournament, leagues):
        super().__init__(timeout=180)
        self.tournament = tournament
        self.leagues = leagues
        self.current_page = 0

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary, disabled=True)
    async def previous_page(self, button: Button, interaction: discord.Interaction):
        self.current_page -= 1
        await self.show_league_table(interaction, self.current_page)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, button: Button, interaction: discord.Interaction):
        self.current_page += 1
        await self.show_league_table(interaction, self.current_page)
    
    @discord.ui.button(label="← Back to Tournament", style=discord.ButtonStyle.blurple, emoji="🔙")
    async def back_to_tournament(self, button: Button, interaction: discord.Interaction):
        """Return to the main tournament view."""
        # Recreate the main tournament view
        embed = discord.Embed(
            title=self.tournament['name'],
            color=discord.Color.gold() if self.tournament['is_completed'] else discord.Color.blue()
        )
        
        # Set guild info as author
        guild = interaction.guild
        if guild:
            embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        
        # Add basic info
        embed.add_field(name="Teams", value=str(self.tournament['num_teams']), inline=True)
        embed.add_field(name="Leagues", value=str(self.tournament['num_leagues']), inline=True)
        embed.add_field(name="Teams per League", value=str(self.tournament['num_teams'] // self.tournament['num_leagues']), inline=True)
        
        # Add dates
        start_date = self.tournament['start_date'].strftime('%Y-%m-%d') if self.tournament['start_date'] else "Unknown"
        end_date = self.tournament['end_date'].strftime('%Y-%m-%d') if self.tournament['end_date'] else "Ongoing"
        embed.add_field(name="Start Date", value=start_date, inline=True)
        embed.add_field(name="End Date", value=end_date, inline=True)
        embed.add_field(name="Status", value="Completed" if self.tournament['is_completed'] else "Active", inline=True)
        
        # Add teams info
        teams = await get_tournament_teams(self.tournament['id'])
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
        if self.tournament['champion']:
            awards_text += f"🏆 **Champion:** {self.tournament['champion']}\n"
        if self.tournament['runner_up']:
            awards_text += f"🥈 **Runner-up:** {self.tournament['runner_up']}\n"
        if self.tournament['third_place']:
            awards_text += f"🥉 **Third Place:** {self.tournament['third_place']}\n"
        if self.tournament['top_scorer']:
            awards_text += f"⚽ **Top Scorer:** {self.tournament['top_scorer']}\n"
        if self.tournament['top_assister']:
            awards_text += f"👟 **Top Assister:** {self.tournament['top_assister']}\n"
        if self.tournament['top_defender']:
            awards_text += f"🛡️ **Top Defender:** {self.tournament['top_defender']}\n"
        if self.tournament['top_goalkeeper']:
            awards_text += f"🧤 **Top Goalkeeper:** {self.tournament['top_goalkeeper']}\n"
        
        if awards_text:
            embed.add_field(name="Awards", value=awards_text, inline=False)
        else:
            embed.add_field(name="Awards", value="None yet", inline=False)
        
        # Create buttons based on permissions
        view = TournamentManagementView(self.tournament)
        
        await interaction.response.edit_message(embed=embed, view=view)

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
        self.children[0].label = f"◀️ Page {page + 1}"
        self.children[1].label = f"Page {page + 1} ▶️"
        
        table = await get_tournament_league_table(self.tournament['id'], league['id'])
        embed = create_league_table_embed(self.tournament, league, table)
        embed.set_footer(text=f"Page {page + 1} of {len(self.leagues)}")
        
        # Create management view for this league
        management_view = LeagueTableManagementView(self.tournament, league, table)
        
        if hasattr(interaction, 'response') and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=management_view)
        else:
            await interaction.edit_original_response(embed=embed, view=management_view)
    
    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True

# === LEAGUE TABLE MANAGEMENT VIEWS ===

class LeagueTableManagementView(View):
    def __init__(self, tournament, league, table):
        super().__init__(timeout=300)
        self.tournament = tournament
        self.league = league
        self.table = table

    @discord.ui.button(label="Add Match Result", style=discord.ButtonStyle.green, emoji="📝")
    async def add_match_result(self, button: Button, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to add match results.", ephemeral=True)
            return
        
        # Get teams from this league
        teams = await get_tournament_teams(self.tournament['id'])
        league_teams = [team for team in teams if team.get('league_name') == self.league['league_name']]
        
        if len(league_teams) < 2:
            await interaction.response.send_message("Need at least 2 teams in this league to add match results.", ephemeral=True)
            return
        
        # Create team selection view
        view = MatchTeamSelectionView(self.tournament, "add_result", self.league['league_name'])
        await view.setup_teams()
        embed = discord.Embed(
            title=f"Add Match Result - {self.league['league_name']}",
            description="Select the teams for this match:",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Add Forfeit", style=discord.ButtonStyle.secondary, emoji="🏳️")
    async def add_forfeit(self, button: Button, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to add forfeits.", ephemeral=True)
            return
        
        # Get teams from this league
        teams = await get_tournament_teams(self.tournament['id'])
        league_teams = [team for team in teams if team.get('league_name') == self.league['league_name']]
        
        if len(league_teams) < 2:
            await interaction.response.send_message("Need at least 2 teams in this league to add forfeits.", ephemeral=True)
            return
        
        # Create team selection view
        view = MatchTeamSelectionView(self.tournament, "add_forfeit", self.league['league_name'])
        await view.setup_teams()
        embed = discord.Embed(
            title=f"Add Forfeit - {self.league['league_name']}",
            description="Select the forfeiting team and their opponent:",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Edit Team Stats", style=discord.ButtonStyle.blurple, emoji="✏️")
    async def edit_team_stats(self, button: Button, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to edit team stats.", ephemeral=True)
            return
        
        # Create team selection view for editing stats
        view = TeamStatsEditView(self.tournament, self.league, self.table)
        embed = discord.Embed(
            title=f"Edit Team Stats - {self.league['league_name']}",
            description="Select a team to edit their stats:",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="View Matches", style=discord.ButtonStyle.grey, emoji="📋")
    async def view_matches(self, button: Button, interaction: discord.Interaction):
        # Get matches for this league
        matches = await get_tournament_matches(self.tournament['id'], self.league['league_name'])
        
        if not matches:
            embed = discord.Embed(
                title="No Matches",
                description=f"No matches have been played in {self.league['league_name']} yet.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Create matches view
        view = TournamentMatchesView(self.tournament, matches)
        embed = discord.Embed(
            title=f"{self.tournament['name']} - {self.league['league_name']} Matches",
            description=f"Showing {len(matches)} matches",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class TeamStatsEditView(View):
    def __init__(self, tournament, league, table):
        super().__init__(timeout=180)
        self.tournament = tournament
        self.league = league
        self.table = table
        
        # Create team selection dropdown
        options = []
        for team in table:
            options.append(discord.SelectOption(
                label=team['guild_name'],
                description=f"P: {team['matches_played']} | W: {team['wins']} | D: {team['draws']} | L: {team['losses']} | Pts: {team['points']}",
                value=str(team['guild_id'])
            ))
        
        select = Select(placeholder="Choose a team to edit stats...", options=options)
        select.callback = self.team_selected
        self.add_item(select)

    async def team_selected(self, interaction: discord.Interaction):
        # Get the selected value from the interaction data
        selected_value = interaction.data.get('values', [None])[0]
        if not selected_value:
            await interaction.response.send_message("No team selected.", ephemeral=True)
            return
            
        guild_id = int(selected_value)
        
        # Find the team in the table
        team = next((t for t in self.table if t['guild_id'] == guild_id), None)
        if not team:
            await interaction.response.send_message("Selected team not found.", ephemeral=True)
            return
        
        # Get current stats from database
        current_stats = await get_team_tournament_stats(
            self.tournament['id'], 
            guild_id, 
            self.league['league_name']
        )
        
        if not current_stats:
            await interaction.response.send_message("Could not retrieve team stats from database.", ephemeral=True)
            return
        
        # Create edit modal
        modal = EditTeamStatsModal(
            self.tournament['id'],
            guild_id,
            self.league['league_name'],
            team['guild_name'],
            current_stats
        )
        await interaction.response.send_modal(modal)

class MatchTeamSelectionView(View):
    def __init__(self, tournament, action, league_name=None):
        super().__init__(timeout=180)
        self.tournament = tournament
        self.action = action
        self.league_name = league_name
        self.home_team_id = None
        self.away_team_id = None
        self.teams = []
        
        # Create team selection dropdowns
        self.home_team_select = Select(placeholder="Loading teams...", options=[], custom_id="home_team")
        self.away_team_select = Select(placeholder="Loading teams...", options=[], custom_id="away_team")
        
        # Set up callbacks
        self.home_team_select.callback = self.home_team_selected
        self.away_team_select.callback = self.away_team_selected
        
        self.add_item(self.home_team_select)
        self.add_item(self.away_team_select)
        
        # Add confirm button
        self.confirm_button = Button(label="Confirm Selection", style=discord.ButtonStyle.green, custom_id="confirm_teams")
        self.confirm_button.callback = self.confirm_selection
        self.add_item(self.confirm_button)

    async def setup_teams(self):
        """Load teams and populate dropdowns."""
        self.teams = await get_tournament_teams(self.tournament['id'])
        
        # Filter teams by league if specified
        if self.league_name:
            self.teams = [team for team in self.teams if team.get('league_name') == self.league_name]
        
        # Populate dropdowns with team options
        team_options = []
        for team in self.teams:
            team_options.append(discord.SelectOption(
                label=team['guild_name'],
                description=f"League: {team.get('league_name', 'Unknown')}",
                value=str(team['guild_id'])
            ))
        
        self.home_team_select.options = team_options
        self.away_team_select.options = team_options
        self.home_team_select.placeholder = "Home Team"
        self.away_team_select.placeholder = "Away Team"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def home_team_selected(self, interaction: discord.Interaction):
        """Handle home team selection."""
        selected_value = interaction.data.get('values', [None])[0]
        if selected_value:
            self.home_team_id = int(selected_value)
            await interaction.response.send_message(f"Home team selected: {selected_value}", ephemeral=True)
        else:
            await interaction.response.send_message("No home team selected.", ephemeral=True)

    async def away_team_selected(self, interaction: discord.Interaction):
        """Handle away team selection."""
        selected_value = interaction.data.get('values', [None])[0]
        if selected_value:
            self.away_team_id = int(selected_value)
            await interaction.response.send_message(f"Away team selected: {selected_value}", ephemeral=True)
        else:
            await interaction.response.send_message("No away team selected.", ephemeral=True)

    async def confirm_selection(self, interaction: discord.Interaction):
        """Handle team selection confirmation."""
        try:
            # Load teams if not already loaded
            if not self.teams:
                await interaction.response.defer(ephemeral=True)
                await self.setup_teams()

            # Check if both teams are selected
            if not self.home_team_id or not self.away_team_id:
                await interaction.response.send_message("Please select both home and away teams.", ephemeral=True)
                return
            
            if self.home_team_id == self.away_team_id:
                await interaction.response.send_message("Home and away teams must be different.", ephemeral=True)
                return
            
            # Get team names
            home_team = next((t for t in self.teams if t['guild_id'] == self.home_team_id), None)
            away_team = next((t for t in self.teams if t['guild_id'] == self.away_team_id), None)
            
            if not home_team or not away_team:
                await interaction.response.send_message("Could not find selected teams.", ephemeral=True)
                return
            
            if self.action == "add_result":
                # Create match result modal
                modal = AddMatchResultModal(
                    self.tournament['id'],
                    home_team['guild_name'],
                    away_team['guild_name']
                )
                await interaction.response.send_modal(modal)
            elif self.action == "add_forfeit":
                # Create forfeit modal
                modal = AddForfeitModal(
                    self.tournament['id'],
                    home_team['guild_name'],  # Assuming home team is forfeiting
                    away_team['guild_name']
                )
                await interaction.response.send_modal(modal)
        except Exception as e:
            await interaction.response.send_message(f"Error during team selection: {str(e)}", ephemeral=True)

class TournamentMatchesView(View):
    def __init__(self, tournament, matches):
        super().__init__(timeout=300)
        self.tournament = tournament
        self.matches = matches
        self.current_page = 0
        self.matches_per_page = 5

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.grey, disabled=True)
    async def previous_page(self, button: Button, interaction: discord.Interaction):
        self.current_page -= 1
        await self.show_matches(interaction, self.current_page)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.grey)
    async def next_page(self, button: Button, interaction: discord.Interaction):
        self.current_page += 1
        await self.show_matches(interaction, self.current_page)

    @discord.ui.button(label="Edit Match", style=discord.ButtonStyle.blurple)
    async def edit_match(self, button: Button, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to edit matches.", ephemeral=True)
            return
        
        # Create match selection view
        view = MatchSelectionView(self.tournament, self.matches, "edit")
        embed = discord.Embed(
            title="Edit Match",
            description="Select a match to edit:",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Delete Match", style=discord.ButtonStyle.red)
    async def delete_match(self, button: Button, interaction: discord.Interaction):
        # Check admin permissions
        member = interaction.guild.get_member(interaction.user.id)
        if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
            await interaction.response.send_message("You need admin permissions to delete matches.", ephemeral=True)
            return
        
        # Create match selection view
        view = MatchSelectionView(self.tournament, self.matches, "delete")
        embed = discord.Embed(
            title="Delete Match",
            description="Select a match to delete:",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_matches(self, interaction, page):
        if page >= len(self.matches):
            page = len(self.matches) - 1
        if page < 0:
            page = 0
            
        self.current_page = page
        
        # Update buttons
        self.children[0].disabled = page == 0
        self.children[1].disabled = page >= len(self.matches) - 1
        
        # Get matches for current page
        start_idx = page * self.matches_per_page
        end_idx = start_idx + self.matches_per_page
        page_matches = self.matches[start_idx:end_idx]
        
        embed = discord.Embed(
            title=f"{self.tournament['name']} - Matches",
            description=f"Showing matches {start_idx + 1}-{min(end_idx, len(self.matches))} of {len(self.matches)}",
            color=discord.Color.blue()
        )
        
        for match in page_matches:
            match_text = f"**{match['home_team']}** vs **{match['away_team']}** ({match['scoreline']})"
            if match.get('notes'):
                match_text += f"\n*{match['notes']}*"
            embed.add_field(name=f"Match {match.get('match_id', 'Unknown')}", value=match_text, inline=False)
        
        if hasattr(interaction, 'response') and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.edit_original_response(embed=embed, view=self)

class MatchSelectionView(View):
    def __init__(self, tournament, matches, action):
        super().__init__(timeout=180)
        self.tournament = tournament
        self.matches = matches
        self.action = action
        
        # Create match selection dropdown
        options = []
        for match in matches:
            match_text = f"{match['home_team']} vs {match['away_team']} ({match['scoreline']})"
            options.append(discord.SelectOption(
                label=match_text[:100],
                description=f"Match ID: {match.get('match_id', 'Unknown')}",
                value=str(match.get('match_id', ''))
            ))
        
        select = Select(placeholder="Choose a match...", options=options)
        select.callback = self.match_selected
        self.add_item(select)

    async def match_selected(self, interaction: discord.Interaction):
        match_id = interaction.values[0]
        
        # Find the match
        match = next((m for m in self.matches if str(m.get('match_id')) == match_id), None)
        if not match:
            await interaction.response.send_message("Selected match not found.", ephemeral=True)
            return
        
        if self.action == "edit":
            # Create edit modal
            modal = EditMatchResultModal(
                self.tournament['id'],
                match_id,
                match.get('home_score', 0),
                match.get('away_score', 0),
                match.get('notes')
            )
            await interaction.response.send_modal(modal)
        elif self.action == "delete":
            # Create delete confirmation view
            view = DeleteMatchConfirmationView(self.tournament, match)
            embed = discord.Embed(
                title="Delete Match",
                description=f"Are you sure you want to delete this match?\n\n**{match['home_team']}** vs **{match['away_team']}** ({match['scoreline']})",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class DeleteMatchConfirmationView(View):
    def __init__(self, tournament, match):
        super().__init__(timeout=60)
        self.tournament = tournament
        self.match = match

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.red)
    async def confirm_delete(self, button: Button, interaction: discord.Interaction):
        try:
            success = await delete_match_result(self.tournament['id'], self.match['match_id'])
            if success:
                embed = discord.Embed(
                    title="Match Deleted",
                    description=f"Match **{self.match['home_team']}** vs **{self.match['away_team']}** has been deleted.",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                await interaction.response.send_message("Failed to delete match.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error deleting match: {str(e)}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel_delete(self, button: Button, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Deletion Cancelled",
            description="Match deletion has been cancelled.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=None)

# === MATCH SELECTION VIEWS ===

class MatchAddConfirmationView(View):
    def __init__(self, tournament, selected_matches, filtered_matches):
        super().__init__(timeout=60)
        self.tournament = tournament
        self.selected_matches = selected_matches
        self.filtered_matches = filtered_matches
    
    @discord.ui.button(label="✅ Confirm & Add Matches", style=discord.ButtonStyle.success)
    async def confirm_add(self, button: Button, interaction: discord.Interaction):
        """Actually add the matches to the tournament."""
        # Defer the response immediately to prevent timeout
        await interaction.response.defer()
        
        try:
            # Find the selected match data
            selected_match_data = []
            for match in self.filtered_matches:
                if match['match_id'] in self.selected_matches:
                    selected_match_data.append(match)
            
            # Add matches to tournament
            added_matches = []
            failed_matches = []
            
            for match_data in selected_match_data:
                try:
                    success = await add_match_to_tournament(
                        self.tournament['id'],
                        match_data['match_id'],
                        match_data['home_team_guild_id'],
                        match_data['away_team_guild_id']
                    )
                    if success:
                        added_matches.append(f"{match_data['home_team']} vs {match_data['away_team']} ({match_data['scoreline']})")
                    else:
                        failed_matches.append(f"{match_data['home_team']} vs {match_data['away_team']}")
                except Exception as e:
                    failed_matches.append(f"{match_data['home_team']} vs {match_data['away_team']} (Error: {str(e)})")
            
            # Create result embed
            embed = discord.Embed(
                title="✅ Matches Added to Tournament",
                color=discord.Color.green() if added_matches else discord.Color.red()
            )
            
            if added_matches:
                embed.add_field(
                    name=f"✅ Successfully Added ({len(added_matches)})",
                    value="\n".join(f"• {match}" for match in added_matches[:10]) + (f"\n... and {len(added_matches) - 10} more" if len(added_matches) > 10 else ""),
                    inline=False
                )
            
            if failed_matches:
                embed.add_field(
                    name=f"❌ Failed to Add ({len(failed_matches)})",
                    value="\n".join(f"• {match}" for match in failed_matches[:10]) + (f"\n... and {len(failed_matches) - 10} more" if len(failed_matches) > 10 else ""),
                    inline=False
                )
            
            if added_matches:
                embed.description = f"Tournament statistics have been updated automatically."
            
            await interaction.edit_original_response(embed=embed, view=None)
            
        except Exception as e:
            await interaction.edit_original_response(content=f"Error processing matches: {str(e)}", embed=None, view=None)
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_add(self, button: Button, interaction: discord.Interaction):
        """Cancel the match addition."""
        embed = discord.Embed(
            title="❌ Match Addition Cancelled",
            description="No matches were added to the tournament.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)
    
    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True

class MatchSelectionView(View):
    def __init__(self, tournament, filtered_matches):
        super().__init__(timeout=300)
        self.tournament = tournament
        self.filtered_matches = filtered_matches
        self.current_page = 0
        self.matches_per_page = 25
        self.total_pages = (len(filtered_matches) - 1) // self.matches_per_page + 1
        self.selected_matches = set()
        
        self.update_view()

    def update_view(self):
        """Update the view with current page of matches."""
        self.clear_items()
        
        start_idx = self.current_page * self.matches_per_page
        end_idx = start_idx + self.matches_per_page
        page_matches = self.filtered_matches[start_idx:end_idx]
        
        if page_matches:
            # Create match selection dropdown
            options = []
            for match in page_matches:
                home_team = match['home_team']
                away_team = match['away_team']
                scoreline = match['scoreline']
                match_date = match['datetime'][:10]  # Just the date part
                
                label = f"{home_team} vs {away_team} ({scoreline})"
                description = f"Played on {match_date}"
                
                # Check if already selected
                if match['match_id'] in self.selected_matches:
                    label = f"✓ {label}"
                
                options.append(discord.SelectOption(
                    label=label[:100],
                    description=description[:100],
                    value=match['match_id']
                ))
            
            select = Select(
                placeholder=f"Select matches to add ({len(self.selected_matches)} selected)...",
                options=options,
                min_values=0,
                max_values=len(options)
            )
            select.callback = self.match_selected
            self.add_item(select)
        
        # Add pagination buttons if needed
        if self.total_pages > 1:
            prev_button = Button(
                label="◀️ Previous",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page == 0
            )
            prev_button.callback = self.previous_page
            self.add_item(prev_button)
            
            next_button = Button(
                label="Next ▶️",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page >= self.total_pages - 1
            )
            next_button.callback = self.next_page
            self.add_item(next_button)
        
        # Add control buttons
        if self.selected_matches:
            confirm_button = Button(
                label=f"Add {len(self.selected_matches)} Matches",
                style=discord.ButtonStyle.success
            )
            confirm_button.callback = self.confirm_selection
            self.add_item(confirm_button)
        
        clear_button = Button(
            label="Clear Selection",
            style=discord.ButtonStyle.red,
            disabled=len(self.selected_matches) == 0
        )
        clear_button.callback = self.clear_selection
        self.add_item(clear_button)
        
        # Add back button
        back_button = Button(
            label="← Back to Tournament",
            style=discord.ButtonStyle.blurple,
            emoji="🔙"
        )
        back_button.callback = self.back_to_tournament
        self.add_item(back_button)

    async def match_selected(self, interaction):
        """Handle match selection."""
        # Toggle selection for selected matches
        for match_id in interaction.data['values']:
            if match_id in self.selected_matches:
                self.selected_matches.remove(match_id)
            else:
                self.selected_matches.add(match_id)
        
        # Update the view
        self.update_view()
        
        embed = discord.Embed(
            title=f"Add Matches to {self.tournament['name']}",
            description=f"Selected {len(self.selected_matches)} matches to add to the tournament.\nPage {self.current_page + 1} of {self.total_pages}",
            color=discord.Color.green()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def previous_page(self, interaction):
        """Go to previous page."""
        self.current_page -= 1
        self.update_view()
        
        embed = discord.Embed(
            title=f"Add Matches to {self.tournament['name']}",
            description=f"Selected {len(self.selected_matches)} matches to add to the tournament.\nPage {self.current_page + 1} of {self.total_pages}",
            color=discord.Color.green()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_page(self, interaction):
        """Go to next page."""
        self.current_page += 1
        self.update_view()
        
        embed = discord.Embed(
            title=f"Add Matches to {self.tournament['name']}",
            description=f"Selected {len(self.selected_matches)} matches to add to the tournament.\nPage {self.current_page + 1} of {self.total_pages}",
            color=discord.Color.green()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def clear_selection(self, interaction):
        """Clear all selected matches."""
        self.selected_matches.clear()
        self.update_view()
        
        embed = discord.Embed(
            title=f"Add Matches to {self.tournament['name']}",
            description=f"Selection cleared. Page {self.current_page + 1} of {self.total_pages}",
            color=discord.Color.green()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def confirm_selection(self, interaction):
        """Show confirmation dialog before adding matches."""
        if not self.selected_matches:
            await interaction.response.send_message("No matches selected.", ephemeral=True)
            return
        
        # Show confirmation dialog
        view = MatchAddConfirmationView(self.tournament, self.selected_matches, self.filtered_matches)
        
        # Find the selected match data for preview
        selected_match_data = []
        for match in self.filtered_matches:
            if match['match_id'] in self.selected_matches:
                selected_match_data.append(match)
        
        # Create confirmation embed
        embed = discord.Embed(
            title="🔄 Confirm Match Addition",
            description=f"Are you sure you want to add **{len(self.selected_matches)}** matches to **{self.tournament['name']}**?",
            color=discord.Color.orange()
        )
        
        # Show preview of selected matches
        preview_text = ""
        for i, match in enumerate(selected_match_data[:5]):
            preview_text += f"• {match['home_team']} vs {match['away_team']} ({match['scoreline']})\n"
        
        if len(selected_match_data) > 5:
            preview_text += f"... and {len(selected_match_data) - 5} more matches"
        
        embed.add_field(
            name="Selected Matches Preview",
            value=preview_text,
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Warning",
            value="This action cannot be undone. Tournament statistics will be updated automatically.",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def on_timeout(self):
        """Handle timeout by disabling all items."""
        for item in self.children:
            item.disabled = True
    
    async def back_to_tournament(self, interaction):
        """Return to the main tournament view."""
        # Recreate the main tournament view
        embed = discord.Embed(
            title=self.tournament['name'],
            color=discord.Color.gold() if self.tournament['is_completed'] else discord.Color.blue()
        )
        
        # Set guild info as author
        guild = interaction.guild
        if guild:
            embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        
        # Add basic info
        embed.add_field(name="Teams", value=str(self.tournament['num_teams']), inline=True)
        embed.add_field(name="Leagues", value=str(self.tournament['num_leagues']), inline=True)
        embed.add_field(name="Teams per League", value=str(self.tournament['num_teams'] // self.tournament['num_leagues']), inline=True)
        
        # Add dates
        start_date = self.tournament['start_date'].strftime('%Y-%m-%d') if self.tournament['start_date'] else "Unknown"
        end_date = self.tournament['end_date'].strftime('%Y-%m-%d') if self.tournament['end_date'] else "Ongoing"
        embed.add_field(name="Start Date", value=start_date, inline=True)
        embed.add_field(name="End Date", value=end_date, inline=True)
        embed.add_field(name="Status", value="Completed" if self.tournament['is_completed'] else "Active", inline=True)
        
        # Add teams info
        teams = await get_tournament_teams(self.tournament['id'])
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
        if self.tournament.get('champion'):
            awards_text += f"🏆 **Champion:** {self.tournament['champion']}\n"
        if self.tournament.get('runner_up'):
            awards_text += f"🥈 **Runner-up:** {self.tournament['runner_up']}\n"
        if self.tournament.get('third_place'):
            awards_text += f"🥉 **Third Place:** {self.tournament['third_place']}\n"
        if self.tournament.get('top_scorer'):
            awards_text += f"⚽ **Top Scorer:** {self.tournament['top_scorer']}\n"
        if self.tournament.get('top_assister'):
            awards_text += f"👟 **Top Assister:** {self.tournament['top_assister']}\n"
        if self.tournament.get('top_defender'):
            awards_text += f"🛡️ **Top Defender:** {self.tournament['top_defender']}\n"
        if self.tournament.get('top_goalkeeper'):
            awards_text += f"🧤 **Top Goalkeeper:** {self.tournament['top_goalkeeper']}\n"
        
        if awards_text:
            embed.add_field(name="Awards", value=awards_text, inline=False)
        else:
            embed.add_field(name="Awards", value="None yet", inline=False)
        
        # Create buttons based on permissions
        view = TournamentManagementView(self.tournament)
        
        await interaction.response.edit_message(embed=embed, view=view)

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
        f"{'#':<1} {'Team':<20} {'P':<2} {'W':<2} {'D':<2} {'L':<2} {'GF':<3} {'GA':<3} {'GD':<4} {'Pts':<3}",
        "-" * 56
    ]
    
    for i, team in enumerate(table, 1):
        pos = str(i)
        name = team['guild_name'][:19]  # Truncate if too long
        played = str(team['matches_played'])
        won = str(team['wins'])
        drawn = str(team['draws'])
        lost = str(team['losses'])
        gf = str(team['goals_for'])
        ga = str(team['goals_against'])
        gd = str(team['goal_difference'])
        pts = str(team['points'])
        
        table_lines.append(
            f"{pos:<1} {name:<20} {played:<2} {won:<2} {drawn:<2} {lost:<2} {gf:<3} {ga:<3} {gd:<4} {pts:<3}"
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
    if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
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
        # Only try to send error response if we haven't responded yet
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error loading tournaments: {str(e)}", ephemeral=True)
            else:
                # Use followup if response was already sent
                await interaction.followup.send(f"Error loading tournaments: {str(e)}", ephemeral=True)
        except discord.errors.NotFound:
            # Interaction expired, nothing we can do
            print(f"Tournament view error (interaction expired): {str(e)}")
        except Exception as secondary_e:
            # Final fallback - just log the error
            print(f"Tournament view error: {str(e)}, Secondary error: {str(secondary_e)}")

@bot.slash_command(name="delete_tournament", description="Delete a tournament and all its data.")
async def delete_tournament_command(interaction: discord.Interaction):
    """Allows admins to delete tournaments."""
    # Check admin permissions
    member = interaction.guild.get_member(interaction.user.id)
    if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
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
        # Only try to send error response if we haven't responded yet
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error loading tournaments: {str(e)}", ephemeral=True) 
            else:
                # Use followup if response was already sent
                await interaction.followup.send(f"Error loading tournaments: {str(e)}", ephemeral=True)
        except discord.errors.NotFound:
            # Interaction expired, nothing we can do
            print(f"Tournament delete error (interaction expired): {str(e)}")
        except Exception as secondary_e:
            # Final fallback - just log the error
            print(f"Tournament delete error: {str(e)}, Secondary error: {str(secondary_e)}")

# === TRANSFER MANAGEMENT COMMANDS ===

@bot.slash_command(name="transfer_window_status", description="Control the player transfer window.")
async def transfer_window_status_command(interaction: discord.Interaction, status: str):
    """Control transfer window status."""
    # Check admin permissions
    member = interaction.guild.get_member(interaction.user.id)
    if not (member and any(role.id in [ADMIN_ROLE_ID, MY_PERM] for role in member.roles)):
        await interaction.response.send_message("You need admin permissions to control transfer windows.", ephemeral=True)
        return
    
    # Validate status input
    if status.upper() not in ['ON', 'OFF']:
        await interaction.response.send_message("Status must be either 'ON' or 'OFF'.", ephemeral=True)
        return
    
    try:
        from ios_bot.transfer_management import set_transfer_window_status, get_transfer_window_status
        
        is_open = status.upper() == 'ON'
        current_status = await get_transfer_window_status()
        
        if current_status == is_open:
            status_text = "open" if is_open else "closed"
            await interaction.response.send_message(f"Transfer window is already {status_text}.", ephemeral=True)
            return
        
        await set_transfer_window_status(is_open, interaction.user.id, interaction.user.display_name)
        
        status_text = "opened" if is_open else "closed"
        await interaction.response.send_message(f"✅ Transfer window has been {status_text}.", ephemeral=True)
        
    except Exception as e:
        await interaction.response.send_message(f"Error updating transfer window: {str(e)}", ephemeral=True)

@bot.slash_command(name="view_transfers", description="View recent player transfers.")
async def view_transfers_command(interaction: discord.Interaction, limit: int = 20):
    """View recent player transfers."""
    try:
        from ios_bot.transfer_management import get_player_transfer_history, get_transfer_window_status
        
        # Limit the results to prevent spam
        limit = min(max(limit, 1), 50)
        
        transfers = await get_player_transfer_history(limit)
        window_status = await get_transfer_window_status()
        
        embed = discord.Embed(
            title=f"Recent Player Transfers ({len(transfers)})",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        # Add transfer window status
        window_icon = "🟢" if window_status else "🔴"
        window_text = "OPEN" if window_status else "CLOSED"
        embed.add_field(
            name="Transfer Window Status",
            value=f"{window_icon} {window_text}",
            inline=False
        )
        
        if not transfers:
            embed.add_field(
                name="No Transfers Found",
                value="No player transfers have been recorded yet.",
                inline=False
            )
        else:
            # Group transfers by type for better organization
            transfer_text = ""
            for transfer in transfers:
                # Format transfer entry
                player_name = transfer['player_name']
                transfer_type = transfer['transfer_type']
                transfer_date = transfer['transfer_date'].strftime('%m/%d/%y')
                
                if transfer_type == 'JOIN':
                    from_text = "Free Agent"
                    to_text = transfer['to_team_name']
                    icon = "🔵"
                elif transfer_type == 'LEAVE':
                    from_text = transfer['from_team_name']
                    to_text = "Free Agent"
                    icon = "🔴"
                else:  # TRANSFER
                    from_text = transfer['from_team_name']
                    to_text = transfer['to_team_name']
                    icon = "🔄"
                
                # Add reason if available
                reason_text = ""
                if transfer.get('reason'):
                    reason_text = f" ({transfer['reason']})"
                
                transfer_text += f"{icon} **{player_name}** {from_text} → {to_text}{reason_text} - {transfer_date}\n"
                
                # Prevent field from getting too long
                if len(transfer_text) > 900:
                    transfer_text += f"... and {len(transfers) - transfers.index(transfer) - 1} more"
                    break
            
            embed.add_field(
                name="Transfer History",
                value=transfer_text if transfer_text else "No transfers found",
                inline=False
            )
        
        embed.set_footer(text=f"Showing last {len(transfers)} transfers")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.response.send_message(f"Error retrieving transfers: {str(e)}", ephemeral=True)

class MatchViewTeamSelectionView(View):
    def __init__(self, interaction_type: str, limit: int = 10):
        super().__init__(timeout=180)
        self.interaction_type = interaction_type  # "team_matches" or "head_to_head"
        self.limit = limit
        self.selected_teams = []
        self.max_teams = 1 if interaction_type == "team_matches" else 2
        
    async def setup_team_selection(self, interaction):
        """Set up team selection dropdown."""
        # Get all teams
        teams = await get_all_teams_with_details()
        
        if not teams:
            await interaction.edit_original_response(content="❌ No teams found in the database.")
            return
        
        # Limit to first 25 teams (Discord limit)
        teams = teams[:25]
        
        options = []
        for team in teams:
            team_type = "🌍" if team.get('is_national_team', False) else "🏢"
            options.append(SelectOption(
                label=f"{team_type} {team['guild_name']}"[:100],
                description=f"Captain: {team['captain_name']}"[:100],
                value=str(team['guild_id'])
            ))
        
        select = Select(
            placeholder=f"Select {'a team' if self.max_teams == 1 else 'teams'} to view matches...",
            min_values=1,
            max_values=self.max_teams,
            options=options
        )
        select.callback = self.team_selected
        
        self.clear_items()
        self.add_item(select)
        
        # Create embed
        embed = discord.Embed(
            title=f"🏆 {'Team Matches' if self.interaction_type == 'team_matches' else 'Head-to-Head Matches'}",
            description=f"Select {'a team' if self.max_teams == 1 else 'two teams'} to view match history.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Available Teams",
            value=f"Found {len(teams)} teams in the database",
            inline=False
        )
        
        # Handle both ApplicationContext and Interaction objects
        if hasattr(interaction, 'edit_original_response'):
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.edit(embed=embed, view=self)
    
    async def team_selected(self, interaction):
        """Handle team selection."""
        await interaction.response.defer()
        
        selected_guild_ids = [int(val) for val in interaction.data['values']]
        
        # Get team details
        teams = await get_all_teams_with_details()
        selected_teams = [team for team in teams if team['guild_id'] in selected_guild_ids]
        
        if self.interaction_type == "team_matches":
            await self.show_team_matches(interaction, selected_teams[0])
        else:
            await self.show_head_to_head(interaction, selected_teams[0], selected_teams[1])
    
    async def show_team_matches(self, interaction, team):
        """Show matches for a single team."""
        try:
            # Get matches for the team using dynamic matching
            matches = await get_matches_by_team_with_dynamic_linking(team['guild_id'], limit=self.limit)
            
            if not matches:
                await interaction.edit_original_response(content=f"❌ No matches found for team '{team['guild_name']}'.", view=None)
                return
            
            # Create embed
            embed = discord.Embed(
                title=f"🏆 Recent Matches for {team['guild_name']}",
                description=f"Showing {len(matches)} most recent matches",
                color=discord.Color.blue()
            )
            
            match_list = []
            for match in matches:
                # Format match info
                home_team = match['home_team_name']
                away_team = match['away_team_name']
                scoreline = match['scoreline'] or "N/A"
                match_date = match['datetime'].strftime("%Y-%m-%d") if hasattr(match['datetime'], 'strftime') else str(match['datetime'])[:10]
                
                # Determine if this team was home or away
                if match['home_guild_id'] == team['guild_id']:
                    vs_team = away_team
                    result_indicator = "🏠"
                else:
                    vs_team = home_team
                    result_indicator = "✈️"
                
                match_list.append(f"{result_indicator} **{team['guild_name']}** vs **{vs_team}** ({scoreline}) - {match_date}")
            
            embed.add_field(
                name="📅 Match History",
                value="\n".join(match_list),
                inline=False
            )
            
            embed.set_footer(text=f"🏠 = Home match | ✈️ = Away match | Total matches: {len(matches)}")
            
            await interaction.edit_original_response(embed=embed, view=None)
            
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Error retrieving matches: {str(e)}", view=None)
    
    async def show_head_to_head(self, interaction, team1, team2):
        """Show head-to-head matches between two teams."""
        try:
            if team1['guild_id'] == team2['guild_id']:
                await interaction.edit_original_response(content="❌ Cannot compare a team with itself.", view=None)
                return
            
            # Get matches between the teams using dynamic matching
            matches = await get_matches_between_teams_with_dynamic_linking(team1['guild_id'], team2['guild_id'], limit=self.limit)
            
            if not matches:
                await interaction.edit_original_response(content=f"❌ No matches found between '{team1['guild_name']}' and '{team2['guild_name']}'.", view=None)
                return
            
            # Create embed
            embed = discord.Embed(
                title=f"⚔️ Head-to-Head: {team1['guild_name']} vs {team2['guild_name']}",
                description=f"Showing {len(matches)} most recent matches between these teams",
                color=discord.Color.orange()
            )
            
            # Calculate head-to-head stats
            team1_wins = 0
            team2_wins = 0
            draws = 0
            
            match_list = []
            for match in matches:
                home_team = match['home_team_name']
                away_team = match['away_team_name']
                scoreline = match['scoreline'] or "N/A"
                match_date = match['datetime'].strftime("%Y-%m-%d") if hasattr(match['datetime'], 'strftime') else str(match['datetime'])[:10]
                
                # Determine result (this is a simple example - you might want more sophisticated parsing)
                if scoreline != "N/A" and "-" in scoreline:
                    try:
                        score_parts = scoreline.split("-")
                        home_score = int(score_parts[0].strip())
                        away_score = int(score_parts[1].strip())
                        
                        if home_score > away_score:
                            winner = home_team
                        elif away_score > home_score:
                            winner = away_team
                        else:
                            winner = "Draw"
                        
                        # Count wins
                        if winner == team1['guild_name']:
                            team1_wins += 1
                            result_emoji = "🟢" if match['home_team_name'] == team1['guild_name'] else "🔵"
                        elif winner == team2['guild_name']:
                            team2_wins += 1
                            result_emoji = "🔴" if match['home_team_name'] == team2['guild_name'] else "🟠"
                        else:
                            draws += 1
                            result_emoji = "⚪"
                    except:
                        result_emoji = "❔"
                else:
                    result_emoji = "❔"
                
                match_list.append(f"{result_emoji} **{home_team}** vs **{away_team}** ({scoreline}) - {match_date}")
            
            # Add head-to-head record
            if team1_wins + team2_wins + draws > 0:
                embed.add_field(
                    name="📊 Head-to-Head Record",
                    value=f"**{team1['guild_name']}:** {team1_wins} wins\n**{team2['guild_name']}:** {team2_wins} wins\n**Draws:** {draws}",
                    inline=False
                )
                
                embed.add_field(
                    name="📅 Recent Matches",
                    value="\n".join(match_list[:10]) + (f"\n... and {len(match_list) - 10} more" if len(match_list) > 10 else ""),
                    inline=False
                )
            
            embed.set_footer(text=f"Total matches: {len(matches)}")
            
            await interaction.edit_original_response(embed=embed, view=None)
            
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Error retrieving matches: {str(e)}", view=None)

@bot.slash_command(name="view_team_matches", description="View recent matches for a specific team.")
async def view_team_matches_command(ctx: discord.ApplicationContext, limit: int = 10):
    """View recent matches for a specific team using team selection."""
    await ctx.defer(ephemeral=True)
    
    limit = min(max(limit, 1), 25)
    view = MatchViewTeamSelectionView("team_matches", limit)
    await view.setup_team_selection(ctx)

@bot.slash_command(name="view_head_to_head", description="View head-to-head matches between two teams.")
async def view_head_to_head_command(ctx: discord.ApplicationContext, limit: int = 10):
    """View head-to-head matches between two teams using team selection."""
    await ctx.defer(ephemeral=True)
    
    limit = min(max(limit, 1), 25)
    view = MatchViewTeamSelectionView("head_to_head", limit)
    await view.setup_team_selection(ctx)

@bot.slash_command(name="league_table", description="View the league table for a tournament (public).")
async def league_table_command(interaction: discord.Interaction, tournament_name: str):
    """View the league table for a specific tournament (public message)."""
    try:
        # Get tournament by name
        tournament = await get_tournament_by_name(tournament_name)
        if not tournament:
            await interaction.response.send_message(f"Tournament '{tournament_name}' not found.", ephemeral=True)
            return
        
        # Get leagues for this tournament
        leagues = await get_tournament_leagues(tournament['id'])
        if not leagues:
            await interaction.response.send_message("No leagues found for this tournament.", ephemeral=True)
            return
        
        # Create league selection view
        view = LeagueTablePublicView(tournament, leagues)
        embed = discord.Embed(
            title=f"League Tables - {tournament['name']}",
            description="Select a league to view its table:",
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed, view=view)
        
    except Exception as e:
        await interaction.response.send_message(f"Error viewing league table: {str(e)}", ephemeral=True)

@bot.slash_command(name="edit_match", description="Edit a match result (Admin only).")
async def edit_match_command(interaction: discord.Interaction, tournament_name: str):
    """Edit a match result in a tournament."""
    # Check admin permissions
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to edit matches.", ephemeral=True)
        return
    
    try:
        # Get tournament by name
        tournament = await get_tournament_by_name(tournament_name)
        if not tournament:
            await interaction.response.send_message(f"Tournament '{tournament_name}' not found.", ephemeral=True)
            return
        
        # Get matches for this tournament
        matches = await get_tournament_matches(tournament['id'])
        if not matches:
            await interaction.response.send_message("No matches found for this tournament.", ephemeral=True)
            return
        
        # Create match selection view
        view = MatchEditSelectionView(tournament, matches)
        embed = discord.Embed(
            title=f"Edit Match - {tournament['name']}",
            description="Select a match to edit:",
            color=discord.Color.orange()
        )
        
        await interaction.response.send_message(embed=embed, view=view)
        
    except Exception as e:
        await interaction.response.send_message(f"Error editing match: {str(e)}", ephemeral=True)

class LeagueTablePublicView(View):
    """View for selecting a league to display its table publicly."""
    
    def __init__(self, tournament, leagues):
        super().__init__(timeout=60)
        self.tournament = tournament
        self.leagues = leagues
        self.current_page = 0
        
        # Add league selection dropdown
        options = []
        for league in leagues:
            options.append(discord.SelectOption(
                label=f"League {league['name']}",
                description=f"View table for League {league['name']}",
                value=str(league['id'])
            ))
        
        self.add_item(LeagueSelectPublic(options))
    
    async def on_timeout(self):
        """Handle timeout."""
        pass

class LeagueSelectPublic(Select):
    """Select component for choosing a league to view publicly."""
    
    def __init__(self, options):
        super().__init__(
            placeholder="Select a league to view...",
            options=options,
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle league selection."""
        selected_league_id = int(self.values[0])
        selected_league = None
        
        for league in self.view.leagues:
            if league['id'] == selected_league_id:
                selected_league = league
                break
        
        if not selected_league:
            await interaction.response.send_message("Selected league not found.", ephemeral=True)
            return
        
        try:
            # Get league table
            table = await get_tournament_league_table(self.view.tournament['id'], selected_league['name'])
            
            if not table:
                await interaction.response.send_message(f"No data available for League {selected_league['name']}.", ephemeral=True)
                return
            
            # Create embed
            embed = create_league_table_embed(self.view.tournament, selected_league['name'], table)
            embed.title = f"League {selected_league['name']} Table - {self.view.tournament['name']}"
            
            # Send as public message (not ephemeral)
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            await interaction.response.send_message(f"Error displaying league table: {str(e)}", ephemeral=True)

class MatchEditSelectionView(View):
    """View for selecting a match to edit."""
    
    def __init__(self, tournament, matches):
        super().__init__(timeout=60)
        self.tournament = tournament
        self.matches = matches
        self.current_page = 0
        self.matches_per_page = 5
        
        # Add match selection dropdown
        self.update_match_options()
    
    def update_match_options(self):
        """Update the match selection options based on current page."""
        # Clear existing items except the first one
        while len(self.children) > 1:
            self.remove_item(self.children[-1])
        
        start_idx = self.current_page * self.matches_per_page
        end_idx = start_idx + self.matches_per_page
        page_matches = self.matches[start_idx:end_idx]
        
        options = []
        for match in page_matches:
            # Create a readable match description
            home_team = match.get('home_team_name', 'Unknown')
            away_team = match.get('away_team_name', 'Unknown')
            scoreline = match.get('scoreline', 'N/A')
            match_date = match.get('datetime', 'Unknown')
            
            if hasattr(match_date, 'strftime'):
                date_str = match_date.strftime("%Y-%m-%d")
            else:
                date_str = str(match_date)[:10]
            
            options.append(discord.SelectOption(
                label=f"{home_team} vs {away_team}",
                description=f"Score: {scoreline} | Date: {date_str}",
                value=match['match_id']
            ))
        
        # Add match selection dropdown
        self.add_item(MatchSelectEdit(options))
        
        # Add navigation buttons
        self.add_item(PreviousMatchButton())
        self.add_item(NextMatchButton())
    
    async def on_timeout(self):
        """Handle timeout."""
        pass

class MatchSelectEdit(Select):
    """Select component for choosing a match to edit."""
    
    def __init__(self, options):
        super().__init__(
            placeholder="Select a match to edit...",
            options=options,
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle match selection for editing."""
        selected_match_id = self.values[0]
        selected_match = None
        
        for match in self.view.matches:
            if match['match_id'] == selected_match_id:
                selected_match = match
                break
        
        if not selected_match:
            await interaction.response.send_message("Selected match not found.", ephemeral=True)
            return
        
        try:
            # Parse current score
            scoreline = selected_match.get('scoreline', '0-0')
            if '-' in scoreline:
                home_score, away_score = map(int, scoreline.split('-'))
            else:
                home_score, away_score = 0, 0
            
            # Create edit modal
            modal = EditMatchResultModal(
                tournament_id=self.view.tournament['id'],
                match_id=selected_match_id,
                current_home_score=home_score,
                current_away_score=away_score,
                current_notes=selected_match.get('notes', '')
            )
            
            await interaction.response.send_modal(modal)
            
        except Exception as e:
            await interaction.response.send_message(f"Error editing match: {str(e)}", ephemeral=True)

class PreviousMatchButton(Button):
    """Button to go to previous page of matches."""
    
    def __init__(self):
        super().__init__(label="◀️ Previous", style=discord.ButtonStyle.secondary)
    
    async def callback(self, interaction: discord.Interaction):
        """Handle previous page button."""
        if self.view.current_page > 0:
            self.view.current_page -= 1
            self.view.update_match_options()
            
            # Update the view
            await interaction.response.edit_message(view=self.view)
        else:
            await interaction.response.send_message("Already on the first page.", ephemeral=True)

class NextMatchButton(Button):
    """Button to go to next page of matches."""
    
    def __init__(self):
        super().__init__(label="Next ▶️", style=discord.ButtonStyle.secondary)
    
    async def callback(self, interaction: discord.Interaction):
        """Handle next page button."""
        matches_per_page = self.view.matches_per_page
        max_pages = (len(self.view.matches) - 1) // matches_per_page
        
        if self.view.current_page < max_pages:
            self.view.current_page += 1
            self.view.update_match_options()
            
            # Update the view
            await interaction.response.edit_message(view=self.view)
        else:
            await interaction.response.send_message("Already on the last page.", ephemeral=True)

@bot.slash_command(name="recalculate_stats", description="Manually recalculate tournament stats (Admin only).")
async def recalculate_stats_command(interaction: discord.Interaction, tournament_name: str):
    """Manually recalculate tournament stats from matches."""
    # Check admin permissions
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to recalculate stats.", ephemeral=True)
        return
    
    try:
        # Defer the response
        await interaction.response.defer(ephemeral=True)
        
        # Get tournament by name
        tournament = await get_tournament_by_name(tournament_name)
        if not tournament:
            await interaction.edit_original_response(content=f"Tournament '{tournament_name}' not found.")
            return
        
        # Recalculate tournament stats
        await recalculate_tournament_stats_v2(tournament['id'])
        
        embed = discord.Embed(
            title="Stats Recalculated",
            description=f"Tournament stats for **{tournament['name']}** have been recalculated from matches.",
            color=discord.Color.green()
        )
        
        await interaction.edit_original_response(embed=embed)
        
    except Exception as e:
        await interaction.edit_original_response(content=f"Error recalculating stats: {str(e)}")