# Environment Setup for IOSPL Bot

## Required Environment Variables

Before running the bot, you must set the following environment variables:

### Discord Configuration
- `DISCORD_BOT_TOKEN`: Your Discord bot token (get this from Discord Developer Portal)

### Database Configuration
- `DB_HOST`: Database host (default: db-par-01.apollopanel.com)
- `DB_PORT`: Database port (default: 3306)
- `DB_USER`: Database username
- `DB_PASSWORD`: Database password
- `DB_NAME`: Database name

## Setup Instructions

1. **Get a new Discord bot token:**
   - Go to https://discord.com/developers/applications
   - Select your bot application
   - Go to "Bot" section
   - Click "Reset Token" and copy the new token

2. **Set environment variables (see Platform-Specific Instructions below):**

## Platform-Specific Environment Variable Setup

### 🖥️ **VPS/Dedicated Server (Linux)**
```bash
# Create a .env file (recommended)
nano .env

# Add these lines to the .env file:
DISCORD_BOT_TOKEN=your_bot_token_here
DB_HOST=db-par-01.apollopanel.com
DB_PORT=3306
DB_USER=your_db_username
DB_PASSWORD=your_db_password
DB_NAME=your_db_name

# Load environment variables and run
source .env
python main.py
```

### 🪟 **Windows Server**
```powershell
# Set environment variables in PowerShell
$env:DISCORD_BOT_TOKEN="your_bot_token_here"
$env:DB_HOST="db-par-01.apollopanel.com"
$env:DB_PORT="3306"
$env:DB_USER="your_db_username"
$env:DB_PASSWORD="your_db_password"
$env:DB_NAME="your_db_name"

# Run the bot
python main.py
```

### ☁️ **Cloud Hosting Services**

**Heroku:**
```bash
heroku config:set DISCORD_BOT_TOKEN=your_bot_token_here
heroku config:set DB_HOST=db-par-01.apollopanel.com
heroku config:set DB_USER=your_db_username
heroku config:set DB_PASSWORD=your_db_password
heroku config:set DB_NAME=your_db_name
```

**Railway:**
- Go to your project dashboard
- Click "Variables" tab
- Add each environment variable

**Render:**
- Go to your service dashboard
- Click "Environment" tab
- Add each environment variable

### 🐧 **Using systemd (Linux Service)**
```bash
# Create service file
sudo nano /etc/systemd/system/iospl-bot.service

# Add this content:
[Unit]
Description=IOSPL Community Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/your/bot
Environment=DISCORD_BOT_TOKEN=your_bot_token_here
Environment=DB_HOST=db-par-01.apollopanel.com
Environment=DB_USER=your_db_username
Environment=DB_PASSWORD=your_db_password
Environment=DB_NAME=your_db_name
ExecStart=/usr/bin/python3 main.py
Restart=always

[Install]
WantedBy=multi-user.target

# Enable and start service
sudo systemctl enable iospl-bot.service
sudo systemctl start iospl-bot.service
```

### 🐳 **Docker**
```dockerfile
# In your Dockerfile
ENV DISCORD_BOT_TOKEN=your_bot_token_here
ENV DB_HOST=db-par-01.apollopanel.com
ENV DB_USER=your_db_username
ENV DB_PASSWORD=your_db_password
ENV DB_NAME=your_db_name

# Or use docker-compose.yml
version: '3.8'
services:
  iospl-bot:
    build: .
    environment:
      - DISCORD_BOT_TOKEN=your_bot_token_here
      - DB_HOST=db-par-01.apollopanel.com
      - DB_USER=your_db_username
      - DB_PASSWORD=your_db_password
      - DB_NAME=your_db_name
```

3. **Run the bot:**

   **Standard mode (with restart protection):**
   ```bash
   python main.py
   ```

   **No restart protection (for debugging):**
   ```bash
   python main.py --no-restart
   ```

   **Custom restart settings:**
   ```bash
   python main.py --max-restarts 5 --restart-delay 60
   ```

## Command Line Options

- `--no-restart`: Start without automatic restart logic (useful for debugging)
- `--max-restarts N`: Maximum number of restart attempts (default: 3)
- `--restart-delay N`: Initial delay between restarts in seconds (default: 30)
- `--help`: Show all available options

## Security Notes

- **NEVER** commit tokens or passwords to version control
- Keep your bot token secure and regenerate it if compromised
- The bot will refuse to start without proper environment variables set
- Use `.env` files locally and proper environment variable management on servers

## Simplified Structure

The bot now uses a simplified file structure:
- **`main.py`**: Entry point with environment validation and restart logic
- **`ios_bot/__init__.py`**: Core bot setup and Discord event handlers
- **`ios_bot/config.py`**: Configuration and environment variable loading
- **`ios_bot/tasks.py`**: Scheduled background tasks

## Fixed Issues

This update fixes the following critical issues:
- Removed hardcoded tokens and credentials
- Added comprehensive error handling to prevent crash loops
- Reduced task frequency to prevent API rate limiting
- Added timeouts to prevent hanging processes
- Improved bot startup and shutdown procedures
- Simplified file structure for easier maintenance 