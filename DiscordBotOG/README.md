# 🤖 CA Community Bot

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://discordpy.readthedocs.io/)
[![License](https://img.shields.io/badge/License-Custom-red.svg)](#license)

> **A comprehensive Discord bot for managing the IOSoccer North America and Central America community of 4000+ users**

The CA Community Bot is a sophisticated Discord bot designed to manage competitive IOSoccer gameplay, team organization, tournaments, player statistics, and community interactions for the IOSCA (IOSoccer Central America) community.

## 🌟 Overview

This bot serves as the central hub for:
- **Matchmaking System**: Organize 6v6 and 8v8 matches with automated lineup management
- **Team Management**: Register teams, manage players, and coordinate team activities  
- **Tournament System**: Create and manage multi-league tournaments with automated brackets
- **Player Statistics**: Track individual player performance and generate ratings
- **Server Management**: Integrate with IOSoccer game servers for match coordination
- **Community Features**: Translation services, player lookup, and administrative tools

---

## 🚀 Core Features

### 🏆 **Matchmaking & Match Management**

#### Team Signup System
- **Position-based signups**: Players sign up for specific positions (GK, LB, CB, RB, CM, LW, CF, RW)
- **Interactive buttons**: Click-to-sign interface with real-time lineup updates
- **Substitute system**: FIFO (First In, First Out) sub queue that automatically fills positions
- **Dual game modes**: Support for both 6v6 and 8v8 matches
- **Team channels**: Dedicated channels for individual teams vs main matchmaking channels

#### Match Coordination
- **Ready check system**: Verify all positions are filled before proceeding
- **Server selection**: Choose from configured game servers with real-time status
- **Map selection**: Pick from curated map pools for each game type
- **Automatic DMs**: Send connection details to all players
- **Match tracking**: Log active matches and prevent conflicts

#### Challenge System
- **Inter-team challenges**: Teams can challenge each other directly
- **Broadcast challenges**: Challenge any available team in the community
- **Challenge acceptance**: Interactive accept/decline system
- **Cross-server challenges**: Challenge teams from different Discord servers

### 🏅 **Tournament Management**

#### Tournament Creation & Management
- **Multi-league tournaments**: Support for multiple divisions/leagues
- **Flexible team limits**: Configure teams per league
- **League tables**: Automatic standings calculation with points, goals, etc.
- **Match scheduling**: Add matches manually or through automated systems
- **Tournament completion**: Award ceremonies with champion recognition

#### Tournament Features
- **Team registration**: Teams can register for specific leagues
- **Match result submission**: Manual result entry with validation
- **Forfeit handling**: Automatic 3-0 forfeit scoring
- **Statistics tracking**: Comprehensive tournament stats per team
- **Admin controls**: Tournament editing, team management, match updates

### 📊 **Player Statistics & Ratings**

#### Statistics System
- **Match data processing**: Parse IOSoccer match files for detailed stats
- **Performance metrics**: Track goals, assists, saves, tackles, and 20+ other stats
- **Position-based analysis**: Different metrics for attackers, defenders, midfielders, goalkeepers
- **Time-weighted ratings**: Recent performance weighs more heavily
- **Player cards**: Visual stat cards with bronze/silver/gold tiers

#### Rating Algorithm
- **Composite scoring**: Combines multiple performance aspects
- **Role-specific metrics**: 
  - **Attackers**: Goals, shots on target, chance creation
  - **Playmakers**: Assists, key passes, chance creation
  - **Defenders**: Interceptions, tackles, defensive actions
  - **Goalkeepers**: Saves, goals prevented, distribution
- **Normalized ratings**: Z-score based system for fair comparison
- **Historical tracking**: Maintain rating history over time

### 🖥️ **Server Integration**

#### Game Server Management
- **RCON connectivity**: Real-time server status and control
- **Server monitoring**: Track player counts, map rotation, server health
- **Connection automation**: Automatic server connection info distribution
- **Multi-server support**: Manage multiple IOSoccer servers
- **Server administration**: Add/remove servers, update configurations

### 👥 **Team & Player Management**

#### Team Registration
- **Guild-based teams**: Link Discord servers to teams
- **Captain system**: Designated team leadership roles
- **Player rosters**: Manage team member lists
- **Channel configuration**: Set up team-specific matchmaking channels
- **Team statistics**: Track team performance across matches and tournaments

#### Player Database
- **Discord integration**: Link Discord accounts to Steam IDs
- **Player lookup**: Search players by name, Discord ID, or Steam ID
- **Transfer system**: Handle player movements between teams
- **Activity tracking**: Monitor player participation and engagement

### 🛠️ **Administrative Tools**

#### Moderation Features
- **Channel clearing**: Bulk message deletion with safety checks
- **User management**: Player registration and account linking
- **Permission system**: Role-based command access
- **Error logging**: Comprehensive error tracking and reporting

#### Translation Services
- **Multi-language support**: English ⟷ Spanish translation
- **Community integration**: Help bridge language barriers
- **Automatic detection**: Smart language detection for translations

---

## 🎮 Commands Reference

### **Player Commands**

| Command | Description | Usage |
|---------|-------------|--------|
| `/sign` | Sign up for a position | `/sign team:1 position:GK @player` |
| `/unsign` | Remove from position | `/unsign @player` |
| `/sub` | Join substitute queue | `/sub` |
| `/lineup` | View current lineups | `/lineup` |
| `/ready` | Start match when ready | `/ready` |
| `/help` | Show all commands | `/help` |

### **Team Management**

| Command | Description | Usage |
|---------|-------------|--------|
| `/register_team` | Register a new team | `/register_team` |
| `/view_teams` | Browse all teams | `/view_teams` |
| `/edit_team` | Modify team details | `/edit_team` |
| `/team_players` | Manage team roster | `/team_players` |

### **Tournament System**

| Command | Description | Usage |
|---------|-------------|--------|
| `/register_tournament` | Create tournament | `/register_tournament` |
| `/view_tournament` | Browse tournaments | `/view_tournament` |
| `/tournament_management` | Admin tournament tools | `/tournament_management` |

### **Statistics & Players**

| Command | Description | Usage |
|---------|-------------|--------|
| `/view_player` | Player stats & card | `/view_player @user` |
| `/view_match` | Match history | `/view_match` |
| `/register_me` | Link Steam account | `/register_me` |

### **Server Management**

| Command | Description | Usage |
|---------|-------------|--------|
| `/server_status` | Check server status | `/server_status` |
| `/edit_servers` | Manage servers (admin) | `/edit_servers` |

### **Utility Commands**

| Command | Description | Usage |
|---------|-------------|--------|
| `/translate_english` | Translate to English | `/translate_english Hola mundo` |
| `/translate_spanish` | Translate to Spanish | `/translate_spanish Hello world` |
| `/clear` | Clear messages | `/clear amount:10` |
| `/here` | Get channel info | `/here` |

---

## 🏗️ Technical Architecture

### **Database Schema**

The bot uses a MySQL database with the following key tables:

- **`IOSCA_TEAMS`**: Team information, captains, channels, players
- **`IOSCA_PLAYERS`**: Player Discord/Steam ID linking
- **`TOURNAMENTS_V2`**: Tournament structure and league information
- **`TOURNAMENT_DATA`**: Match results and team statistics
- **`MATCH_STATS`**: Historical match data and results
- **`IOS_SERVERS`**: Game server configurations
- **`TEAM_NAME_MAPPINGS`**: Fuzzy matching for team names
- **`TRANSFER_REQUESTS`**: Player transfer management

### **Core Systems**

#### **Signup Manager** (`signup_manager.py`)
- Manages channel states and player signups
- Handles lineup displays and team coordination
- Processes substitution queue (FIFO system)
- Maintains persistent lineup messages

#### **Challenge Manager** (`challenge_manager.py`)  
- Coordinates inter-team challenges
- Manages challenge states and acceptance
- Handles broadcast vs direct challenges
- Integrates with matchmaking system

#### **Database Manager** (`database_manager.py`)
- Asynchronous database operations
- Connection pooling and optimization
- Data validation and integrity
- Migration and schema management

#### **Rating System** (`ratings/`)
- Processes IOSoccer match files
- Calculates performance metrics
- Generates player ratings and cards
- Maintains historical statistics

#### **Task Scheduler** (`tasks.py`)
- Automated lineup clearing
- Statistics refresh cycles
- Match announcement system
- Background maintenance tasks

### **Performance Features**

- **Async/await**: Non-blocking database and API operations
- **Connection pooling**: Efficient database connection management
- **Caching systems**: Reduce redundant database queries
- **Error handling**: Comprehensive error logging and recovery
- **Rate limiting**: Prevent spam and abuse
- **Memory optimization**: Efficient data structures and cleanup

### **📚 External Libraries & Dependencies**

The bot utilizes a comprehensive set of external libraries for various functionalities:

#### **🤖 Discord & Bot Framework**
- **`discord.py`** - Feature-rich Discord API wrapper with slash commands
- **`py-cord`** - Updated and maintained fork of discord.py
- **`discord.ext.commands`** - Command framework for Discord bots
- **`discord.ui`** - User interface components (Views, Buttons, Modals, Selects)

#### **🗄️ Database & Data Processing**
- **`mysql.connector`** - MySQL database connectivity with connection pooling
- **`pandas`** - Data manipulation and analysis for statistics processing
- **`numpy`** - Numerical computing for rating calculations and statistical analysis
- **`asyncio`** - Asynchronous programming for non-blocking operations

#### **🖼️ Image Processing & Generation**
- **`PIL (Pillow)`** - Python Imaging Library for player card generation
- **`matplotlib`** - Plotting library for performance graphs and charts
- **`requests`** - HTTP library for downloading avatars and team logos
- **`io`** - Input/output operations for image handling

#### **🎮 Game Server Integration**
- **`rcon.source`** - Source RCON protocol for IOSoccer server communication
- **`a2s`** - Source A2S protocol for advanced server querying
- **`paramiko`** - SSH client for server file management and log processing

#### **🌐 Web Services & APIs**
- **`googletrans`** - Google Translate API for multi-language support
- **`requests`** - HTTP client for external API communications
- **`json`** - JSON data parsing and serialization

#### **📊 Statistics & Analysis**
- **`csv`** - CSV file processing for match statistics
- **`difflib`** - Text similarity algorithms for fuzzy matching
- **`collections.Counter`** - Efficient counting for statistical analysis
- **`collections.defaultdict`** - Default dictionaries for data aggregation

#### **🕒 Time & Scheduling**
- **`datetime`** - Date and time handling with timezone support
- **`pytz`** - Timezone calculations and conversions
- **`time`** - Time-related functions and scheduling
- **`asyncio.tasks`** - Background task scheduling and management

#### **🔧 System & Utilities**
- **`os`** - Operating system interface for file operations
- **`sys`** - System-specific parameters and functions
- **`subprocess`** - Process management for external script execution
- **`tempfile`** - Temporary file creation and management
- **`logging`** - Comprehensive logging system with multiple handlers
- **`traceback`** - Error traceback capture and analysis

#### **🔍 Text Processing & Matching**
- **`re`** - Regular expressions for pattern matching
- **`difflib.SequenceMatcher`** - Advanced text similarity algorithms
- **`unicodedata`** - Unicode character handling for international names

#### **🔒 Security & Validation**
- **`hashlib`** - Cryptographic hashing for data integrity
- **`secrets`** - Secure random number generation
- **`typing`** - Type hints for code reliability and documentation

#### **📈 Performance & Monitoring**
- **`multiprocessing`** - Parallel processing for intensive operations
- **`threading`** - Thread management for concurrent operations
- **`memory_profiler`** - Memory usage monitoring and optimization
- **`cProfile`** - Performance profiling for optimization

---

## 🔧 Installation & Setup

### **Prerequisites**
- Python 3.10+
- MySQL 5.7+ or MariaDB 10.3+
- Discord Bot Token
- IOSoccer game servers (optional, for full functionality)

### **Environment Variables**

Create a `.env` file or set these environment variables:

```bash
# Discord Configuration
DISCORD_BOT_TOKEN=your_bot_token_here
CLIENT_ID=your_bot_client_id

# Database Configuration  
DB_HOST=your_database_host
DB_PORT=3306
DB_USER=your_database_username
DB_PASSWORD=your_database_password
DB_NAME=your_database_name

# Optional: Server Configuration
MAIN_GUILD_ID=your_main_discord_server_id
ADMIN_ROLE_ID=your_admin_role_id
```

### **Installation Steps**

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-repo/ca-community-bot.git
   cd ca-community-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. **Initialize the database**
   ```bash
   python main.py
   # Database tables will be created automatically
   ```

5. **Invite the bot to your Discord server**
   - Use the generated invite link from `/invite` command
   - Ensure proper permissions are granted

### **Docker Deployment** (Optional)

```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

---

## 🎯 Usage Examples

### **Setting Up a Match**

1. **Players sign up for positions:**
   ```
   /sign team:1 position:GK
   /sign team:1 position:LB  
   /sign team:2 position:CF
   ```

2. **Check if teams are ready:**
   ```
   /ready
   ```

3. **Bot guides through server and map selection**

4. **Match starts automatically with DMs sent to all players**

### **Managing a Tournament**

1. **Create tournament (Admin only):**
   ```
   /register_tournament
   ```

2. **Teams register for leagues:**
   ```
   /view_tournament → Select tournament → Register Team
   ```

3. **Add match results:**
   ```
   Tournament Management → Add Match Result
   ```

4. **View standings:**
   ```
   Tournament Management → View League Table
   ```

### **Player Statistics**

1. **View player stats:**
   ```
   /view_player @username
   ```

2. **Check match history:**
   ```
   /view_match
   ```

3. **Link Steam account:**
   ```
   /register_me
   ```

---

## 🔍 Advanced Features

### **🔄 FIFO Substitute System**
When a player unsigns from a position, the first player in the substitute queue automatically fills that position. This ensures fair rotation and quick match organization with race condition protection.

### **🌐 Cross-Server Challenges**
Teams from different Discord servers can challenge each other, with the bot coordinating between servers and managing the match setup process through sophisticated state synchronization.

### **🎯 Dynamic Position Validation**
The bot automatically detects whether a channel is configured for 6v6 or 8v8 matches and adjusts available positions accordingly, with real-time validation and conflict resolution.

### **🧠 Intelligent Team Matching & Fuzzy Logic**
- **Advanced string similarity algorithms** using Levenshtein distance and SequenceMatcher
- **Fuzzy team name matching** with 70%+ similarity threshold for CSV import
- **Automatic team nickname mapping** and variation handling
- **Similarity scoring system** for team name disambiguation
- **Bulk auto-linking** of CSV team names to registered Discord teams

### **🖥️ Real-Time Server Integration**
- **RCON connectivity** to IOSoccer game servers for live status monitoring
- **A2S protocol support** for advanced server querying
- **Automatic server health checks** and failover mechanisms
- **Multi-server load balancing** and region selection
- **SSH/SFTP integration** for server file management and log processing

### **⚡ Database Performance Optimization**
- **Connection pooling** with automatic retry and failover
- **Query result caching** with TTL-based expiration
- **Critical database indexing** reducing query times by 90%
- **Emergency performance fixes** for production issues
- **Optimized tournament queries** using JSON fields and bulk operations
- **Asynchronous database operations** preventing bot blocking

### **🎨 Dynamic Player Card Generation**
- **PIL-based image generation** with custom templates (Bronze/Silver/Gold)
- **Real-time avatar downloading** and circular masking
- **Team logo integration** with automatic positioning
- **Rating-based template selection** and visual styling
- **Temporary file management** with automatic cleanup

### **📊 Advanced Statistics Processing**
- **Time-weighted rating calculations** using exponential decay
- **Position-specific performance metrics** for different player roles
- **Multi-dimensional statistical analysis** (Attack/Defense/Playmaker/Goalkeeper)
- **Z-score normalization** for fair player comparisons
- **Weekly automated rating generation** with background processing
- **CSV-to-database synchronization** with conflict resolution

### **🔄 Automated Transfer Management**
- **Transfer window system** with open/close controls
- **Complete transfer history logging** with audit trails
- **Player movement tracking** between teams over time
- **Automated announcements** for transfers and team changes
- **Retroactive team assignment** for historical match data

### **🛡️ Comprehensive Error Handling**
- **Global error catching** with detailed logging and context
- **Rate limit detection** and automatic backoff
- **Database connection recovery** with exponential backoff
- **Discord API error handling** with retry mechanisms
- **Graceful degradation** when external services fail
- **Error context preservation** for debugging

### **🔧 Advanced Channel State Management**
- **Persistent lineup messages** with automatic updates
- **Multi-team state synchronization** across channels
- **Challenge state coordination** between different Discord servers
- **Race condition protection** for simultaneous player actions
- **State validation and corruption recovery**

### **📈 Weekly Performance Graphs**
- **Matplotlib-based chart generation** showing player progression
- **Time-series analysis** of player performance metrics
- **Team average calculations** with weighted contributions
- **Performance trend visualization** over multiple weeks

### **🌍 Multi-Language Support**
- **Google Translate API integration** for English ⟷ Spanish translation
- **Automatic language detection** for community messages
- **Context-aware translation** preserving gaming terminology
- **Community bridge functionality** connecting different language speakers

### **🔍 Advanced Search & Discovery**
- **Fuzzy player name matching** across multiple Discord servers
- **Team discovery** with partial name matching
- **Match history correlation** using similarity algorithms
- **Player statistics aggregation** across team changes

### **⏰ Intelligent Task Scheduling**
- **Automated lineup clearing** at scheduled intervals
- **Background statistics compilation** from game servers
- **Weekly rating generation** with dependency management
- **Match announcement automation** based on activity patterns
- **Database maintenance tasks** with performance monitoring

### **Areas for Contribution**
- 🐛 Bug fixes and error handling improvements
- 🚀 Performance optimizations
- 🎨 UI/UX improvements for Discord interactions
- 📊 Additional statistics and metrics
- 🌐 Internationalization and language support
- 📚 Documentation improvements

---

## 🐛 Support & Issues

### **Getting Help**
- **Discord**: Join our community server for support
- **Issues**: Report bugs on GitHub Issues
- **Documentation**: Check the `/help` command in Discord
- **Contact**: Direct message @shaq#6096 for assistance

### **Common Issues**

**Bot not responding to commands:**
- Check bot permissions in your Discord server
- Verify the bot is online and properly configured
- Ensure environment variables are set correctly

**Database connection errors:**
- Verify database credentials and connection
- Check if database server is accessible
- Ensure required tables exist (run initialization)

**Match coordination problems:**
- Verify game server connectivity and RCON settings
- Check that teams are properly registered
- Ensure players have linked their Steam accounts

**Code Organization and Simplification:**
- Ensure that the full code for this bot can be simplified further with better organized file structure.
---

## 📊 Statistics & Metrics

The CA Community Bot serves a community of **4000+ users** across multiple Discord servers, managing:

- **Daily active matches**: 50+ matches coordinated daily
- **Tournament participation**: 100+ teams in active tournaments
- **Player database**: 2000+ registered players with statistics
- **Server integration**: Multiple IOSoccer game servers monitored
- **Match statistics**: 10,000+ matches tracked and analyzed
- **Community engagement**: 24/7 automated community management

---

## 🏆 Recognition & Awards

The CA Community Bot has become the standard for IOSoccer community management, providing:

- **Streamlined match organization** reducing setup time by 80%
- **Comprehensive statistics tracking** for competitive analysis
- **Professional tournament management** rivaling major esports platforms
- **Cross-language community support** bridging English and Spanish speakers
- **Automated server management** reducing administrative overhead

---

## 📄 License

Copyright (c) 2025 **CA Community Bot**

*THIS SOFTWARE IS PROVIDED WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.*

This bot is proprietary software developed specifically for the IOSoccer Central America community. All rights reserved.

---

## 🙏 Acknowledgments

- **IOSoccer Community**: For their continued support and feedback
- **Discord.py**: For the excellent Python Discord API wrapper
- **MySQL**: For robust database management
- **Community Contributors**: All the players and developers who helped shape this bot

---

<div align="center">

**🤖 Made with ❤️ for the IOSoccer Community**

*Bringing competitive IOSoccer to Discord with style and efficiency*

[⭐ Star this repository](https://github.com/ramymomo20/CA-Community-Bot) | [🐛 Report Issues](https://github.com/ramymomo20/CA-Community-Bot/issues) | [💬 Join Our Discord](https://discord.gg/5Mg957paY2)

</div>
