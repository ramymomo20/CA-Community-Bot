from ios_bot.config import *
import inspect

@bot.message_command(
    name="Traducir al español",
    name_localizations={
        "en-US": "Translate to Spanish",
        "es-ES": "Traducir al español"
    }
)
async def translate_spanish(ctx, message: discord.Message):
    await ctx.defer(ephemeral=True)
    translator = Translator()

    try:
        original = (message.content or "").strip()
        if not original:
            await ctx.followup.send("Error: No text found in message to translate.", ephemeral=True)
            return

        translated = translator.translate(original, dest="es")
        if inspect.isawaitable(translated):
            translated = await translated
        await ctx.followup.send(translated.text, ephemeral=True)

    except Exception as e:
        await ctx.followup.send(f"Error: translation failed. ({e})", ephemeral=True)
