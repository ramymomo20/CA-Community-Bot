"""
Batch Player Registration Command
Allows administrators to register multiple players from a CSV file.
"""

from ios_bot.config import *
import io
import csv
import re


ID64_BASE = 76561197960265728


def _to_legacy_steam(raw_steam_id: str) -> str | None:
    """Normalize supported Steam ID formats to STEAM_0:X:Y."""
    if not raw_steam_id:
        return None

    value = str(raw_steam_id).strip()
    if not value:
        return None

    # Common typo in manually entered IDs.
    value = value.replace("STEAM0:", "STEAM_0:")

    legacy = re.fullmatch(r"STEAM_\d+:([01]):(\d+)", value, flags=re.IGNORECASE)
    if legacy:
        y = int(legacy.group(1))
        z = int(legacy.group(2))
        return f"STEAM_0:{y}:{z}"

    steam3 = re.fullmatch(r"\[U:1:(\d+)\]", value, flags=re.IGNORECASE)
    if steam3:
        account_id = int(steam3.group(1))
        y = account_id % 2
        z = (account_id - y) // 2
        return f"STEAM_0:{y}:{z}"

    if re.fullmatch(r"\d{16,20}", value):
        sid64 = int(value)
        offset = sid64 - ID64_BASE
        y = offset % 2
        z = (offset - y) // 2
        return f"STEAM_0:{y}:{z}"

    return None


def _steam_aliases(raw_steam_id: str) -> list[str]:
    """Build comparable aliases so existing rows are found across formats."""
    raw = str(raw_steam_id or "").strip()
    if not raw:
        return []

    aliases: list[str] = []

    def _add(value: str):
        v = str(value or "").strip()
        if v and v not in aliases:
            aliases.append(v)

    _add(raw)
    legacy = _to_legacy_steam(raw)
    if not legacy:
        return aliases

    _add(legacy)
    m = re.fullmatch(r"STEAM_0:([01]):(\d+)", legacy, flags=re.IGNORECASE)
    if m:
        y = int(m.group(1))
        z = int(m.group(2))
        account_id = z * 2 + y
        _add(f"[U:1:{account_id}]")
        _add(str(ID64_BASE + account_id))
    return aliases


async def _get_player_by_steam_alias(steam_id: str) -> dict | None:
    """Find player row by any equivalent Steam ID representation."""
    aliases = [a.strip().lower() for a in _steam_aliases(steam_id) if str(a).strip()]
    if not aliases:
        return None

    row = await bot.db.pool.fetchrow(
        """
        SELECT *
        FROM IOSCA_PLAYERS
        WHERE lower(trim(steam_id::text)) = ANY($1::text[])
        LIMIT 1
        """,
        aliases
    )
    return dict(row) if row else None


@bot.slash_command(
    name="batch_register",
    description="Register multiple players from a CSV file (Admin only)"
)
@commands.has_permissions(administrator=True)
async def batch_register(
    ctx: discord.ApplicationContext,
    file: discord.Option(discord.Attachment, "CSV file with format: name,discordID,steamID")
):
    """Register multiple players from a CSV file.
    
    CSV Format:
    name,discordID,steamID
    John Doe,123456789012345678,STEAM_0:1:12345678
    Jane Smith,987654321098765432,76561198111111111
    """
    await ctx.defer()
    
    # Validate file type
    if not file.filename.endswith('.csv'):
        await ctx.followup.send("❌ Please upload a CSV file.", ephemeral=True)
        return
    
    try:
        # Download and read file
        content = await file.read()
        text = content.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)

        if len(rows) < 1:
            await ctx.followup.send("❌ CSV file is empty.", ephemeral=True)
            return
        
        # Results tracking
        results = {
            'success': [],
            'errors': [],
            'skipped': []
        }
        
        # Process each row
        for line_num, row in enumerate(rows, 1):
            if not row or not any(str(col).strip() for col in row):
                continue

            # Optional header support: name,discordID,steamID
            if line_num == 1:
                lowered = [str(col).strip().lower() for col in row[:3]]
                if lowered == ["name", "discordid", "steamid"]:
                    continue

            try:
                if len(row) != 3:
                    results['errors'].append(f"Line {line_num}: Invalid format (expected 3 columns, got {len(row)})")
                    continue

                name = str(row[0]).strip()
                discord_id_str = str(row[1]).strip()
                steam_raw = str(row[2]).strip()
                
                # Validate Discord ID
                try:
                    discord_id = int(discord_id_str)
                except ValueError:
                    results['errors'].append(f"Line {line_num} ({name}): Invalid Discord ID '{discord_id_str}'")
                    continue
                
                steam_id = _to_legacy_steam(steam_raw)
                if not steam_id:
                    results['errors'].append(
                        f"Line {line_num} ({name}): Invalid Steam ID format '{steam_raw}'"
                    )
                    continue

                # Skip only if this Steam ID is linked to a different real Discord user.
                existing_by_steam = await _get_player_by_steam_alias(steam_id)
                if existing_by_steam:
                    existing_discord = existing_by_steam.get("discord_id")
                    has_real_discord = str(existing_discord).strip() not in ("", "0", "None", "null")
                    if has_real_discord and str(existing_discord) != str(discord_id):
                        results['skipped'].append(
                            f"{name} (steam already linked to different discord)"
                        )
                        continue

                # Register player
                ok = await bot.db.players.register_player(
                    discord_id=discord_id,
                    username=name,
                    steam_id=steam_id
                )
                if not ok:
                    results['errors'].append(f"Line {line_num} ({name}): Could not register player")
                    continue

                results['success'].append(name)
                
            except Exception as e:
                results['errors'].append(f"Line {line_num}: {str(e)}")
                continue
        
        # Create results embed
        embed = discord.Embed(
            title="📊 Batch Registration Results",
            color=discord.Color.green() if results['success'] else discord.Color.red()
        )
        
        # Add success field
        if results['success']:
            success_text = '\n'.join(results['success'][:20])
            if len(results['success']) > 20:
                success_text += f"\n... and {len(results['success']) - 20} more"
            embed.add_field(
                name=f"✅ Successfully Registered ({len(results['success'])})",
                value=success_text or "None",
                inline=False
            )
        
        # Add skipped field
        if results['skipped']:
            skipped_text = '\n'.join(results['skipped'][:10])
            if len(results['skipped']) > 10:
                skipped_text += f"\n... and {len(results['skipped']) - 10} more"
            embed.add_field(
                name=f"⏭️ Skipped ({len(results['skipped'])})",
                value=skipped_text,
                inline=False
            )
        
        # Add errors field
        if results['errors']:
            errors_text = '\n'.join(results['errors'][:10])
            if len(results['errors']) > 10:
                errors_text += f"\n... and {len(results['errors']) - 10} more"
            embed.add_field(
                name=f"❌ Errors ({len(results['errors'])})",
                value=errors_text,
                inline=False
            )
        
        # Summary
        total = len(results['success']) + len(results['skipped']) + len(results['errors'])
        embed.set_footer(text=f"Processed {total} lines from CSV")
        
        await ctx.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in batch_register command: {e}")
        await ctx.followup.send(f"❌ Error processing file: {str(e)}", ephemeral=True)


@batch_register.error
async def batch_register_error(ctx, error):
    """Handle errors for batch_register command."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("❌ You need Administrator permissions to use this command.", ephemeral=True)
    else:
        logger.error(f"Error in batch_register: {error}")
        await ctx.respond(f"❌ An error occurred: {str(error)}", ephemeral=True)
