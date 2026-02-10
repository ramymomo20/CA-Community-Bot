from ios_bot.config import *
import inspect

@bot.message_command(
    name="Translate to English",
    name_localizations={
        "en-US": "Translate to English",
        "es-ES": "Traducir al Inglés"
    }
)
async def translate_english(ctx, message: discord.Message):
    await ctx.defer(ephemeral=True)
    translator = Translator()

    try:
        original = (message.content or "").strip()
        if not original:
            await ctx.followup.send("Error: No text found in message to translate.", ephemeral=True)
            return

        # Support both sync and async translate implementations
        translated = translator.translate(original, dest="en")
        if inspect.isawaitable(translated):
            translated = await translated
        await ctx.followup.send(translated.text, ephemeral=True)

    except Exception as e:
        await ctx.followup.send(f"Error: translation failed. ({e})", ephemeral=True)
