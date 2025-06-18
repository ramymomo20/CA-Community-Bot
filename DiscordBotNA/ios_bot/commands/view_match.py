from itertools import zip_longest
from ios_bot.config import *
from ios_bot.database_manager import get_player_by_steam_id, get_team_by_name

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
    Calculate MVP using a more realistic, position-aware, impact-based scoring system.
    """
    if not player_stats:
        return "No data available"

    # Define stat weights for each position
    WEIGHTS = {
        'GK': {
            'keeperSaves': 0.25,
            'keeperSavesCaught': 0.10,
            'cleanSheets': 0.25,
            'passesCompleted': 0.10,
            'assists': 0.10,
            'secondAssists': 0.05,
            'goalsConceded': -0.40,  # negative weight
        },
        'DEF': {  # LB, CB, RB
            'interceptions': 0.25,
            'slidingTacklesCompleted': 0.25,
            'passesCompleted': 0.15,
            'assists': 0.15,
            'secondAssists': 0.10,
            'goals': 0.15,  # rare, so more valuable
            'keyPasses': 0.05
        },
        'MID': {  # CM
            'passesCompleted': 0.25,
            'keyPasses': 0.30,
            'assists': 0.25,
            'goals': 0.25,
            'secondAssists': 0.15,
            'interceptions': 0.15,
            'slidingTacklesCompleted': 0.10,
            'shotsOnGoal': 0.05,
        },
        'FWD': {  # LW, CF, RW
            'goals': 0.35,
            'assists': 0.25,
            'shotsOnGoal': 0.10,
            'keyPasses': 0.15,
            'secondAssists': 0.15,
            'passesCompleted': 0.05,
            'interceptions': 0.05,
        }
    }

    # Map positions to categories
    position_categories = {
        'GK': ['GK'],
        'DEF': ['LB', 'CB', 'RB'],
        'MID': ['CM'],
        'FWD': ['LW', 'CF', 'RW']
    }

    # Precompute min/max for each stat across all players
    stat_minmax = {}
    for cat, weights in WEIGHTS.items():
        for stat in weights:
            values = []
            for p in player_stats:
                try:
                    v = float(p.get(stat, 0))
                    values.append(v)
                except Exception:
                    continue
            if values:
                stat_minmax[stat] = (min(values), max(values))
            else:
                stat_minmax[stat] = (0, 0)

    def normalize(value, stat):
        min_val, max_val = stat_minmax.get(stat, (0, 0))
        if max_val == min_val:
            return 1.0 if value > 0 else 0.0
        return (value - min_val) / (max_val - min_val)

    player_scores = []
    for player in player_stats:
        pos = player.get('Position')
        pos_category = next((cat for cat, positions in position_categories.items() if pos in positions), None)
        if not pos_category:
            continue
        weights = WEIGHTS[pos_category]
        score = 0
        stats_display = []
        for stat, weight in weights.items():
            try:
                value = float(player.get(stat, 0))
                norm = normalize(value, stat)
                # For negative weights (e.g., goalsConceded), invert normalization
                if weight < 0:
                    norm = 1 - norm
                score += abs(weight) * norm * (1 if weight > 0 else -1)
                if value > 0 and stat not in ['goalsConceded']:
                    stats_display.append(f"{stat}: {int(value)}")
            except Exception:
                continue
        player_scores.append({
            'name': player['Name'],
            'position': pos,
            'score': score,
            'stats': stats_display
        })

    if not player_scores:
        return "No valid players found"

    
    # Sort by score and get MVP
    player_scores.sort(key=lambda x: x['score'], reverse=True)
    mvp = player_scores[0]
    
    # Format MVP display with their key stats
    return f"`{mvp['name']}` (**{mvp['position']}**) : `{mvp['score'] * 100:.2f} / 100`"


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

def format_team_lineup(team_name, players, position_order):
    """Formats a single team's lineup with stats."""
    lines = []
    for pos in position_order:
        if pos in players:
            p = players[pos]
            stats = []
            if int(float(p.get('goals', 0))) > 0:
                stats.append(f"⚽x{int(float(p['goals']))}")
            if int(float(p.get('assists', 0))) > 0:
                stats.append(f"👟x{int(float(p['assists']))}")
            if int(float(p.get('keeperSaves', 0))) > 0:
                stats.append(f"🧤x{int(float(p['keeperSaves']))}")
            stats_str = " ".join(stats)
            lines.append(f"{pos} {p['Name']} {stats_str}")
        else:
            # Add red X for missing GK in single keeper games
            if pos == 'GK' and len(position_order) == 6:  # 6v6 game
                lines.append(f"{pos} ❌")
            else:
                lines.append(f"{pos} -")
    return "\n".join(lines)

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

        # Add MVP and other awards
        mvp_name = get_mvp(match_player_stats)
        embed.add_field(name="🏆 MVP", value=mvp_name, inline=False)
        
        # Add lineups in separate fields
        if match_player_stats:
            # Define position order based on game type
            position_order = ['GK', 'LB', 'RB', 'CM', 'LW', 'RW'] if game_type == '6v6' else ['GK', 'LB', 'CB', 'RB', 'CM', 'LW', 'CF', 'RW']
            
            # Get players by position for each team
            home_players = {p['Position']: p for p in match_player_stats if p['Team Name'] == home_team_name}
            away_players = {p['Position']: p for p in match_player_stats if p['Team Name'] == away_team_name}
            
            # Format each team's lineup
            home_lineup = format_team_lineup(home_team_name, home_players, position_order)
            away_lineup = format_team_lineup(away_team_name, away_players, position_order)
            
            # Add lineups as separate fields
            embed.add_field(name=f"{home_team_name}'s Lineup", value=f"```{home_lineup}```", inline=True)
            embed.add_field(name=f"{away_team_name}'s Lineup", value=f"```{away_lineup}```", inline=True)
        else:
            embed.add_field(name="Players", value="Detailed player stats not available for this match.", inline=False)

        # add other people
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