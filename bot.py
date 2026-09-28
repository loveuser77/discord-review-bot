import asyncio
import logging

import discord
from discord.ext import commands

import config
import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.members = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    if isinstance(error, discord.app_commands.CheckFailure):
        mensaje = str(error) or "No tienes permiso para usar este comando."
    else:
        mensaje = "Ocurrió un error al ejecutar el comando."
        log.exception("Error en comando de aplicación", exc_info=error)

    if interaction.response.is_done():
        await interaction.followup.send(mensaje, ephemeral=True)
    else:
        await interaction.response.send_message(mensaje, ephemeral=True)

COGS = [
    "cogs.tickets",
    "cogs.reviews",
    "cogs.economy",
    "cogs.admin",
]


@bot.event
async def on_ready():
    log.info(f"Sesión iniciada como {bot.user} ({bot.user.id})")

    # Registro de vistas persistentes para que los botones funcionen tras reiniciar el bot
    from cogs.tickets import PanelView, TicketView
    from cogs.reviews import VerificationView

    bot.add_view(PanelView())
    bot.add_view(TicketView())
    bot.add_view(VerificationView())

    try:
        synced = await bot.tree.sync()
        log.info(f"{len(synced)} comandos sincronizados")
    except Exception as e:
        log.error(f"Error sincronizando comandos: {e}")


async def main():
    await database.init_db()
    async with bot:
        for cog in COGS:
            await bot.load_extension(cog)
            log.info(f"Cog cargado: {cog}")
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    if not config.TOKEN:
        raise SystemExit(
            "No se encontró DISCORD_TOKEN. Crea un archivo .env a partir de .env.example."
        )
    asyncio.run(main())
