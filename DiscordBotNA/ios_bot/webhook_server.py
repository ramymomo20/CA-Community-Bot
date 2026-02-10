"""
Webhook server to receive database notifications from Supabase.
Runs alongside the Discord bot to handle real-time match announcements.
"""

import asyncio
import logging
from flask import Flask, request, jsonify
from threading import Thread
from datetime import datetime
import os
import re

logger = logging.getLogger(__name__)

# Flask app for webhook endpoint
webhook_app = Flask(__name__)

# Store reference to bot instance
bot_instance = None
announced_matches = set()

def set_bot_instance(bot):
    """Set the bot instance for webhook handlers to use."""
    global bot_instance
    bot_instance = bot


@webhook_app.route('/webhook/match-insert', methods=['POST'])
def handle_match_insert():
    """Handle webhook notification when a new match is inserted into MATCH_STATS."""
    try:
        # Verify webhook secret for security
        webhook_secret = request.headers.get('X-Webhook-Secret')
        expected_secret = os.getenv('WEBHOOK_SECRET', 'your-secret-key-here')
        
        if webhook_secret != expected_secret:
            logger.warning("Unauthorized webhook request received")
            return jsonify({'error': 'Unauthorized'}), 401
        
        # Get the webhook payload
        payload = request.get_json()
        
        if not payload:
            return jsonify({'error': 'No payload'}), 400
        
        # Extract match data from payload
        # Supabase sends: {"type": "INSERT", "table": "MATCH_STATS", "record": {...}, "old_record": null}
        record = payload.get('record', {})
        webhook_type = payload.get('type', '')
        
        if webhook_type != 'INSERT':
            return jsonify({'status': 'ignored', 'reason': 'not an insert'}), 200
        
        match_id = record.get('id')
        match_datetime = record.get('datetime')
        
        # Skip if already announced
        if match_id in announced_matches:
            return jsonify({'status': 'skipped', 'reason': 'already announced'}), 200
        
        # Check if match is from today
        if match_datetime:
            try:
                # Parse datetime string
                if isinstance(match_datetime, str):
                    match_dt = datetime.fromisoformat(match_datetime.replace('Z', '+00:00'))
                else:
                    match_dt = match_datetime
                
                # Check if match is from today
                from pytz import timezone as pytz_timezone
                est_tz = pytz_timezone('EST')
                current_date = datetime.now(est_tz).date()
                match_date = match_dt.astimezone(est_tz).date()
                
                if match_date != current_date:
                    return jsonify({'status': 'skipped', 'reason': 'not from today'}), 200
            except Exception as e:
                logger.error(f"Error parsing match datetime: {e}")
        
        # Schedule announcement in bot's event loop
        if bot_instance:
            asyncio.run_coroutine_threadsafe(
                announce_match(record),
                bot_instance.loop
            )
            announced_matches.add(match_id)
            
            return jsonify({'status': 'success', 'match_id': match_id}), 200
        else:
            logger.error("Bot instance not set for webhook handler")
            return jsonify({'error': 'Bot not ready'}), 503
            
    except Exception as e:
        logger.error(f"Error handling webhook: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@webhook_app.route('/webhook/sourcecord', methods=['POST'])
def handle_sourcecord_webhook():
    """Handle incoming webhooks from SourceCord (SourceMod plugin).

    Expected JSON: {"username": "PlayerName\nSTEAM_1:0:12345", "content": "message with <@123...>", "avatar_url": "..."}
    This endpoint will attempt to extract the Steam ID from the username and a Discord mention
    from the content, and register the mapping in the database via `bot_instance.db.players.register_player`.
    """
    try:
        # Basic auth via X-Webhook-Secret header (same secret used by other webhooks)
        webhook_secret = request.headers.get('X-Webhook-Secret')
        expected_secret = os.getenv('WEBHOOK_SECRET', 'your-secret-key-here')
        if webhook_secret != expected_secret:
            logger.warning("Unauthorized SourceCord webhook request received")
            return jsonify({'error': 'Unauthorized'}), 401

        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({'error': 'No payload'}), 400

        username = payload.get('username', '')
        content = payload.get('content', '')

        # Username often formatted by SourceCord as "PlayerName\nSTEAM_ID"
        parts = username.splitlines()
        player_name = parts[0].strip() if len(parts) > 0 else ''
        steam_id = parts[-1].strip() if len(parts) > 1 else ''

        # Try to locate first Discord mention <@123456789012345678>
        m = re.search(r"<@!?(?P<id>\d+)>", content)
        if not m:
            return jsonify({'status': 'no-discord-mention'}), 200

        try:
            discord_id = int(m.group('id'))
        except Exception:
            return jsonify({'error': 'invalid-discord-id'}), 400

        # Register on bot loop
        if bot_instance:
            async def do_register():
                try:
                    # Use stored method to register or update mapping
                    ok = await bot_instance.db.players.register_player(discord_id, player_name, steam_id)
                    return ok
                except Exception as e:
                    logger.error(f"Error registering player from SourceCord webhook: {e}", exc_info=True)
                    return False

            fut = asyncio.run_coroutine_threadsafe(do_register(), bot_instance.loop)
            try:
                ok = fut.result(timeout=10)
            except Exception as e:
                logger.error(f"Timeout/error waiting for register_player: {e}")
                return jsonify({'error': 'registration_failed'}), 500

            return jsonify({'registered': bool(ok)}), 200
        else:
            logger.error("Bot instance not set for SourceCord webhook handler")
            return jsonify({'error': 'Bot not ready'}), 503

    except Exception as e:
        logger.error(f"Error handling SourceCord webhook: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


async def announce_match(match_record):
    """Announce a new match in the fixtures channel."""
    try:
        import discord
        from ios_bot.config import FIXTURES_CHANNEL_ID
        
        if not FIXTURES_CHANNEL_ID:
            logger.warning("FIXTURES_CHANNEL_ID not configured")
            return
        
        # Get match details from database
        match_id = match_record.get('id')
        
        query = """
        SELECT 
            ms.id,
            ms.datetime,
            ms.home_score,
            ms.away_score,
            ht.home_team_name as home_team,
            at.away_team_name as away_team
        FROM MATCH_STATS ms
        LEFT JOIN IOSCA_TEAMS ht ON ms.home_guild_id = ht.guild_id
        LEFT JOIN IOSCA_TEAMS at ON ms.away_guild_id = at.guild_id
        WHERE ms.id = $1
        """
        
        match = await bot_instance.db.pool.fetchrow(query, match_id)
        
        if not match:
            logger.warning(f"Match {match_id} not found in database")
            return
        
        home_team = match['home_team']
        away_team = match['away_team']
        home_score = match['home_score'] or 0
        away_score = match['away_score'] or 0
        
        # Get the fixtures channel
        channel = bot_instance.get_channel(FIXTURES_CHANNEL_ID)
        
        if not channel:
            logger.error(f"Fixtures channel {FIXTURES_CHANNEL_ID} not found")
            return
        
        # Create announcement embed
        score = f"{home_score} - {away_score}"
        embed = discord.Embed(
            title="Match Concluded",
            description=f"**FULL TIME: {home_team} {score} {away_team}**\n\nThe match overview is now available to view: use `/view_match` to see the full stats.",
            color=discord.Color.blue()
        )
        
        from datetime import timezone as dt_timezone
        timestamp = datetime.now(dt_timezone.utc).strftime("%I:%M %p")
        embed.set_footer(text=f"Match Concluded • {timestamp}")
        
        # Send announcement
        await channel.send(embed=embed)
        logger.info(f"✅ Announced match: {home_team} vs {away_team}")
        
    except Exception as e:
        logger.error(f"Error announcing match: {e}", exc_info=True)


@webhook_app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'bot_ready': bot_instance is not None,
        'timestamp': datetime.utcnow().isoformat()
    }), 200


def run_webhook_server(host='0.0.0.0', port=5000):
    """Run the webhook server in a separate thread."""
    webhook_app.run(host=host, port=port, debug=False, use_reloader=False)


def start_webhook_server(bot, host='0.0.0.0', port=5000):
    """Start the webhook server in a background thread.
    
    Args:
        bot: Discord bot instance
        host: Host to bind to (default: 0.0.0.0 for all interfaces)
        port: Port to listen on (default: 5000)
    """
    set_bot_instance(bot)
    
    # Start Flask server in a daemon thread
    webhook_thread = Thread(
        target=run_webhook_server,
        args=(host, port),
        daemon=True,
        name="WebhookServer"
    )
    webhook_thread.start()
    
    logger.info(f"🌐 Webhook server started on http://{host}:{port}")
    logger.info(f"   Webhook endpoint (match insert): http://{host}:{port}/webhook/match-insert")
    logger.info(f"   Webhook endpoint (SourceCord): http://{host}:{port}/webhook/sourcecord")
    logger.info(f"   Health check: http://{host}:{port}/health")
    
    return webhook_thread
