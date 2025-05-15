from ..config import *
from googletrans import Translator

@bot.message_command(name = 'Translate to Spanish')
async def translate_spanish(ctx, message: discord.Message):
    
    translator = Translator()
    
    text = await ctx.fetch_message(message.id)

    # Send the translated message to the channel
    await ctx.respond(translator.translate(text.content, dest='es').text, ephemeral=True)