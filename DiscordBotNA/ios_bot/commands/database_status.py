from ios_bot.config import *
from ios_bot.database_manager import (
    get_db_status_info, 
    perform_failover, 
    is_secondary_db_available,
    test_database_connection,
    sync_databases,
    _current_db
)

@bot.slash_command(name="db_status", description="Check database connection status and failover information (Admin only)")
@commands.has_permissions(administrator=True)
async def database_status(interaction: discord.Interaction):
    """Check current database status and failover information."""
    await interaction.response.defer()
    
    try:
        # Get database status information
        status_info = get_db_status_info()
        
        # Test current database connection
        db_test_result = await test_database_connection()
        
        # Create status embed
        embed = discord.Embed(
            title="🗄️ Database Status",
            color=discord.Color.green() if db_test_result['connected'] else discord.Color.red()
        )
        
        # Current database info
        current_config = status_info['current_config']
        embed.add_field(
            name="📍 Current Database",
            value=f"**Type:** {status_info['current_db'].upper()}\n"
                  f"**Host:** `{current_config['host']}`\n"
                  f"**Database:** `{current_config['database']}`\n"
                  f"**Status:** {'✅ Connected' if db_test_result['connected'] else '❌ Disconnected'}",
            inline=False
        )
        
        # Failover information
        failover_info = f"**Secondary DB Available:** {'✅ Yes' if status_info['secondary_available'] else '❌ No'}\n"
        failover_info += f"**Failover Count:** {status_info['failover_count']}\n"
        
        if status_info['last_failover_time']:
            import datetime
            failover_time = datetime.datetime.fromtimestamp(status_info['last_failover_time'])
            failover_info += f"**Last Failover:** {failover_time.strftime('%Y-%m-%d %H:%M:%S')}"
        else:
            failover_info += f"**Last Failover:** Never"
            
        embed.add_field(
            name="🔄 Failover Status",
            value=failover_info,
            inline=False
        )
        
        # Database test details
        if db_test_result.get('details'):
            embed.add_field(
                name="🔍 Connection Details",
                value=db_test_result['details'],
                inline=False
            )
        
        # Add secondary database info if available
        if status_info['secondary_available']:
            secondary_config = current_db_config['secondary']
            embed.add_field(
                name="🔄 Secondary Database",
                value=f"**Host:** `{secondary_config['host']}`\n"
                      f"**Database:** `{secondary_config['database']}`\n"
                      f"**Status:** Ready for failover",
                inline=False
            )
        
        embed.set_footer(text=f"Checked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        embed = discord.Embed(
            title="❌ Database Status Check Failed",
            description=f"An error occurred while checking database status:\n```{str(e)}```",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)

@bot.slash_command(name="db_failover", description="Manually trigger database failover (Admin only)")
@commands.has_permissions(administrator=True)
async def database_failover(interaction: discord.Interaction):
    """Manually trigger database failover."""
    await interaction.response.defer()
    
    try:
        # Check if secondary database is available
        if not is_secondary_db_available():
            embed = discord.Embed(
                title="❌ Failover Not Available",
                description="Secondary database configuration is not available. Please configure secondary database settings.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        # Get current database before failover
        current_db_before = _current_db
        
        # Perform failover
        if perform_failover():
            # Test the new connection
            db_test_result = await test_database_connection()
            
            embed = discord.Embed(
                title="🔄 Database Failover Executed",
                description=f"Successfully switched from **{current_db_before.upper()}** to **{_current_db.upper()}** database.",
                color=discord.Color.green() if db_test_result['connected'] else discord.Color.orange()
            )
            
            # Add connection test result
            embed.add_field(
                name="🔍 New Connection Status",
                value=f"{'✅ Connected' if db_test_result['connected'] else '❌ Connection Failed'}",
                inline=False
            )
            
            if db_test_result.get('details'):
                embed.add_field(
                    name="Connection Details",
                    value=db_test_result['details'],
                    inline=False
                )
            
        else:
            embed = discord.Embed(
                title="❌ Failover Failed",
                description="Unable to perform database failover. Check console logs for details.",
                color=discord.Color.red()
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        embed = discord.Embed(
            title="❌ Failover Error",
            description=f"An error occurred during failover:\n```{str(e)}```",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)

@bot.slash_command(name="db_sync", description="Manually synchronize databases (Admin only)")
@commands.has_permissions(administrator=True)
async def database_sync(
    interaction: discord.Interaction,
    source: Option(str, "Source database", choices=["primary", "secondary"], default="primary"),
    target: Option(str, "Target database", choices=["primary", "secondary"], default="secondary"),
    sync_structure: Option(bool, "Sync table structures", default=True),
    sync_data: Option(bool, "Sync table data", default=True)
):
    """Manually synchronize databases."""
    await interaction.response.defer()
    
    try:
        # Validate inputs
        if source == target:
            embed = discord.Embed(
                title="❌ Invalid Configuration",
                description="Source and target databases cannot be the same.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        # Check if both databases are available
        if not is_secondary_db_available():
            embed = discord.Embed(
                title="❌ Secondary Database Not Available",
                description="Secondary database configuration is not available. Cannot perform sync.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
        
        # Send initial message
        embed = discord.Embed(
            title="🔄 Database Synchronization Started",
            description=f"Synchronizing from **{source.upper()}** to **{target.upper()}** database...\n"
                       f"**Structure Sync:** {'✅ Yes' if sync_structure else '❌ No'}\n"
                       f"**Data Sync:** {'✅ Yes' if sync_data else '❌ No'}\n\n"
                       f"⏳ This may take several minutes depending on database size.",
            color=discord.Color.blue()
        )
        message = await interaction.followup.send(embed=embed)
        
        # Perform synchronization
        sync_result = await sync_databases(
            source_db=source,
            target_db=target,
            sync_structure=sync_structure,
            sync_data=sync_data
        )
        
        # Create result embed
        if sync_result['success']:
            embed = discord.Embed(
                title="✅ Database Synchronization Completed",
                color=discord.Color.green()
            )
            
            # Add sync details
            duration = sync_result['end_time'] - sync_result['start_time']
            embed.add_field(
                name="📊 Sync Results",
                value=f"**Source:** {sync_result['source_db'].upper()}\n"
                      f"**Target:** {sync_result['target_db'].upper()}\n"
                      f"**Tables Synced:** {sync_result['tables_synced']}/{sync_result['total_tables']}\n"
                      f"**Duration:** {duration.total_seconds():.2f} seconds",
                inline=False
            )
            
            if sync_result['errors']:
                error_list = '\n'.join(sync_result['errors'][:5])  # Show first 5 errors
                if len(sync_result['errors']) > 5:
                    error_list += f"\n... and {len(sync_result['errors']) - 5} more errors"
                
                embed.add_field(
                    name="⚠️ Errors Encountered",
                    value=f"```{error_list}```",
                    inline=False
                )
        else:
            embed = discord.Embed(
                title="❌ Database Synchronization Failed",
                color=discord.Color.red()
            )
            
            if sync_result['errors']:
                error_list = '\n'.join(sync_result['errors'][:3])  # Show first 3 errors
                embed.add_field(
                    name="Error Details",
                    value=f"```{error_list}```",
                    inline=False
                )
            
            embed.add_field(
                name="📊 Partial Results",
                value=f"**Tables Synced:** {sync_result['tables_synced']}/{sync_result['total_tables']}",
                inline=False
            )
        
        # Update the message with results
        await message.edit(embed=embed)
        
    except Exception as e:
        embed = discord.Embed(
            title="❌ Synchronization Error",
            description=f"An error occurred during database synchronization:\n```{str(e)}```",
            color=discord.Color.red()
        )
        try:
            await interaction.followup.send(embed=embed)
        except:
            # If followup fails, try editing the original message
            try:
                await message.edit(embed=embed)
            except:
                pass
