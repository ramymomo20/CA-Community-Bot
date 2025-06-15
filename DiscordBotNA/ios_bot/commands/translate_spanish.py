from ios_bot.config import *

@bot.message_command(
    name="Traducir al español",
    name_localizations={
        "en-US": "Translate to Spanish",
        "es-ES": "Traducir al español"
    }
)
async def translate_spanish(ctx: discord.ApplicationContext, message: discord.Message):
    translator = Translator()

    try:
        original = message.content or ""
        # await the translate coroutine
        translated = await translator.translate(original, dest="es")
        await ctx.respond(translated.text, ephemeral=True)

    except Exception:
        await ctx.respond("Error: solo puedes traducir texto.", ephemeral=True)