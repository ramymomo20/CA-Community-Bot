-- ============================================
-- FINAL MERGED DATABASE SCHEMA FOR IOSCA BOT
-- PostgreSQL / Supabase
-- Combines best features from both schemas
-- ============================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 1. PLAYERS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS IOSCA_PLAYERS (
    steam_id VARCHAR(255) PRIMARY KEY,
    discord_id BIGINT UNIQUE NOT NULL,
    discord_name VARCHAR(255) NOT NULL,
    rating DECIMAL(4,2) DEFAULT 5.0,
    rating_updated_at TIMESTAMP DEFAULT NOW(),
    registered_at TIMESTAMP DEFAULT NOW(),
    last_active TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for players
CREATE INDEX IF NOT EXISTS idx_players_discord_id ON IOSCA_PLAYERS(discord_id);
CREATE INDEX IF NOT EXISTS idx_players_rating ON IOSCA_PLAYERS(rating DESC);
CREATE INDEX IF NOT EXISTS idx_players_name ON IOSCA_PLAYERS(discord_name);

-- Comments
COMMENT ON TABLE IOSCA_PLAYERS IS 'Registered players with their Steam and Discord IDs';
COMMENT ON COLUMN IOSCA_PLAYERS.rating IS 'Player rating (0-10 scale) calculated from match performance';

-- ============================================
-- 2. TEAMS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS IOSCA_TEAMS (
    guild_id BIGINT PRIMARY KEY,
    guild_name VARCHAR(255) NOT NULL,
    guild_icon TEXT,
    captain_id BIGINT NOT NULL,
    captain_name VARCHAR(255) NOT NULL,
    players JSONB DEFAULT '[]'::jsonb,
    eights_channels JSONB DEFAULT '[]'::jsonb,
    sixes_channels JSONB DEFAULT '[]'::jsonb,
    fives_channels JSONB DEFAULT '[]'::jsonb,
    press_channel_id BIGINT,
    is_national_team BOOLEAN DEFAULT FALSE,
    is_mix_team BOOLEAN DEFAULT FALSE,
    average_rating DECIMAL(4,2) DEFAULT 5.0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for teams
CREATE INDEX IF NOT EXISTS idx_teams_captain ON IOSCA_TEAMS(captain_id);
CREATE INDEX IF NOT EXISTS idx_teams_name ON IOSCA_TEAMS(guild_name);
CREATE INDEX IF NOT EXISTS idx_teams_national ON IOSCA_TEAMS(is_national_team) WHERE is_national_team = TRUE;
CREATE INDEX IF NOT EXISTS idx_teams_mix ON IOSCA_TEAMS(is_mix_team) WHERE is_mix_team = TRUE;
CREATE INDEX IF NOT EXISTS idx_teams_players ON IOSCA_TEAMS USING GIN(players);
CREATE INDEX IF NOT EXISTS idx_teams_press_channel ON IOSCA_TEAMS(press_channel_id);

-- Comments
COMMENT ON TABLE IOSCA_TEAMS IS 'Registered teams (Discord servers)';
COMMENT ON COLUMN IOSCA_TEAMS.players IS 'Array of player objects: [{"id": discord_id, "name": "..."}]';
COMMENT ON COLUMN IOSCA_TEAMS.eights_channels IS 'Array of channel IDs for 8v8 matchmaking';
COMMENT ON COLUMN IOSCA_TEAMS.sixes_channels IS 'Array of channel IDs for 6v6 matchmaking';
COMMENT ON COLUMN IOSCA_TEAMS.fives_channels IS 'Array of channel IDs for 5v5 matchmaking';
COMMENT ON COLUMN IOSCA_TEAMS.press_channel_id IS 'Optional press channel used for scheduling messages';

-- ============================================
-- 2B. TEAM LINEUPS (SNAPSHOTS)
-- ============================================

CREATE TABLE IF NOT EXISTS TEAM_LINEUPS (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    context_type VARCHAR(32),
    lineup JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (guild_id, channel_id)
);

CREATE INDEX IF NOT EXISTS idx_team_lineups_guild ON TEAM_LINEUPS (guild_id);
CREATE INDEX IF NOT EXISTS idx_team_lineups_channel ON TEAM_LINEUPS (channel_id);

-- Track skipped match imports so they aren't reprocessed
CREATE TABLE IF NOT EXISTS MATCH_IMPORT_SKIPS (
    match_id TEXT PRIMARY KEY,
    filename TEXT,
    reason TEXT,
    match_datetime TIMESTAMP,
    skipped_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_match_import_skips_datetime ON MATCH_IMPORT_SKIPS (match_datetime);

-- ============================================
-- 3. MATCH STATS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS MATCH_STATS (
    id SERIAL PRIMARY KEY,
    match_id VARCHAR(255) UNIQUE NOT NULL,
    datetime TIMESTAMP NOT NULL,
    home_guild_id BIGINT,
    away_guild_id BIGINT,
    home_team_name VARCHAR(255) NOT NULL,
    away_team_name VARCHAR(255) NOT NULL,
    home_score INTEGER NOT NULL DEFAULT 0,
    away_score INTEGER NOT NULL DEFAULT 0,
    game_type VARCHAR(10) NOT NULL CHECK (game_type IN ('5v5', '6v6', '8v8')),
    home_lineup JSONB DEFAULT '[]'::jsonb,
    away_lineup JSONB DEFAULT '[]'::jsonb,
    extratime BOOLEAN DEFAULT FALSE,
    penalties BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (home_guild_id) REFERENCES IOSCA_TEAMS(guild_id) ON DELETE SET NULL,
    FOREIGN KEY (away_guild_id) REFERENCES IOSCA_TEAMS(guild_id) ON DELETE SET NULL
);

-- Indexes for match stats
CREATE INDEX IF NOT EXISTS idx_match_stats_datetime ON MATCH_STATS(datetime DESC);
CREATE INDEX IF NOT EXISTS idx_match_stats_home_team ON MATCH_STATS(home_guild_id);
CREATE INDEX IF NOT EXISTS idx_match_stats_away_team ON MATCH_STATS(away_guild_id);
CREATE INDEX IF NOT EXISTS idx_match_stats_home_team_name ON MATCH_STATS(home_team_name);
CREATE INDEX IF NOT EXISTS idx_match_stats_away_team_name ON MATCH_STATS(away_team_name);
CREATE INDEX IF NOT EXISTS idx_match_stats_match_id ON MATCH_STATS(match_id);
CREATE INDEX IF NOT EXISTS idx_match_stats_game_type ON MATCH_STATS(game_type);

-- Comments
COMMENT ON TABLE MATCH_STATS IS 'Match results and metadata';
COMMENT ON COLUMN MATCH_STATS.match_id IS 'Unique match identifier from game server';
COMMENT ON COLUMN MATCH_STATS.home_team_name IS 'Home team name from match JSON (can be linked to IOSCA_TEAMS when team registers)';
COMMENT ON COLUMN MATCH_STATS.away_team_name IS 'Away team name from match JSON (can be linked to IOSCA_TEAMS when team registers)';
COMMENT ON COLUMN MATCH_STATS.substitutions IS 'Array of substitution events detected from match timeline: [{"time": seconds, "team": "home|away", "player_out": {...}, "player_in": {...}}]';

-- ============================================
-- 4. PLAYER MATCH DATA TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS PLAYER_MATCH_DATA (
    id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL,
    steam_id VARCHAR(255) NOT NULL,
    guild_id BIGINT,
    position VARCHAR(10),
    goals INTEGER DEFAULT 0,
    assists INTEGER DEFAULT 0,
    second_assists INTEGER DEFAULT 0,
    shots INTEGER DEFAULT 0,
    shots_on_goal INTEGER DEFAULT 0,
    passes_completed INTEGER DEFAULT 0,
    passes_attempted INTEGER DEFAULT 0,
    chances_created INTEGER DEFAULT 0,
    key_passes INTEGER DEFAULT 0,
    interceptions INTEGER DEFAULT 0,
    tackles INTEGER DEFAULT 0,
    sliding_tackles_completed INTEGER DEFAULT 0,
    fouls INTEGER DEFAULT 0,
    yellow_cards INTEGER DEFAULT 0,
    red_cards INTEGER DEFAULT 0,
    keeper_saves INTEGER DEFAULT 0,
    keeper_saves_caught INTEGER DEFAULT 0,
    goals_conceded INTEGER DEFAULT 0,
    offsides INTEGER DEFAULT 0,
    own_goals INTEGER DEFAULT 0,
    fouls_suffered INTEGER DEFAULT 0,
    free_kicks INTEGER DEFAULT 0,
    penalties INTEGER DEFAULT 0,
    corners INTEGER DEFAULT 0,
    throw_ins INTEGER DEFAULT 0,
    goal_kicks INTEGER DEFAULT 0,
    possession DECIMAL(5,2) DEFAULT 0,
    time_played INTEGER DEFAULT 0,
    time_gk INTEGER DEFAULT 0,
    time_def INTEGER DEFAULT 0,
    time_mid INTEGER DEFAULT 0,
    time_att INTEGER DEFAULT 0,
    distance_covered DECIMAL(10,2) DEFAULT 0,
    pass_accuracy DECIMAL(5,2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (match_id) REFERENCES MATCH_STATS(id) ON DELETE CASCADE,
    FOREIGN KEY (guild_id) REFERENCES IOSCA_TEAMS(guild_id) ON DELETE SET NULL
);

-- Indexes for player match data
CREATE INDEX IF NOT EXISTS idx_player_match_data_match ON PLAYER_MATCH_DATA(match_id);
CREATE INDEX IF NOT EXISTS idx_player_match_data_player ON PLAYER_MATCH_DATA(steam_id);
CREATE INDEX IF NOT EXISTS idx_player_match_data_team ON PLAYER_MATCH_DATA(guild_id);
CREATE INDEX IF NOT EXISTS idx_player_match_data_position ON PLAYER_MATCH_DATA(position);
CREATE INDEX IF NOT EXISTS idx_player_match_data_goals ON PLAYER_MATCH_DATA(goals DESC);

-- Comments
COMMENT ON TABLE PLAYER_MATCH_DATA IS 'Individual player statistics for each match';

-- ============================================
-- 7. SERVERS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS IOS_SERVERS (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    address VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL,
    sftp_ip VARCHAR(255),
    host_username VARCHAR(255),
    host_password VARCHAR(255),
    server_type VARCHAR(50) DEFAULT 'linux',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for servers
CREATE INDEX IF NOT EXISTS idx_servers_active ON IOS_SERVERS(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_servers_name ON IOS_SERVERS(name);

-- Comments
COMMENT ON TABLE IOS_SERVERS IS 'Game servers for SFTP match data retrieval';
COMMENT ON COLUMN IOS_SERVERS.address IS 'Server address/IP';
COMMENT ON COLUMN IOS_SERVERS.password IS 'Server password';
COMMENT ON COLUMN IOS_SERVERS.sftp_ip IS 'SFTP server IP address';
COMMENT ON COLUMN IOS_SERVERS.host_username IS 'SFTP username';
COMMENT ON COLUMN IOS_SERVERS.host_password IS 'SFTP password (encrypted in production)';

-- ============================================
-- 8. MAIN DISCORD TABLE (Optional)
-- ============================================

CREATE TABLE IF NOT EXISTS MAIN_DISCORD (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL UNIQUE,
    guild_name VARCHAR(255) NOT NULL,
    fives_channels JSONB DEFAULT '[]'::jsonb,
    sixes_channels JSONB DEFAULT '[]'::jsonb,
    eights_channels JSONB DEFAULT '[]'::jsonb,
    results_channel BIGINT,
    fixtures_channel JSONB DEFAULT '[]'::jsonb,
    confirmed_channel JSONB DEFAULT '[]'::jsonb,
    captains_channel JSONB DEFAULT '[]'::jsonb,
    admin_role_id BIGINT,
    team_leader_role_id BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Comments
COMMENT ON TABLE MAIN_DISCORD IS 'Main Discord guild configuration';

-- ============================================
-- 9. ACTIVE CHALLENGES TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS ACTIVE_CHALLENGES (
    challenge_id VARCHAR(36) PRIMARY KEY,
    challenger_guild_id BIGINT NOT NULL,
    challenged_guild_id BIGINT,
    channel_type VARCHAR(10) NOT NULL CHECK (channel_type IN ('6v6', '8v8')),
    status VARCHAR(20) NOT NULL CHECK (status IN ('searching', 'accepted', 'in_progress', 'completed')),
    challenger_lineup JSONB NOT NULL,
    challenged_lineup JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL
);

-- Indexes for active challenges
CREATE INDEX IF NOT EXISTS idx_active_challenges_challenger ON ACTIVE_CHALLENGES(challenger_guild_id);
CREATE INDEX IF NOT EXISTS idx_active_challenges_challenged ON ACTIVE_CHALLENGES(challenged_guild_id);
CREATE INDEX IF NOT EXISTS idx_active_challenges_status ON ACTIVE_CHALLENGES(status);
CREATE INDEX IF NOT EXISTS idx_active_challenges_expires ON ACTIVE_CHALLENGES(expires_at);

-- Comments
COMMENT ON TABLE ACTIVE_CHALLENGES IS 'Active match challenges and their lineups';

-- ============================================
-- 10. FUNCTIONS AND TRIGGERS
-- ============================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for IOSCA_PLAYERS
DROP TRIGGER IF EXISTS update_iosca_players_updated_at ON IOSCA_PLAYERS;
CREATE TRIGGER update_iosca_players_updated_at
    BEFORE UPDATE ON IOSCA_PLAYERS
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for IOSCA_TEAMS
DROP TRIGGER IF EXISTS update_teams_updated_at ON IOSCA_TEAMS;
CREATE TRIGGER update_teams_updated_at
    BEFORE UPDATE ON IOSCA_TEAMS
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for MATCH_STATS
DROP TRIGGER IF EXISTS update_match_stats_updated_at ON MATCH_STATS;
CREATE TRIGGER update_match_stats_updated_at
    BEFORE UPDATE ON MATCH_STATS
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for PLAYER_MATCH_DATA
DROP TRIGGER IF EXISTS update_player_match_data_updated_at ON PLAYER_MATCH_DATA;
CREATE TRIGGER update_player_match_data_updated_at
    BEFORE UPDATE ON PLAYER_MATCH_DATA
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for SERVERS
DROP TRIGGER IF EXISTS update_servers_updated_at ON IOS_SERVERS;
CREATE TRIGGER update_servers_updated_at
    BEFORE UPDATE ON IOS_SERVERS
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger for MAIN_DISCORD
DROP TRIGGER IF EXISTS update_main_discord_updated_at ON MAIN_DISCORD;
CREATE TRIGGER update_main_discord_updated_at
    BEFORE UPDATE ON MAIN_DISCORD
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 11. VIEWS FOR COMMON QUERIES
-- ============================================

-- View: Player statistics summary (comprehensive)
CREATE OR REPLACE VIEW player_stats_summary AS
SELECT 
    p.steam_id,
    p.discord_id,
    p.discord_name,
    p.rating,
    
    -- Match counts
    COUNT(DISTINCT pmd.match_id) as total_matches,
    
    -- Offensive stats
    SUM(pmd.goals) as total_goals,
    SUM(pmd.assists) as total_assists,
    SUM(pmd.second_assists) as total_second_assists,
    SUM(pmd.shots) as total_shots,
    SUM(pmd.shots_on_goal) as total_shots_on_goal,
    SUM(pmd.key_passes) as total_key_passes,
    SUM(pmd.chances_created) as total_chances_created,
    SUM(pmd.offsides) as total_offsides,
    
    -- Passing stats
    SUM(pmd.passes_attempted) as total_passes_attempted,
    SUM(pmd.passes_completed) as total_passes_completed,
    CASE 
        WHEN SUM(pmd.passes_attempted) > 0 
        THEN ROUND((SUM(pmd.passes_completed)::DECIMAL / SUM(pmd.passes_attempted) * 100), 2)
        ELSE 0 
    END as overall_pass_completion_pct,
    AVG(pmd.pass_accuracy) as avg_pass_accuracy_per_match,
    
    -- Defensive stats
    SUM(pmd.tackles) as total_tackles,
    SUM(pmd.sliding_tackles_completed) as total_sliding_tackles_completed,
    SUM(pmd.interceptions) as total_interceptions,
    
    -- Goalkeeper stats
    SUM(pmd.keeper_saves) as total_keeper_saves,
    SUM(pmd.keeper_saves_caught) as total_keeper_saves_caught,
    SUM(pmd.goals_conceded) as total_goals_conceded,
    
    -- Discipline
    SUM(pmd.fouls) as total_fouls,
    SUM(pmd.yellow_cards) as total_yellow_cards,
    SUM(pmd.red_cards) as total_red_cards,
    SUM(pmd.own_goals) as total_own_goals,
    
    -- Time played (in seconds)
    SUM(pmd.time_played) as total_time_played,
    SUM(pmd.time_gk) as total_time_gk,
    SUM(pmd.time_def) as total_time_def,
    SUM(pmd.time_mid) as total_time_mid,
    SUM(pmd.time_att) as total_time_att,
    
    -- Physical
    SUM(pmd.distance_covered) as total_distance_covered,
    AVG(pmd.distance_covered) as avg_distance_per_match,
    
    -- Averages per match
    ROUND(AVG(pmd.goals), 2) as avg_goals_per_match,
    ROUND(AVG(pmd.assists), 2) as avg_assists_per_match,
    ROUND(AVG(pmd.shots), 2) as avg_shots_per_match,
    ROUND(AVG(pmd.tackles), 2) as avg_tackles_per_match,
    ROUND(AVG(pmd.passes_completed), 2) as avg_passes_completed_per_match,
    ROUND(AVG(pmd.keeper_saves), 2) as avg_saves_per_match

-- View: Team statistics summary
CREATE OR REPLACE VIEW team_stats_summary AS
SELECT 
    t.guild_id,
    t.guild_name,
    t.captain_name,
    t.average_rating,
    COUNT(DISTINCT CASE WHEN ms.home_guild_id = t.guild_id THEN ms.id END) as home_matches,
    COUNT(DISTINCT CASE WHEN ms.away_guild_id = t.guild_id THEN ms.id END) as away_matches,
    COUNT(DISTINCT ms.id) as total_matches,
    SUM(CASE WHEN ms.home_guild_id = t.guild_id THEN ms.home_score ELSE ms.away_score END) as goals_scored,
    SUM(CASE WHEN ms.home_guild_id = t.guild_id THEN ms.away_score ELSE ms.home_score END) as goals_conceded,
    SUM(CASE 
        WHEN (ms.home_guild_id = t.guild_id AND ms.home_score > ms.away_score) OR 
             (ms.away_guild_id = t.guild_id AND ms.away_score > ms.home_score) 
        THEN 1 ELSE 0 
    END) as wins,
    SUM(CASE 
        WHEN ms.home_score = ms.away_score 
        THEN 1 ELSE 0 
    END) as draws,
    SUM(CASE 
        WHEN (ms.home_guild_id = t.guild_id AND ms.home_score < ms.away_score) OR 
             (ms.away_guild_id = t.guild_id AND ms.away_score < ms.home_score) 
        THEN 1 ELSE 0 
    END) as losses
FROM IOSCA_TEAMS t
LEFT JOIN MATCH_STATS ms ON t.guild_id = ms.home_guild_id OR t.guild_id = ms.away_guild_id
GROUP BY t.guild_id, t.guild_name, t.captain_name, t.average_rating;

-- View: Recent matches
CREATE OR REPLACE VIEW recent_matches AS
SELECT 
    ms.id,
    ms.match_id,
    ms.datetime,
    ms.home_team_name,
    ms.away_team_name,
    ht.guild_name as home_team_guild,
    at.guild_name as away_team_guild,
    ms.home_score,
    ms.away_score,
    ms.game_type,
    CASE 
        WHEN ms.home_score > ms.away_score THEN COALESCE(ht.guild_name, ms.home_team_name)
        WHEN ms.away_score > ms.home_score THEN COALESCE(at.guild_name, ms.away_team_name)
        ELSE 'Draw'
    END as winner
FROM MATCH_STATS ms
LEFT JOIN IOSCA_TEAMS ht ON ms.home_guild_id = ht.guild_id
LEFT JOIN IOSCA_TEAMS at ON ms.away_guild_id = at.guild_id
ORDER BY ms.datetime DESC;

-- Tournament system schema

CREATE TABLE IF NOT EXISTS TOURNAMENTS (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    format VARCHAR(10) NOT NULL CHECK (format IN ('5v5', '6v6', '8v8')),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended', 'archived')),
    num_teams INTEGER NOT NULL,
    points_win INTEGER NOT NULL DEFAULT 3,
    points_draw INTEGER NOT NULL DEFAULT 1,
    points_loss INTEGER NOT NULL DEFAULT 0,
    created_by BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS TOURNAMENT_TEAMS (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
    guild_id BIGINT REFERENCES IOSCA_TEAMS(guild_id) ON DELETE SET NULL,
    team_name_snapshot VARCHAR(255) NOT NULL,
    team_icon_snapshot VARCHAR(255),
    seed INTEGER,
    UNIQUE (tournament_id, guild_id)
);

CREATE TABLE IF NOT EXISTS TOURNAMENT_MATCHES (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
    match_stats_id INTEGER NOT NULL REFERENCES MATCH_STATS(id) ON DELETE CASCADE,
    match_key VARCHAR(255),
    home_guild_id BIGINT,
    away_guild_id BIGINT,
    home_score INTEGER NOT NULL DEFAULT 0,
    away_score INTEGER NOT NULL DEFAULT 0,
    game_type VARCHAR(10) NOT NULL CHECK (game_type IN ('5v5', '6v6', '8v8')),
    played_at TIMESTAMP NOT NULL,
    UNIQUE (tournament_id, match_stats_id)
);

CREATE TABLE IF NOT EXISTS TOURNAMENT_STANDINGS (
    tournament_id INTEGER NOT NULL REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
    guild_id BIGINT NOT NULL,
    wins INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    goals_for INTEGER NOT NULL DEFAULT 0,
    goals_against INTEGER NOT NULL DEFAULT 0,
    goal_diff INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    matches_played INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (tournament_id, guild_id)
);

CREATE TABLE IF NOT EXISTS TOURNAMENT_PLAYER_STATS (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
    steam_id VARCHAR(255) NOT NULL,
    discord_id BIGINT,
    player_name VARCHAR(255),
    team_guild_id BIGINT,
    goals INTEGER NOT NULL DEFAULT 0,
    assists INTEGER NOT NULL DEFAULT 0,
    second_assists INTEGER NOT NULL DEFAULT 0,
    keeper_saves INTEGER NOT NULL DEFAULT 0,
    tackles INTEGER NOT NULL DEFAULT 0,
    interceptions INTEGER NOT NULL DEFAULT 0,
    matches_played INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (tournament_id, steam_id, team_guild_id)
);

-- Tournament fixtures + scheduling + forfeits

CREATE TABLE IF NOT EXISTS TOURNAMENT_FIXTURES (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
    week_number INTEGER,
    week_label TEXT,
    home_guild_id BIGINT,
    away_guild_id BIGINT,
    home_name_raw TEXT,
    away_name_raw TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_played BOOLEAN NOT NULL DEFAULT FALSE,
    played_match_stats_id INTEGER REFERENCES MATCH_STATS(id) ON DELETE SET NULL,
    played_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS TOURNAMENT_SCHEDULES (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
    fixture_id INTEGER NOT NULL REFERENCES TOURNAMENT_FIXTURES(id) ON DELETE CASCADE,
    proposed_by BIGINT,
    proposed_time TIMESTAMP NOT NULL,
    slot_start TIMESTAMP NOT NULL,
    server_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    last_action_by BIGINT,
    confirmed_at TIMESTAMP,
    reminder_sent_at TIMESTAMP,
    proposal_expires_at TIMESTAMP,
    proposal_message_ids JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS TOURNAMENT_FORFEITS (
    id SER IAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES TOURNAMENTS(id) ON DELETE CASCADE,
    fixture_id INTEGER REFERENCES TOURNAMENT_FIXTURES(id) ON DELETE SET NULL,
    forfeiting_guild_id BIGINT NOT NULL,
    winner_guild_id BIGINT NOT NULL,
    score_forfeit INTEGER NOT NULL DEFAULT 10,
    created_by BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tournament_status ON TOURNAMENTS(status);
CREATE INDEX IF NOT EXISTS idx_tournament_matches_tournament ON TOURNAMENT_MATCHES(tournament_id);
CREATE INDEX IF NOT EXISTS idx_tournament_matches_match ON TOURNAMENT_MATCHES(match_stats_id);
CREATE INDEX IF NOT EXISTS idx_tournament_standings_points ON TOURNAMENT_STANDINGS(points DESC);
CREATE INDEX IF NOT EXISTS idx_tournament_player_goals ON TOURNAMENT_PLAYER_STATS(goals DESC);
CREATE INDEX IF NOT EXISTS idx_tournament_fixtures_tournament ON TOURNAMENT_FIXTURES(tournament_id);
CREATE INDEX IF NOT EXISTS idx_tournament_fixtures_week ON TOURNAMENT_FIXTURES(tournament_id, week_number);
CREATE INDEX IF NOT EXISTS idx_tournament_fixtures_open ON TOURNAMENT_FIXTURES(tournament_id, is_active, is_played);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tournament_fixtures_played_match ON TOURNAMENT_FIXTURES(played_match_stats_id) WHERE played_match_stats_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tournament_schedules_tournament ON TOURNAMENT_SCHEDULES(tournament_id);
CREATE INDEX IF NOT EXISTS idx_tournament_schedules_fixture ON TOURNAMENT_SCHEDULES(fixture_id);
CREATE INDEX IF NOT EXISTS idx_tournament_schedules_slot ON TOURNAMENT_SCHEDULES(tournament_id, slot_start);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tournament_confirmed_server_slot
ON TOURNAMENT_SCHEDULES(server_name, slot_start)
WHERE status IN ('confirmed');
CREATE INDEX IF NOT EXISTS idx_tournament_forfeits_tournament ON TOURNAMENT_FORFEITS(tournament_id);

-- Triggers
DROP TRIGGER IF EXISTS update_tournaments_updated_at ON TOURNAMENTS;
CREATE TRIGGER update_tournaments_updated_at
    BEFORE UPDATE ON TOURNAMENTS
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tournament_standings_updated_at ON TOURNAMENT_STANDINGS;
CREATE TRIGGER update_tournament_standings_updated_at
    BEFORE UPDATE ON TOURNAMENT_STANDINGS
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tournament_player_stats_updated_at ON TOURNAMENT_PLAYER_STATS;
CREATE TRIGGER update_tournament_player_stats_updated_at
    BEFORE UPDATE ON TOURNAMENT_PLAYER_STATS
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tournament_schedules_updated_at ON TOURNAMENT_SCHEDULES;
CREATE TRIGGER update_tournament_schedules_updated_at
    BEFORE UPDATE ON TOURNAMENT_SCHEDULES
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

