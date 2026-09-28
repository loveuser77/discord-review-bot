import discord
from discord import app_commands
from discord.ext import commands

import database as db
from utils import es_staff
from config import COLOR_INFO, COLOR_EXITO



class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="saldo", description="Muestra tu saldo actual")
    async def saldo(self, interaction: discord.Interaction):
        balance = await db.get_balance(interaction.guild_id, interaction.user.id)
        metodo = await db.get_metodo_pago(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(title="Tu saldo", color=COLOR_INFO)
        embed.add_field(name="Saldo disponible", value=str(balance))
        embed.add_field(name="Método de pago", value=metodo or "No configurado")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="metodo-pago", description="Configura cómo quieres recibir tus pagos")
    @app_commands.describe(metodo="Ejemplo: PayPal, transferencia, USDT...")
    async def metodo_pago(self, interaction: discord.Interaction, metodo: str):
        await db.set_metodo_pago(interaction.guild_id, interaction.user.id, metodo)
        await interaction.response.send_message(
            f"Tu método de pago quedó como: {metodo}", ephemeral=True
        )

    @app_commands.command(name="addsaldo", description="Agrega saldo a un usuario")
    @app_commands.describe(usuario="Usuario", cantidad="Cantidad a agregar")
    @es_staff()
    async def addsaldo(self, interaction: discord.Interaction, usuario: discord.Member, cantidad: float):
        await db.sumar_balance(interaction.guild_id, usuario.id, cantidad)
        await interaction.response.send_message(
            f"Se agregaron {cantidad} al saldo de {usuario.mention}.", ephemeral=True
        )

    @app_commands.command(name="quitarsaldo", description="Quita saldo a un usuario")
    @app_commands.describe(usuario="Usuario", cantidad="Cantidad a quitar")
    @es_staff()
    async def quitarsaldo(self, interaction: discord.Interaction, usuario: discord.Member, cantidad: float):
        await db.sumar_balance(interaction.guild_id, usuario.id, -cantidad)
        await interaction.response.send_message(
            f"Se quitaron {cantidad} del saldo de {usuario.mention}.", ephemeral=True
        )

    @app_commands.command(name="pagar", description="Marca el saldo de un usuario como pagado")
    @app_commands.describe(usuario="Usuario a pagar")
    @es_staff()
    async def pagar(self, interaction: discord.Interaction, usuario: discord.Member):
        balance = await db.get_balance(interaction.guild_id, usuario.id)
        if balance <= 0:
            await interaction.response.send_message(
                f"{usuario.mention} no tiene saldo pendiente.", ephemeral=True
            )
            return
        await db.set_balance(interaction.guild_id, usuario.id, 0)
        embed = discord.Embed(
            title="Pago registrado",
            description=f"Se marcó como pagado un saldo de {balance} a {usuario.mention}.",
            color=COLOR_EXITO,
        )
        await interaction.response.send_message(embed=embed)
        try:
            await usuario.send(f"Se te pagó un saldo de {balance}. ¡Gracias por tu trabajo!")
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
