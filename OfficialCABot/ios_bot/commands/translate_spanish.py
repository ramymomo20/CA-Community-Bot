from ios_bot.config import *
from ios_bot.utils.translation import translate_text

@bot.message_command(
    name="Traducir al español",
    name_localizations={
        "en-US": "Translate to Spanish",
        "es-ES": "Traducir al español"
    }
)
async def translate_spanish(ctx, message: discord.Message):
    await ctx.defer(ephemeral=True)

    original = (message.content or "").strip()
    if not original:
        await ctx.followup.send("Error: No text found in message to translate.", ephemeral=True)
        return

    try:
        translated = await translate_text(original, target="es")
        await ctx.followup.send(translated, ephemeral=True)
    except Exception as e:
        await ctx.followup.send(f"Error: translation failed. ({e})", ephemeral=True)
