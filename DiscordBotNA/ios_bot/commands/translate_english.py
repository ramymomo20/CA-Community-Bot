from ios_bot.config import *

@bot.message_command(
    name="Translate to English",
    name_localizations={
        "en-US": "Translate to English",
        "es-ES": "Traducir al Inglés"
    }
)
async def translate_english(ctx: discord.ApplicationContext, message: discord.Message):
    translator = Translator()

    try:
        original = message.content or ""
        # await the translate coroutine
        translated = await translator.translate(original, dest="en")
        await ctx.respond(translated.text, ephemeral=True)

    except Exception as e:
        await ctx.respond(f"Error: {e}", ephemeral=True)