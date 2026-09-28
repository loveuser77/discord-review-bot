import discord
from discord import app_commands


def es_staff():
    """Restringe un comando a miembros con permiso de gestionar servidor."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild:
            return True
        raise app_commands.CheckFailure("No tienes permisos para usar este comando.")
    return app_commands.check(predicate)
