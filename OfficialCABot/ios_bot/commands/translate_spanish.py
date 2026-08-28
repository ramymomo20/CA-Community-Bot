from ios_bot.config import *

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
        translated = await asyncio.to_thread(
            lambda: GoogleTranslator(source="auto", target="es").translate(original)
        )
        await ctx.followup.send(translated, ephemeral=True)
    except Exception as e:
        await ctx.followup.send(f"Error: translation failed. ({e})", ephemeral=True)
