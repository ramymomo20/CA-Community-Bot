import re
import io
import json
import asyncio
import logging
from datetime import datetime, timedelta
import discord
from discord.ext import commands
from ios_bot.config import *
from ios_bot.utils.match_importer import MatchImporter

logger = logging.getLogger(__name__)


def filename_to_match_id(filename: str) -> str:
    """Convert SFTP filename to canonical match_id used by DB.

    Examples of filenames seen:
      2024.12.28_00h.43m.41s_pa-vs-iosca_12-11.json
      2026.01.13_22h.54m.02s_mg-vs-pa_8-7.json

    Desired canonical id format: YYYYMMDDHHMMSS<teams><score>
    We'll extract datetime, team abbreviations, and scores when possible.
    """
    # Strip extension
    base = filename.rsplit('.', 1)[0]

    # Try pattern: YYYY.MM.DD_HHh.MMm.SSs_home-vs-away_SCORE
    m = re.match(r"(?P<date>\d{4}\.\d{2}\.\d{2}_\d{2}h\.\d{2}m\.\d{2}s)_(?P<teams>.+?)_(?P<score>\d+-\d+)$", base)
    if m:
        datepart = m.group('date')
        teams = m.group('teams')
        score = m.group('score')
        # normalize date
        try:
            dt = datetime.strptime(datepart, "%Y.%m.%d_%Hh.%Mm.%Ss")
            ts = dt.strftime("%Y%m%d%H%M%S")
        except Exception:
            ts = re.sub(r"[^0-9]", '', datepart)

        # teams may be like pa-vs-iosca or mg-vs-pa
        teams = teams.replace('-vs-', '-v-')
        teams = re.sub(r"[^A-Za-z0-9\-v_]", '', teams)

        return f"{ts}{teams}{score.replace('-', '-') }"

    # fallback: try compact pattern like 20260113225402g-v-pa8-7
    m2 = re.match(r"(?P<ts>\d{14})(?P<rest>.+)$", base)
    if m2:
        return base

    # last resort: remove non-alnum
    return re.sub(r"[^0-9A-Za-z\-]", '', base)


@bot.slash_command(name="sync_sftp_matches", description="Find SFTP JSONs missing from DB (admin)")
@commands.has_permissions(administrator=True)
async def sync_sftp_matches(ctx: discord.ApplicationContext, days: int = 30, dry_run: bool = True, limit: int = 200):
    await ctx.defer(ephemeral=True)
    asyncio.create_task(_run_sync(ctx, days, dry_run, limit))


async def _run_sync(ctx, days, dry_run, limit):
    try:
        from ios_bot.ratings.compile_stats import SFTPClient
    except Exception:
        SFTPClient = None

    missing = []
    checked = 0

    sftp = None
    try:
        if SFTPClient:
            sftp = SFTPClient()
            await sftp.connect()
            files = await sftp.list_json_files(days=days)
        else:
            from ios_bot.tasks import list_recent_sftp_files
            files = await list_recent_sftp_files(days=days)
    except Exception as e:
        await ctx.followup.send(f"⚠️ Could not list SFTP files: {e}")
        return

    if not files:
        await ctx.followup.send("No recent SFTP JSON files found.")
        return

    files = files[:limit]

    db = bot.db
    importer = MatchImporter(db)

    # One query for all candidate match_ids instead of up to `limit`
    # individual fetchrow round trips (each independently acquiring a pool
    # connection).
    canonical_by_fname = {fname: filename_to_match_id(fname) for fname in files}
    existing_ids = set()
    if canonical_by_fname:
        existing_rows = await db.pool.fetch(
            "SELECT match_id FROM MATCH_STATS WHERE match_id = ANY($1::text[])",
            list(canonical_by_fname.values()),
        )
        existing_ids = {r["match_id"] for r in existing_rows}

    for fname, canonical in canonical_by_fname.items():
        checked += 1
        if canonical not in existing_ids:
            missing.append((fname, canonical))

    missing_count = len(missing)

    if dry_run:
        lines = [f"Checked {checked} files; missing: {missing_count}\nSample missing:"]
        for f, c in missing[:20]:
            lines.append(f"- {f} -> {c}")
        await ctx.followup.send("\n".join(lines))
        return

    results = {"inserted": [], "failed": []}

    for fname, canonical in missing:
        try:
            if SFTPClient:
                content = await sftp.get_file_content(fname)
                data = json.loads(content)
            else:
                from ios_bot.tasks import fetch_sftp_file_content
                content = await fetch_sftp_file_content(fname)
                data = json.loads(content)

            match_id = await importer.import_match_from_json(data, match_id_str=canonical, source_filename=fname)
            if match_id:
                results['inserted'].append((fname, match_id))
            else:
                results['failed'].append((fname, 'import returned None'))
        except Exception as e:
            results['failed'].append((fname, str(e)))

    summary = [f"Inserted: {len(results['inserted'])}, Failed: {len(results['failed'])}"]
    if results['failed']:
        summary.append("Failures (sample):")
        for f, e in results['failed'][:10]:
            summary.append(f"- {f}: {e}")

    await ctx.followup.send("\n".join(summary))


def setup(bot):
    # Command is registered via @commands.slash_command at import time.
    return
