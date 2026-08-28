"""
IOSCA Community Bot - Main Entry Point
Includes environment validation, restart logic, and optional Hub API startup.
"""

import argparse
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _ensure_hub_mysql_backend_path() -> None:
    """Legacy no-op now that the Hub is back on Postgres-only reads."""
    return


_ensure_hub_mysql_backend_path()


def _as_bool(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_hub_backend_dir():
    """Return hub backend directory if present."""
    root = Path(__file__).resolve().parent
    configured = os.getenv("IOSCA_HUB_BACKEND_DIR")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if (candidate / "app" / "main.py").exists():
            return candidate
    candidates = [
        root / "ioscahub.github.io" / "backend",
        root / "iosca_hub" / "backend",
        root / "iosca_hub_github" / "ioscahub.github.io" / "backend",
    ]
    for candidate in candidates:
        if (candidate / "app" / "main.py").exists():
            return candidate
    return None


class HubAPIServer:
    """Run the Hub FastAPI app in a background thread."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.thread = None
        self.server = None
        self.host = None
        self.port = None

    def start(self):
        if not self.enabled:
            print("Hub API startup disabled by flag.")
            return True

        hub_backend_dir = _resolve_hub_backend_dir()
        if not hub_backend_dir:
            print("WARNING: Hub backend not found.")
            print("Expected one of:")
            print("  - ioscahub.github.io/backend")
            print("  - iosca_hub/backend")
            print("  - iosca_hub_github/ioscahub.github.io/backend")
            print("Or set IOSCA_HUB_BACKEND_DIR to the backend folder explicitly.")
            print("Bot will continue without Hub API.")
            return False

        backend_path = str(hub_backend_dir)
        if backend_path not in sys.path:
            sys.path.insert(0, backend_path)

        try:
            import uvicorn
            from app.main import app as hub_app
        except Exception as e:
            print(f"WARNING: Hub API import failed: {e}")
            print("Install missing packages (fastapi, uvicorn) to run Hub API.")
            print("Bot will continue without Hub API.")
            return False

        self.host = os.getenv("IOSCA_HUB_API_HOST", "0.0.0.0").strip() or "0.0.0.0"
        self.port = int(os.getenv("IOSCA_HUB_API_PORT", "8000"))
        config = uvicorn.Config(
            hub_app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        self.server = uvicorn.Server(config)

        def _run_server():
            self.server.run()

        self.thread = threading.Thread(target=_run_server, name="iosca-hub-api", daemon=True)
        self.thread.start()
        time.sleep(0.75)

        if self.thread.is_alive():
            print(f"Hub API running at http://{self.host}:{self.port}")
            return True

        print("WARNING: Hub API thread exited during startup.")
        return False

    def stop(self):
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=8)
        self.server = None
        self.thread = None


def check_environment():
    """Check if all required environment variables are set."""
    required_vars = ["DISCORD_BOT_TOKEN"]
    db_vars = ["SUPABASE_DB_URL or SUPABASE_POOLER_URL"]

    missing = [var for var in required_vars if not os.getenv(var)]
    db_missing = [var for var in db_vars if not (os.getenv("SUPABASE_DB_URL") or os.getenv("SUPABASE_POOLER_URL"))]

    if missing or db_missing:
        print("Missing required environment variables:")
        for var in missing + db_missing:
            print(f"  - {var}")
        print("Please set these variables before starting the bot.")
        return False

    print("Database configuration is set.")
    return True


def start_bot_simple(start_hub_api=True):
    """Start the bot without restart logic."""
    if not check_environment():
        return False

    hub_enabled = start_hub_api and _as_bool(os.getenv("IOSCA_HUB_ENABLED"), True)
    hub_server = HubAPIServer(enabled=hub_enabled)
    hub_server.start()

    try:
        print("Starting IOSCA Community Bot...")
        from ios_bot import main
        main()
        return True
    except KeyboardInterrupt:
        print("Received keyboard interrupt. Stopping bot...")
        return True
    except Exception as e:
        print(f"Critical error starting bot: {e}")
        return False
    finally:
        hub_server.stop()


def start_bot_with_restarts(max_restarts=3, restart_delay=30, start_hub_api=True):
    """Start the bot with automatic restart logic."""
    if not check_environment():
        return False

    hub_enabled = start_hub_api and _as_bool(os.getenv("IOSCA_HUB_ENABLED"), True)
    hub_server = HubAPIServer(enabled=hub_enabled)
    hub_server.start()

    restart_count = 0

    try:
        while restart_count < max_restarts:
            try:
                print(f"\n{'=' * 50}")
                print(f"Starting IOSCA Community Bot (Attempt {restart_count + 1}/{max_restarts})")
                print(f"Time: {datetime.now()}")
                print(f"{'=' * 50}")

                from ios_bot import main
                main()

                print("Bot exited normally. Not restarting.")
                break

            except KeyboardInterrupt:
                print("Received keyboard interrupt. Stopping bot...")
                break
            except SystemExit as e:
                if e.code == 0:
                    print("Bot exited normally. Not restarting.")
                    break
                if e.code == 1:
                    print("Bot exited with configuration error. Not restarting.")
                    break
                print(f"Bot exited with code {e.code}")
            except Exception as e:
                print(f"Bot crashed with error: {e}")

                restart_count += 1

                if restart_count < max_restarts:
                    print(f"Waiting {restart_delay} seconds before restart...")
                    print(f"Restart {restart_count}/{max_restarts}")
                    time.sleep(restart_delay)

                    # Increase delay after each restart to prevent rapid reconnections.
                    restart_delay = min(int(restart_delay * 1.5), 300)

                    if "ios_bot" in sys.modules:
                        del sys.modules["ios_bot"]
                else:
                    print(f"Maximum restart attempts ({max_restarts}) reached.")
                    print("Please check the logs and fix any issues before restarting.")
                    return False
    finally:
        hub_server.stop()

    return True


def main():
    """Main entry point with command line argument handling."""
    parser = argparse.ArgumentParser(description="IOSCA Community Bot")
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Start without automatic restart logic",
    )
    parser.add_argument(
        "--no-hub-api",
        action="store_true",
        help="Do not start the Hub API alongside the bot",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=3,
        help="Maximum number of restart attempts (default: 3)",
    )
    parser.add_argument(
        "--restart-delay",
        type=int,
        default=30,
        help="Initial delay between restarts in seconds (default: 30)",
    )

    args = parser.parse_args()

    print("IOSCA Community Bot")
    print("=" * 50)

    if args.no_restart:
        print("Running in no-restart mode...")
        success = start_bot_simple(start_hub_api=not args.no_hub_api)
    else:
        print(
            f"Running with restart protection (max: {args.max_restarts}, "
            f"delay: {args.restart_delay}s)..."
        )
        success = start_bot_with_restarts(
            args.max_restarts,
            args.restart_delay,
            start_hub_api=not args.no_hub_api,
        )

    if success:
        print("Bot shutdown completed.")
    else:
        print("Bot failed to start properly.")
        sys.exit(1)


if __name__ == "__main__":
    main()
