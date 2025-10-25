"""
IOSCA Community Bot - Main Entry Point
Includes environment validation and optional restart logic.
"""

import sys
import os
import time
import argparse
from datetime import datetime

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def check_environment():
    """Check if all required environment variables are set."""
    required_vars = [
        'DISCORD_BOT_TOKEN'
    ]
    
    # Primary database variables (required)
    primary_db_vars = [
        'DB_USER1', 
        'DB_PASSWORD1',
        'DB_NAME1'
    ]
    
    # Secondary database variables (optional for failover)
    secondary_db_vars = [
        'DB_USER2', 
        'DB_PASSWORD2',
        'DB_NAME2'
    ]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
    
    # Check primary database
    primary_missing = []
    for var in primary_db_vars:
        if not os.getenv(var):
            primary_missing.append(var)
    
    # Check secondary database
    secondary_missing = []
    secondary_available = True
    for var in secondary_db_vars:
        if not os.getenv(var):
            secondary_missing.append(var)
            secondary_available = False
    
    if missing or primary_missing:
        print("❌ Missing required environment variables:")
        for var in missing + primary_missing:
            print(f"   - {var}")
        print("\nPlease set these environment variables before starting the bot.")
        print("See ENVIRONMENT_SETUP.md for instructions.")
        return False
    
    print("✅ Primary database configuration is set.")
    
    if secondary_available:
        print("✅ Secondary database configuration is set (failover enabled).")
    else:
        print("⚠️ Secondary database configuration is missing (failover disabled).")
        if secondary_missing:
            print("   Missing secondary DB vars:", ", ".join(secondary_missing))
    
    return True

def start_bot_simple():
    """Start the bot without restart logic."""
    if not check_environment():
        return False
    
    try:
        print("Starting IOSCA Community Bot...")
        from ios_bot import main
        main()
        return True
    except KeyboardInterrupt:
        print("\n🛑 Received keyboard interrupt. Stopping bot...")
        return True
    except Exception as e:
        print(f"❌ Critical error starting bot: {e}")
        return False

def start_bot_with_restarts(max_restarts=3, restart_delay=30):
    """Start the bot with automatic restart logic."""
    if not check_environment():
        return False
    
    restart_count = 0
    
    while restart_count < max_restarts:
        try:
            print(f"\n{'='*50}")
            print(f"Starting IOSCA Community Bot (Attempt {restart_count + 1}/{max_restarts})")
            print(f"Time: {datetime.now()}")
            print(f"{'='*50}")
            
            from ios_bot import main
            main()
            
            # If we get here, the bot exited normally
            print("Bot exited normally. Not restarting.")
            break
            
        except KeyboardInterrupt:
            print("\n🛑 Received keyboard interrupt. Stopping bot...")
            break
        except SystemExit as e:
            if e.code == 0:
                print("Bot exited normally. Not restarting.")
                break
            elif e.code == 1:
                print("Bot exited with configuration error. Not restarting.")
                break
            else:
                print(f"Bot exited with code {e.code}")
        except Exception as e:
            print(f"❌ Bot crashed with error: {e}")
            
            restart_count += 1
            
            if restart_count < max_restarts:
                print(f"💤 Waiting {restart_delay} seconds before restart...")
                print(f"🔄 Restart {restart_count}/{max_restarts}")
                time.sleep(restart_delay)
                
                # Increase delay after each restart to prevent rapid reconnections
                restart_delay = min(restart_delay * 1.5, 300)  # Max 5 minutes
                
                # Clear the imported module to ensure clean restart
                if 'ios_bot' in sys.modules:
                    del sys.modules['ios_bot']
            else:
                print(f"❌ Maximum restart attempts ({max_restarts}) reached.")
                print("Please check the logs and fix any issues before restarting.")
                return False
    
    return True

def main():
    """Main entry point with command line argument handling."""
    parser = argparse.ArgumentParser(description='IOSCA Community Bot')
    parser.add_argument('--no-restart', action='store_true', 
                       help='Start without automatic restart logic')
    parser.add_argument('--max-restarts', type=int, default=3,
                       help='Maximum number of restart attempts (default: 3)')
    parser.add_argument('--restart-delay', type=int, default=30,
                       help='Initial delay between restarts in seconds (default: 30)')
    
    args = parser.parse_args()
    
    print("IOSCA Community Bot")
    print("=" * 50)
    
    if args.no_restart:
        print("Running in no-restart mode...")
        success = start_bot_simple()
    else:
        print(f"Running with restart protection (max: {args.max_restarts}, delay: {args.restart_delay}s)...")
        success = start_bot_with_restarts(args.max_restarts, args.restart_delay)
    
    if success:
        print("Bot shutdown completed.")
    else:
        print("Bot failed to start properly.")
        sys.exit(1)

if __name__ == "__main__":
    main()