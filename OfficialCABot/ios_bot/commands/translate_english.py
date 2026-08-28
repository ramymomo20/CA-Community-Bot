from ios_bot.config import *
from ios_bot.utils.translation import translate_text

@bot.message_command(
    name="Translate to English",
    name_localizations={
        "en-US": "Translate to English",
        "es-ES": "Traducir al Inglés"
    }
)
async def translate_english(ctx, message: discord.Message):
    await ctx.defer(ephemeral=True)

    original = (message.content or "").strip()
    if not original:
        await ctx.followup.send("Error: No text found in message to translate.", ephemeral=True)
        return

    try:
        translated = await translate_text(original, target="en")
        await ctx.followup.send(translated, ephemeral=True)
    except Exception as e:
        await ctx.followup.send(f"Error: translation failed. ({e})", ephemeral=True)
