from itertools import zip_longest
from ios_bot.config import *
from ios_bot.database_manager import get_player_by_steam_id, get_team_by_name
import numpy as np

MATCH_SUMMARIES_PATH = os.path.join(os.path.dirname(__file__), '..', 'ratings', 'match_summaries.csv')
PLAYER_STATS_PATH = os.path.join(os.path.dirname(__file__), '..', 'ratings', 'player_stats.csv')

def get_matches():
    """Reads and returns all matches from the CSV, sorted by most recent."""
    if not os.path.exists(MATCH_SUMMARIES_PATH):
        return []
    
    with open(MATCH_SUMMARIES_PATH, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        matches = list(reader)

    # Sort matches by datetime, descending
    matches.sort(key=lambda x: datetime.strptime(x['datetime'], '%Y-%m-%d %H:%M:%S'), reverse=True)
    return matches

def get_player_stats_for_match_id(match_id: str):
    """Reads player_stats.csv and returns all rows for a specific match_id."""
    if not os.path.exists(PLAYER_STATS_PATH):
        return []
    
    match_stats = []
    with open(PLAYER_STATS_PATH, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('match_id') == match_id:
                match_stats.append(row)
    return match_stats

def normalize_value(value, min_val, max_val):
    """Normalize a value between 0 and 1."""
    if max_val == min_val:
        return 1.0  # everyone tied
    return (value - min_val) / (max_val - min_val)

def get_mvp(player_stats):
    """
    Calculate MVP using a realistic, football-based scoring system.
    Base score starts at 5.5/10, with bonuses and penalties applied.
    10/10 ratings are extremely rare and reserved for legendary performances.
    """
    if not player_stats:
        return "No data available"

    # Position-specific impact weights (more conservative values)
    POSITION_WEIGHTS = {
        'GK': {
            # Positive contributions (reduced values)
            'keeperSaves': 0.08,           # Each save worth 0.08 points (reduced from 0.15)
            'keeperSavesCaught': 0.06,     # Caught saves worth slightly less
            'passesCompleted': 0.004,      # Building from back
            'assists': 0.30,               # Rare from GK, valuable but reduced
            'secondAssists': 0.15,         # Good distribution
            'keyPasses': 0.10,             # Quality distribution
            
            # Negative contributions (penalties)
            'goalsConceded': -0.30,        # Each goal conceded hurts more
            'ownGoals': -0.80,             # Own goals very costly
            'redCards': -1.50,             # Red card major penalty
            'yellowCards': -0.25,          # Yellow card minor penalty
            'fouls': -0.12,                # Fouls hurt GK rating
        },
        'DEF': {  # LB, CB, RB
            # Positive contributions (reduced values)
            'interceptions': 0.12,         # Key defensive stat
            'slidingTacklesCompleted': 0.15, # Successful tackles important
            'goals': 0.45,                 # Goals from defenders valuable but reduced
            'assists': 0.25,               # Set pieces, crosses
            'secondAssists': 0.12,         # Good buildup play
            'keyPasses': 0.10,             # Quality passing
            'passesCompleted': 0.006,      # Building from defense
            'keeperSaves': 0.10,           # If they had to go in goal
            
            # Negative contributions
            'goalsConceded': -0.20,        # Shared responsibility
            'ownGoals': -1.00,             # Very costly mistake
            'fouls': -0.18,                # Defensive fouls
            'yellowCards': -0.30,          # Cards hurt defenders more
            'redCards': -2.00,             # Red card devastating for defense
        },
        'MID': {  # CM
            # Positive contributions (reduced values)
            'assists': 0.35,               # Primary job of midfielders
            'secondAssists': 0.20,         # Key passes leading to assists
            'keyPasses': 0.12,             # Creating chances
            'goals': 0.40,                 # Goals from midfield valuable
            'passesCompleted': 0.008,      # Midfield engine
            'interceptions': 0.15,         # Winning ball back
            'slidingTacklesCompleted': 0.12, # Defensive contribution
            'shotsOnGoal': 0.10,           # Threat from distance
            'chancesCreated': 0.15,        # Creating opportunities
            
            # Negative contributions
            'fouls': -0.18,                # Disrupting play
            'yellowCards': -0.25,          # Discipline issues
            'redCards': -1.80,             # Losing midfield control
            'ownGoals': -0.90,             # Rare but costly
        },
        'FWD': {  # LW, CF, RW
            # Positive contributions (reduced values)
            'goals': 0.40,                 # Primary job of forwards
            'assists': 0.30,               # Creating for teammates
            'shotsOnGoal': 0.08,           # Testing keeper
            'keyPasses': 0.12,             # Final ball
            'secondAssists': 0.15,         # Buildup play
            'chancesCreated': 0.20,        # Creating opportunities
            'foulsSuffered': 0.05,         # Drawing fouls
            'passesCompleted': 0.005,      # Link-up play
            'interceptions': 0.08,         # Pressing from front
            
            # Negative contributions
            'fouls': -0.15,                # Unnecessary fouls
            'yellowCards': -0.30,          # Discipline
            'redCards': -1.70,             # Losing attacking threat
            'ownGoals': -0.95,             # Rare but devastating
            'offsides': -0.08,             # Poor positioning
        }
    }

    # Map positions to categories
    position_categories = {
        'GK': ['GK'],
        'DEF': ['LB', 'CB', 'RB'],
        'MID': ['CM'],
        'FWD': ['LW', 'CF', 'RW']
    }

    player_scores = []
    
    for player in player_stats:
        pos = player.get('Position', '').upper()
        pos_category = next((cat for cat, positions in position_categories.items() if pos in positions), None)
        
        if not pos_category:
            continue
            
        weights = POSITION_WEIGHTS[pos_category]
        
        # Start with lower base rating of 5.5/10 (slightly below average performance)
        base_score = 5.5
        bonus_score = 0.0
        
        # Track significant contributions for display
        key_stats = []
        
        # Calculate bonuses and penalties
        for stat, weight in weights.items():
            try:
                value = float(player.get(stat, 0))
                if value > 0:
                    contribution = value * weight
                    bonus_score += contribution
                    
                    # Track significant positive contributions (higher threshold)
                    if weight > 0 and value > 0 and contribution > 0.25:
                        key_stats.append(f"{stat}: {int(value)}")
                        
            except (ValueError, TypeError):
                continue
        
        # Calculate final score (base + bonus, with floor and ceiling)
        final_score = base_score + bonus_score
        
        # Apply realistic bounds (3.0 to 10.0 scale)
        final_score = max(3.0, min(10.0, final_score))
        
        # Special bonuses for exceptional performances (reduced bonuses)
        try:
            goals = float(player.get('goals', 0))
            assists = float(player.get('assists', 0))
            saves = float(player.get('keeperSaves', 0)) + float(player.get('keeperSavesCaught', 0))
            
            # Hat-trick bonus (reduced)
            if goals >= 3:
                final_score += 0.6  # Reduced from 0.8
                key_stats.append("Hat-trick!")
            
            # Double-double bonus (2+ goals and 2+ assists) (reduced)
            elif goals >= 2 and assists >= 2:
                final_score += 0.4  # Reduced from 0.6
                key_stats.append("Goals+Assists")
            
            # Exceptional GK performance (8+ saves with 0-1 goals conceded) (reduced)
            if pos_category == 'GK' and saves >= 8:
                goals_conceded = float(player.get('goalsConceded', 0))
                if goals_conceded <= 3:
                    final_score += 0.6  # Reduced from 0.7
                    key_stats.append("Outstanding saves")
                else:
                    final_score += 0.3  # Reduced from 0.3
                    key_stats.append("Good saves")
            
            # Clean sheet bonus for defenders and GKs (reduced)
            if pos_category in ['GK', 'DEF']:
                goals_conceded = float(player.get('goalsConceded', 0))
                if goals_conceded == 0:
                    final_score += 0.3  # Reduced from 0.4
                    key_stats.append("Clean sheet")
                    
        except (ValueError, TypeError):
            pass
        
        # Apply diminishing returns for high scores (makes 9+ much harder to achieve)
        if final_score > 8.5:
            # Exponential difficulty curve for scores above 8.0
            excess = final_score - 8.5
            # Apply severe diminishing returns: score above 8 becomes progressively harder
            diminished_excess = excess * (0.3 + 0.1 * np.exp(-excess * 2))
            final_score = 8.5 + diminished_excess
        
        # Ensure final score stays within bounds after all calculations
        final_score = max(3.0, min(10.0, final_score))
        
        player_scores.append({
            'name': player['Name'],
            'position': pos,
            'score': final_score,
            'stats': key_stats[:3]  # Show top 3 key stats
        })

    if not player_scores:
        return "No valid players found"

    # Sort by score and get MVP
    player_scores.sort(key=lambda x: x['score'], reverse=True)
    mvp = player_scores[0]
    
    # Enhanced MVP display with key contributions and more precise rating descriptions
    stats_display = " | ".join(mvp['stats']) if mvp['stats'] else "Solid performance"
    
    return f"`{mvp['name']}` (**{mvp['position']}**) : `{mvp['score']:.1f}/10` - {stats_display}"


def get_best_defender(player_stats):
    """Get the best defender based on interceptions and slide tackles."""
    if not player_stats:
        return "No data available"
        
    # Filter for defenders (LB, CB, RB)
    defenders = [p for p in player_stats if p['Position'] in ['LB', 'CB', 'RB']]
    if not defenders:
        return "No defenders found"
        
    # Calculate total defensive actions for each defender
    defender_stats = []
    for defender in defenders:
        interceptions = int(float(defender.get('interceptions', 0)))
        slide_tackles = int(float(defender.get('slidingTacklesCompleted', 0)))
        total = interceptions + slide_tackles
        defender_stats.append({
            'name': defender['Name'],
            'interceptions': interceptions,
            'slide_tackles': slide_tackles,
            'total': total
        })
    
    # Sort by total defensive actions
    defender_stats.sort(key=lambda x: x['total'], reverse=True)
    best_defender = defender_stats[0]

    return f"`{best_defender['name']}`  :|: **Interceptions:** `{best_defender['interceptions']}` **Successful Slide Tackles:** `{best_defender['slide_tackles']}`"

def get_best_goalkeeper(player_stats):
    """Finds the best GK if there are two, based on save ratio."""
    keepers = [p for p in player_stats if p.get('Position') == 'GK']
    if len(keepers) < 2:
        return None # Only show if two GKs played

    best_gk = None
    max_gk_score = -1

    for gk in keepers:
        try:
            saves = int(float(gk.get('keeperSaves', 0))) + int(float(gk.get('keeperSavesCaught', 0)))
            conceded = int(float(gk.get('goalsConceded', 0)))
            # Simple ratio, add 1 to conceded to avoid division by zero
            gk_score = saves / (conceded + 1)
            if gk_score > max_gk_score:
                max_gk_score = gk_score
                best_gk = gk.get('Name', 'N/A')
        except (ValueError, TypeError):
            continue

    return best_gk

def format_team_lineup(team_name, lineup_data, position_order, player_stats=None, substitution_summary=None, home_team_name=None, away_team_name=None):
    """Formats a single team's lineup with stats using the new lineup data structure."""
    lines = []
    
    # Create a mapping of steam_id to player stats for easy lookup
    steam_id_to_stats = {}
    if player_stats:
        for player in player_stats:
            if player['Team Name'] == team_name:
                steam_id_to_stats[player['Steam ID']] = player
    
    # Create a set of players who were subbed out (for the :Substitute: symbol)
    subbed_out_players = set()
    if substitution_summary and home_team_name and away_team_name:
        for sub in substitution_summary:
            team_side, (left_name, left_steamid), (join_name, join_steamid) = sub
            # Map team_side to actual team name for comparison
            if team_side == "home" and team_name == home_team_name:
                subbed_out_players.add(left_steamid)
            elif team_side == "away" and team_name == away_team_name:
                subbed_out_players.add(left_steamid)
    
    for pos, name, steamid in lineup_data:
        if name and steamid:
            # Get player stats if available
            stats = []
            if steamid in steam_id_to_stats:
                player = steam_id_to_stats[steamid]
                if int(float(player.get('goals', 0))) > 0:
                    stats.append(f"⚽x{int(float(player['goals']))}")
                if int(float(player.get('assists', 0))) > 0:
                    stats.append(f"👟x{int(float(player['assists']))}")
                if int(float(player.get('keeperSaves', 0))) > 0:
                    stats.append(f"🧤x{int(float(player['keeperSaves']))}")
                
                # Add card emojis (red overrides yellow, no counts)
                red_cards = int(float(player.get('redCards', 0)))
                yellow_cards = int(float(player.get('yellowCards', 0)))
                if red_cards > 0:
                    stats.append("🟥")
                elif yellow_cards > 0:
                    stats.append("🟨")
            
            stats_str = " ".join(stats)
            
            # Add <:Substitute:1388489365612662887> symbol if player was subbed out
            sub_symbol = " 🔄" if steamid in subbed_out_players else ""
            
            lines.append(f"{pos}: {name}{sub_symbol} {stats_str}")
        else:
            # Add red X for missing GK in single keeper games
            if pos == 'GK' and len(position_order) == 8:
                lines.append(f"{pos}: ❌")
            else:
                lines.append(f"{pos}: -")
    return "\n".join(lines)

def format_substitutions(substitution_summary, player_stats):
    """Formats the substitution summary with player stats."""
    if not substitution_summary:
        return "No substitutions"
    
    # Create a mapping of steam_id to player stats for easy lookup
    steam_id_to_stats = {}
    for player in player_stats:
        steam_id_to_stats[player['Steam ID']] = player
    
    sub_lines = []
    for i, sub in enumerate(substitution_summary):
        team_side, (left_name, left_steamid), (join_name, join_steamid) = sub
        
        # Get stats for both players
        left_stats = steam_id_to_stats.get(left_steamid, {})
        join_stats = steam_id_to_stats.get(join_steamid, {})
        
        # Format stats for left player
        left_stats_str = []
        if int(float(left_stats.get('goals', 0))) > 0:
            left_stats_str.append(f"⚽x{int(float(left_stats['goals']))}")
        if int(float(left_stats.get('assists', 0))) > 0:
            left_stats_str.append(f"👟x{int(float(left_stats['assists']))}")
        if int(float(left_stats.get('keeperSaves', 0))) > 0:
            left_stats_str.append(f"🧤x{int(float(left_stats['keeperSaves']))}")
        
        # Add card emojis for left player (red overrides yellow, no counts)
        left_red_cards = int(float(left_stats.get('redCards', 0)))
        left_yellow_cards = int(float(left_stats.get('yellowCards', 0)))
        if left_red_cards > 0:
            left_stats_str.append("🟥")
        elif left_yellow_cards > 0:
            left_stats_str.append("🟨")
        
        # Format stats for joining player
        join_stats_str = []
        if int(float(join_stats.get('goals', 0))) > 0:
            join_stats_str.append(f"⚽x{int(float(join_stats['goals']))}")
        if int(float(join_stats.get('assists', 0))) > 0:
            join_stats_str.append(f"👟x{int(float(join_stats['assists']))}")
        if int(float(join_stats.get('keeperSaves', 0))) > 0:
            join_stats_str.append(f"🧤x{int(float(join_stats['keeperSaves']))}")
        
        # Add card emojis for joining player (red overrides yellow, no counts)
        join_red_cards = int(float(join_stats.get('redCards', 0)))
        join_yellow_cards = int(float(join_stats.get('yellowCards', 0)))
        if join_red_cards > 0:
            join_stats_str.append("🟥")
        elif join_yellow_cards > 0:
            join_stats_str.append("🟨")
        
        left_stats_display = " ".join(left_stats_str) if left_stats_str else ""
        join_stats_display = " ".join(join_stats_str) if join_stats_str else ""
        
        team_display = "Home" if team_side == "home" else "Away"
        sub_lines.append(f"({i+1}) {team_display}: {left_name} 🔄 {join_name}")
        if left_stats_display or join_stats_display:
            sub_lines.append(f"    {left_name}: {left_stats_display} | {join_name}: {join_stats_display}")
    
    return "\n".join(sub_lines)

async def get_player_mention(performer_str: str):
    """
    Parses performer string, finds player in DB, and returns a mention or name with score.
    Returns the formatted string and a boolean indicating if a score was found.
    """
    if performer_str == 'N/A':
        return 'N/A', True # N/A is considered a valid state, so score is "found"

    parts = performer_str.split(' : ')
    name = parts[0]
    steam_id = parts[1] if len(parts) > 1 else None
    score = parts[2] if len(parts) > 2 else None
    score_found = score is not None

    # Format the score part of the display
    score_display = ""
    if score_found:
        try:
            score_num = float(score)
            score_display = f" {{{int(score_num) if score_num.is_integer() else round(score_num, 2)}}}"
        except ValueError:
            score_display = "" # Don't show score if it's not a number

    # Attempt to find the player in the database
    if steam_id:
        player_record = await get_player_by_steam_id(steam_id)
        if player_record and player_record.get('discord_id'):
            mention = f"<@{player_record['discord_id']}>"
            return f"{mention} ({name}{score_display})", score_found

    # Fallback if no player record is found or no steam_id is present
    return f"{name}{score_display}", score_found

class MatchSelect(Select):
    def __init__(self, matches_on_page):
        options = []
        for match in matches_on_page:
            home_team = match['home_team']
            away_team = match['away_team']
            score = match['scoreline'].replace('-', ' - ')
            date = datetime.strptime(match['datetime'], '%Y-%m-%d %H:%M:%S').strftime('%b %d, %Y')
            
            options.append(discord.SelectOption(
                label=f"{home_team} vs {away_team} ({score})",
                description=f"Played on {date}",
                value=match['match_id'] # Use unique match_id as identifier
            ))
        super().__init__(placeholder="Select a match to view details...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_match_id = self.values[0]
        
        all_matches = get_matches()
        match_data = next((m for m in all_matches if m['match_id'] == selected_match_id), None)

        if not match_data:
            await interaction.followup.send("Could not find the selected match. Please try again.", ephemeral=True)
            return
            
        home_team_name = match_data['home_team']
        away_team_name = match_data['away_team']
        scoreline = match_data['scoreline'].replace('-', ' - ')
        game_type = match_data.get('game_type', '6v6')

        # Get all player stats for this specific match
        match_player_stats = get_player_stats_for_match_id(selected_match_id)

        # Parse lineup data from JSON strings
        initial_lineups = json.loads(match_data.get('initial_lineups', '{}'))
        final_lineups = json.loads(match_data.get('final_lineups', '{}'))
        substitution_summary = json.loads(match_data.get('substitution_summary', '[]'))

        # Build main match summary embed
        embed = discord.Embed(
            title=f"`{home_team_name}`  **{scoreline}**  `{away_team_name}`",
            color=discord.Color.dark_orange()
        )

        # Set team logos
        home_team_info = await get_team_by_name(home_team_name)
        away_team_info = await get_team_by_name(away_team_name)
        main_guild = bot.get_guild(MAIN_GUILD_ID)
        main_guild_icon = main_guild.icon.url if main_guild and main_guild.icon else None

        home_icon_url = home_team_info.get('guild_icon') if home_team_info else main_guild_icon
        away_icon_url = away_team_info.get('guild_icon') if away_team_info else main_guild_icon
        
        score_nums = scoreline.split(" - ")
        if score_nums[0] > score_nums[1]:
            embed.set_thumbnail(url=home_icon_url)
        elif score_nums[1] > score_nums[0]:
            embed.set_thumbnail(url=away_icon_url)
        else:
            embed.set_thumbnail(url=home_icon_url)

        # Add MVP
        mvp_name = get_mvp(match_player_stats)
        embed.add_field(name="🏆 MVP", value=mvp_name, inline=False)
        
        # Add lineups using the new lineup data
        if initial_lineups and final_lineups:
            # Define position order based on game type
            position_order = ['GK', 'LB', 'RB', 'CM', 'LW', 'RW'] if game_type == '6v6' else ['GK', 'LB', 'CB', 'RB', 'CM', 'LW', 'CF', 'RW']
            
            # Get lineup data for each team
            home_initial = initial_lineups.get('home', [])
            away_initial = initial_lineups.get('away', [])
            
            # Format each team's initial lineup
            home_lineup = format_team_lineup(home_team_name, home_initial, position_order, match_player_stats, substitution_summary, home_team_name, away_team_name)
            away_lineup = format_team_lineup(away_team_name, away_initial, position_order, match_player_stats, substitution_summary, home_team_name, away_team_name)
            
            # Add lineups as separate fields
            embed.add_field(name=f"{home_team_name}'s Lineup", value=f"```{home_lineup}```", inline=True)
            embed.add_field(name=f"{away_team_name}'s Lineup", value=f"```{away_lineup}```", inline=True)
            
            # Add substitutions field if there were any
            if substitution_summary:
                subs_text = format_substitutions(substitution_summary, match_player_stats)
                embed.add_field(name="🔄 SUBS", value=f"```{subs_text}```", inline=False)
        else:
            embed.add_field(name="Players", value="Detailed lineup data not available for this match.", inline=False)

        # Add other awards
        best_defender_name = get_best_defender(match_player_stats)
        embed.add_field(name="🛡️ Best Defender", value=best_defender_name, inline=False)

        best_gk_name = get_best_goalkeeper(match_player_stats)
        if best_gk_name:
            embed.add_field(name="🧤 Best Goalkeeper", value=best_gk_name, inline=False)
        
        # Set footer and author
        embed.set_author(name=f"{interaction.user.name}", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
        embed.set_footer(text=f"Requested by {interaction.user.name}")
        
        await interaction.edit_original_response(embed=embed)


class MatchHistoryView(View):
    def __init__(self, interaction, all_matches):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.all_matches = all_matches
        self.current_page = 0
        self.matches_per_page = 25
        self.total_pages = (len(self.all_matches) - 1) // self.matches_per_page + 1
        
        # Create buttons
        self.prev_page_button = Button(label="Previous", style=discord.ButtonStyle.grey)
        self.next_page_button = Button(label="Next", style=discord.ButtonStyle.grey)

        # Assign callbacks
        self.prev_page_button.callback = self.prev_page_callback
        self.next_page_button.callback = self.next_page_callback

        self.update_view()

    def update_view(self):
        """Clears and adds items for the current page."""
        self.clear_items()
        start_index = self.current_page * self.matches_per_page
        end_index = start_index + self.matches_per_page
        matches_on_page = self.all_matches[start_index:end_index]
        
        self.add_item(MatchSelect(matches_on_page))
        self.add_item(self.prev_page_button)
        self.add_item(self.next_page_button)
        self.update_button_states()

    def update_button_states(self):
        """Disables/enables previous/next buttons based on the current page."""
        self.prev_page_button.disabled = self.current_page == 0
        self.next_page_button.disabled = self.current_page >= self.total_pages - 1
        self.prev_page_button.label = f"Page {self.current_page + 1}/{self.total_pages}"

    async def prev_page_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.current_page -= 1
        self.update_view()
        await self.interaction.edit(view=self)

    async def next_page_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.current_page += 1
        self.update_view()
        await self.interaction.edit(view=self)


@bot.slash_command(name="view_match", description="View past match summaries.")
async def view_match(interaction: discord.Interaction):
    """Displays a paginated and selectable list of past matches."""
    all_matches = get_matches()
    if not all_matches:
        await interaction.response.send_message("No match data is available at the moment.", ephemeral=True)
        return
        
    view = MatchHistoryView(interaction, all_matches)
    await interaction.response.send_message("Please select a match to view its summary.", view=view) 