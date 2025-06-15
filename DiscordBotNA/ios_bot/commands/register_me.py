from ios_bot.config import *
from ios_bot.database_manager import get_player_by_steam_id, register_player

# SteamID format validation
steam_id_regex = re.compile(r"^STEAM_[0-1]:[0-1]:\d{1,10}$")


class SteamIDModal(Modal):
    def __init__(self, *args, **kwargs):
        super().__init__(
            InputText(
                label="SteamID",
                placeholder="e.g., STEAM_0:1:12345678",
                required=True,
                min_length=17,
                max_length=20
            ),
            *args,
            **kwargs,
        )
        #self.add_item(self.steam_id_input)

    async def callback(self, interaction: discord.Interaction):
        # Defer the response immediately to avoid timeouts. The response is ephemeral (visible only to the user).
        #await interaction.response.defer(ephemeral=True)
        steam_id = self.children[0].value
        print(steam_id)

        # Validate SteamID format
        if not steam_id_regex.match(steam_id):
            await interaction.response.send_message(
                "Invalid SteamID format. Please use the format `STEAM_X:Y:Z` (e.g., `STEAM_0:1:12345678`).",
                ephemeral=True
            )
            return

        try:
            # Check if the SteamID is already registered to another user
            existing_player = await get_player_by_steam_id(steam_id)
            if (
                existing_player
                and existing_player["discord_id"] != interaction.user.id
            ):
                await interaction.response.send_message(
                    f"The SteamID `{steam_id}` is already registered to another user. Please provide a different one.",
                    ephemeral=True
                )
                return

            # Proceed with registration or update
            await register_player(
                discord_id=interaction.user.id,
                username=interaction.user.display_name,
                steam_id=steam_id,
            )

            message = f"Thank you {interaction.user.mention}! Your registration has been updated with SteamID `{steam_id}`."
            # Use followup.send because we have already deferred the response.
            await interaction.response.send_message(message, ephemeral=True)

        except Exception as e:
            print(f"Error during player registration: {e}")
            await interaction.response.send_message(
                "An error occurred while trying to register your SteamID. Please try again later.",
                ephemeral=True
            )


@bot.slash_command(
    name="player_register", description="Register your SteamID with the league."
)
async def register_me(ctx: ApplicationContext):
    """Shows a modal to register the user's SteamID."""
    modal = SteamIDModal(title="Register your steamID to your account")
    await ctx.send_modal(modal) 