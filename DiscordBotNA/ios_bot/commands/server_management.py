from ios_bot.config import *

class AddServerModal(Modal):
    def __init__(self):
        super().__init__(title="Add New Server")
        
        self.server_name = InputText(
            label="Server Name",
            placeholder="e.g., Paris, London, New York",
            required=True,
            max_length=255
        )
        
        self.server_address = InputText(
            label="Connection Info IP",
            placeholder="e.g., 87.98.129.61:27015",
            required=True,
            max_length=255
        )
        
        self.rcon_password = InputText(
            label="RCON Password",
            placeholder="e.g., BRUHHHH",
            required=True,
            max_length=255
        )
        
        self.sftp_details = InputText(
            label="Host Username",
            placeholder="e.g., root",
            required=True,
            max_length=255
        )
        
        self.host_password = InputText(
            label="Host Password (Optional)",
            placeholder="Host server password",
            required=True,
            max_length=255,
            style=discord.InputTextStyle.paragraph
        )
        
        self.add_item(self.server_name)
        self.add_item(self.server_address)
        self.add_item(self.rcon_password)
        self.add_item(self.sftp_details)
        self.add_item(self.host_password)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Check if server name already exists
        existing_server = await bot.db.servers.get_server_by_name(self.server_name.value)
        if existing_server:
            await interaction.followup.send(f"❌ A server with name '{self.server_name.value}' already exists.", ephemeral=True)
            return

        # Parse SFTP details
        sftp_ip = None
        host_username = None
        if self.sftp_details.value:
            host_username = self.sftp_details.value
            # Derive SFTP IP from server address by changing port to 8822
            try:
                if ':' in self.server_address.value:
                    server_ip, server_port = self.server_address.value.split(':', 1)
                    sftp_ip = f"{server_ip}:8822"
                else:
                    sftp_ip = f"{self.server_address.value}:8822"
            except:
                sftp_ip = f"{self.server_address.value}:8822"

        # Auto-detect server type based on username
        server_type = 'windows' if host_username and host_username.lower() == 'administrator' else 'linux'
        
        # Add the server
        success = await bot.db.servers.add_server(
            name=self.server_name.value,
            address=self.server_address.value,
            password=self.rcon_password.value,
            host_username=host_username,
            host_password=self.host_password.value if self.host_password.value else None,
            server_type=server_type,
            is_active=True
        )
        
        if success:
            embed = Embed(
                title="✅ Server Added Successfully!",
                description=f"Server '{self.server_name.value}' has been added to the database.",
                color=discord.Color.green()
            )
            embed.add_field(name="Server Name", value=self.server_name.value, inline=True)
            embed.add_field(name="Address", value=self.server_address.value, inline=True)
            embed.add_field(name="Server Type", value=f"🖥️ {server_type.title()}", inline=True)
            embed.add_field(name="SFTP IP", value=sftp_ip if sftp_ip else "Not set", inline=True)
            embed.add_field(name="Host Username", value=host_username if host_username else "Not set", inline=True)
            embed.add_field(name="RCON Password", value=f"{len(self.rcon_password.value) * '*'}", inline=True)
            
            # Add SFTP directory info if SFTP details are provided
            if sftp_ip and self.server_address.value:
                try:
                    address_parts = self.server_address.value.split(':')
                    if len(address_parts) == 2:
                        port = address_parts[1]
                        sftp_dir = f"/{sftp_ip}_{port}/iosoccer/statistics"
                        embed.add_field(name="SFTP Directory", value=f"📁 {sftp_dir}", inline=False)
                        embed.add_field(name="SFTP Port", value="8822 (Linux)", inline=True)
                except:
                    pass
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to add server. Please try again.", ephemeral=True)

class DeleteServerView(View):
    def __init__(self):
        super().__init__(timeout=180)
        
        # Get all active servers for the select menu
        self.servers = []
        
    async def setup_servers(self):
        """Setup the select menu with servers from database."""
        servers_data = await bot.db.servers.get_all_servers_with_details()
        active_servers = [s for s in servers_data if s['is_active']]
        
        if not active_servers:
            options = [SelectOption(label="No servers available", value="no_servers", disabled=True)]
        else:
            options = []
            for server in active_servers:
                options.append(SelectOption(
                    label=f"{server['name']} ({server['address']})",
                    value=str(server['id']),
                    description=f"ID: {server['id']}"
                ))
        
        server_select = Select(
            placeholder="Select a server to delete...",
            options=options,
            custom_id="delete_server_select"
        )
        server_select.callback = self.on_server_selected
        self.add_item(server_select)
        
        self.servers = active_servers

    async def on_server_selected(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        selected_server_id = int(self.children[0].values[0])
        
        # Find the server in our list
        server_to_delete = None
        for server in self.servers:
            if server['id'] == selected_server_id:
                server_to_delete = server
                break
        
        if not server_to_delete:
            await interaction.followup.send("❌ Server not found.", ephemeral=True)
            return
        
        # Delete the server (set as inactive)
        success = await bot.db.servers.delete_server(server_to_delete['id'])
        
        if success:
            embed = Embed(
                title="✅ Server Deleted Successfully!",
                description=f"Server '{server_to_delete['name']}' has been deleted from the database.",
                color=discord.Color.red()
            )
            embed.add_field(name="Server Name", value=server_to_delete['name'], inline=True)
            embed.add_field(name="Address", value=server_to_delete['address'], inline=True)
            embed.add_field(name="ID", value=str(server_to_delete['id']), inline=True)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to delete server. Please try again.", ephemeral=True)
        
        self.stop()


class EditServerModal(Modal):
    def __init__(self, server_id: int, current: dict):
        super().__init__(title="Edit Server")
        self.server_id = server_id

        # Use placeholders to show current values; fields are optional
        self.server_name = InputText(
            label="Server Name",
            placeholder=current.get('name', ''),
            required=False,
            max_length=255
        )

        self.server_address = InputText(
            label="Connection Info IP",
            placeholder=current.get('address', ''),
            required=False,
            max_length=255
        )

        self.host_username = InputText(
            label="Host Username",
            placeholder=current.get('host_username', ''),
            required=False,
            max_length=255
        )

        self.host_password = InputText(
            label="Host Password (leave blank to keep)",
            placeholder='(hidden)' if current.get('host_password') else '',
            required=False,
            style=discord.InputTextStyle.paragraph,
            max_length=2000
        )

        self.add_item(self.server_name)
        self.add_item(self.server_address)
        self.add_item(self.host_username)
        self.add_item(self.host_password)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Fetch current server to compare
        current = await bot.db.servers.get_server_by_id(self.server_id)
        if not current:
            await interaction.followup.send("❌ Server not found.", ephemeral=True)
            return

        # Prepare update kwargs only for changed fields
        update_kwargs = {}
        if self.server_name.value and self.server_name.value != current.get('name'):
            update_kwargs['name'] = self.server_name.value

        if self.server_address.value and self.server_address.value != current.get('address'):
            update_kwargs['address'] = self.server_address.value
            # Also compute sftp_ip from address
            try:
                if ':' in self.server_address.value:
                    server_ip, server_port = self.server_address.value.split(':', 1)
                    update_kwargs['sftp_ip'] = f"{server_ip}:8822"
                else:
                    update_kwargs['sftp_ip'] = f"{self.server_address.value}:8822"
            except Exception:
                update_kwargs['sftp_ip'] = f"{self.server_address.value}:8822"

        if self.host_username.value and self.host_username.value != current.get('host_username'):
            update_kwargs['host_username'] = self.host_username.value

        # Only update password if provided (blank means keep existing)
        if self.host_password.value:
            update_kwargs['host_password'] = self.host_password.value

        if not update_kwargs:
            await interaction.followup.send("No changes provided.", ephemeral=True)
            return

        success = await bot.db.servers.update_server(self.server_id, **update_kwargs)

        if success:
            embed = Embed(
                title="✅ Server Updated",
                description=f"Server '{current.get('name')}' updated successfully.",
                color=discord.Color.green()
            )
            for k, v in update_kwargs.items():
                if k == 'host_password':
                    embed.add_field(name=k, value=f"{len(v) * '*'}", inline=True)
                else:
                    embed.add_field(name=k, value=str(v), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to update server.", ephemeral=True)

        self.stop()


class EditServerView(View):
    def __init__(self):
        super().__init__(timeout=180)
        self.servers = []

    async def setup_servers(self):
        servers_data = await bot.db.servers.get_all_servers_with_details()
        active_servers = [s for s in servers_data if s['is_active']]

        if not active_servers:
            options = [SelectOption(label="No servers available", value="no_servers", disabled=True)]
        else:
            options = []
            for server in active_servers:
                options.append(SelectOption(
                    label=f"{server['name']} ({server['address']})",
                    value=str(server['id']),
                    description=f"ID: {server['id']}"
                ))

        server_select = Select(
            placeholder="Select a server to edit...",
            options=options,
            custom_id="edit_server_select"
        )
        server_select.callback = self.on_server_selected
        self.add_item(server_select)
        self.servers = active_servers

    async def on_server_selected(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        selected = int(self.children[0].values[0])
        server = None
        for s in self.servers:
            if s['id'] == selected:
                server = s
                break

        if not server:
            await interaction.followup.send("❌ Server not found.", ephemeral=True)
            return

        modal = EditServerModal(server_id=server['id'], current=server)
        await interaction.response.send_modal(modal)
        self.stop()

@bot.slash_command(
    name="edit_servers",
    description="Add, edit, or delete servers in the database (admin only)."
)
async def edit_servers(ctx, action: str = Option(description="Choose action", choices=["add", "edit", "delete"], required=True)):
    # Check if user has admin permissions
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ You need administrator permissions to use this command.", ephemeral=True)
        return

    if action == "add":
        # Show modal for adding server
        modal = AddServerModal()
        await ctx.send_modal(modal)
    elif action == "delete":
        # Show select menu for deleting server
        view = DeleteServerView()
        await view.setup_servers()
        
        embed = Embed(
            title="Delete Server",
            description="Select a server from the menu below to delete it from the database.",
            color=discord.Color.red()
        )
        
        await ctx.respond(embed=embed, view=view, ephemeral=True)
    elif action == "edit":
        # Show select menu for editing server
        view = EditServerView()
        await view.setup_servers()

        embed = Embed(
            title="Edit Server",
            description="Select a server from the menu below to edit its details.",
            color=discord.Color.blue()
        )

        await ctx.respond(embed=embed, view=view, ephemeral=True)
    else:
        await ctx.respond("❌ Invalid action. Please choose 'add' or 'delete'.", ephemeral=True) 