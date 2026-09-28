import re

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from utils import es_staff
from config import COLOR_INFO, COLOR_PRINCIPAL, COLOR_AVISO



URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


class RestockModal(discord.ui.Modal, title="Restock de reseñas"):
    mensaje = discord.ui.TextInput(
        label="Mensaje de restock",
        style=discord.TextStyle.paragraph,
        placeholder="Pega aquí los enlaces, uno por línea",
        max_length=4000,
    )

    def __init__(self, cog, plantilla):
        super().__init__()
        self.cog = cog
        self.plantilla = plantilla

    async def on_submit(self, interaction: discord.Interaction):
        # Extrae todos los enlaces que aparezcan en el mensaje
        links = [l.rstrip(".,;)") for l in URL_REGEX.findall(str(self.mensaje))]
        if not links:
            await interaction.response.send_message(
                "No encontré ningún enlace en el mensaje.", ephemeral=True
            )
            return

        agregados = await db.agregar_stock(interaction.guild_id, self.plantilla["id"], links)
        repetidos = len(set(links)) - agregados

        embed = discord.Embed(title="Restock realizado", color=COLOR_INFO)
        embed.add_field(name="Plantilla", value=self.plantilla["nombre"])
        embed.add_field(name="Reseñas agregadas", value=str(agregados))
        if repetidos > 0:
            embed.add_field(name="Omitidas por estar repetidas", value=str(repetidos))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="config", description="Configura los canales y la categoría del sistema")
    @app_commands.describe(
        categoria_tickets="Categoría donde se crearán los tickets",
        canal_verificacion="Canal donde se verifican las pruebas enviadas",
        canal_logs="Canal donde se registran reportes y eventos",
        cooldown_horas="Horas antes de que una reseña reclamada pueda considerarse vencida",
    )
    @es_staff()
    async def config(
        self,
        interaction: discord.Interaction,
        categoria_tickets: discord.CategoryChannel = None,
        canal_verificacion: discord.TextChannel = None,
        canal_logs: discord.TextChannel = None,
        cooldown_horas: int = None,
    ):
        cambios = {}
        if categoria_tickets:
            cambios["ticket_category_id"] = categoria_tickets.id
        if canal_verificacion:
            cambios["verification_channel_id"] = canal_verificacion.id
        if canal_logs:
            cambios["log_channel_id"] = canal_logs.id
        if cooldown_horas is not None:
            cambios["cooldown_horas"] = cooldown_horas

        if not cambios:
            cfg = await db.get_config(interaction.guild_id)
            embed = discord.Embed(title="Configuración actual", color=COLOR_INFO)
            embed.add_field(name="Categoría de tickets", value=f"<#{cfg['ticket_category_id']}>" if cfg["ticket_category_id"] else "No configurada")
            embed.add_field(name="Canal de verificación", value=f"<#{cfg['verification_channel_id']}>" if cfg["verification_channel_id"] else "No configurado")
            embed.add_field(name="Canal de logs", value=f"<#{cfg['log_channel_id']}>" if cfg["log_channel_id"] else "No configurado")
            embed.add_field(name="Cooldown", value=f"{cfg['cooldown_horas']} horas")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await db.update_config(interaction.guild_id, **cambios)
        await interaction.response.send_message("Configuración actualizada.", ephemeral=True)

    @app_commands.command(name="plantilla-reseña", description="Crea una plantilla de reseña reutilizable")
    @app_commands.describe(
        nombre="Nombre corto de la plantilla",
        instrucciones="Instrucciones que verá el usuario",
        valor="Cuánto saldo otorga al aprobarse",
    )
    @es_staff()
    async def plantilla_resena(
        self, interaction: discord.Interaction, nombre: str, instrucciones: str, valor: float
    ):
        plantilla_id = await db.crear_plantilla(interaction.guild_id, nombre, instrucciones, valor)
        await interaction.response.send_message(
            f"Plantilla '{nombre}' creada con ID {plantilla_id}.", ephemeral=True
        )

    @app_commands.command(
        name="restock-panel", description="Abre el panel para cargar reseñas al stock"
    )
    @app_commands.describe(plantilla_id="ID de la plantilla (opcional, por defecto la más reciente)")
    @es_staff()
    async def restock_panel(self, interaction: discord.Interaction, plantilla_id: int = None):
        if plantilla_id is not None:
            plantilla = await db.get_plantilla(plantilla_id)
        else:
            plantilla = await db.ultima_plantilla(interaction.guild_id)

        if plantilla is None:
            await interaction.response.send_message(
                "Primero crea una plantilla con /plantilla-reseña (nombre, instrucciones y valor).",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(RestockModal(self, plantilla))

    @app_commands.command(name="plantillas", description="Lista las plantillas de reseña disponibles")
    @es_staff()
    async def plantillas(self, interaction: discord.Interaction):
        filas = await db.listar_plantillas(interaction.guild_id)
        if not filas:
            await interaction.response.send_message("No hay plantillas creadas todavía.", ephemeral=True)
            return
        embed = discord.Embed(title="Plantillas de reseña", color=COLOR_INFO)
        for p in filas:
            embed.add_field(name=f"#{p['id']} - {p['nombre']}", value=f"Valor: {p['valor']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="stock", description="Muestra cuántas reseñas quedan disponibles")
    async def stock(self, interaction: discord.Interaction):
        filas = await db.contar_stock(interaction.guild_id)
        total = await db.total_disponibles(interaction.guild_id)
        embed = discord.Embed(title="Stock disponible", color=COLOR_PRINCIPAL)
        if not filas:
            embed.description = "No hay reseñas disponibles en este momento."
        else:
            for f in filas:
                embed.add_field(name=f["nombre"], value=str(f["cantidad"]), inline=True)
            embed.set_footer(text=f"Total disponible: {total}")
        await interaction.response.send_message(embed=embed)

    async def _revisar_aviso_stock(self, guild_id: int, guild: discord.Guild):
        cfg = await db.get_config(guild_id)
        total = await db.total_disponibles(guild_id)
        if total <= cfg["stock_alert_threshold"] and cfg["log_channel_id"]:
            canal = guild.get_channel(cfg["log_channel_id"])
            if canal:
                await canal.send(
                    embed=discord.Embed(
                        title="Stock bajo",
                        description=f"Quedan {total} reseñas disponibles en total.",
                        color=COLOR_AVISO,
                    )
                )

    @app_commands.command(name="blacklist", description="Agrega o quita a un usuario de la lista negra")
    @app_commands.describe(usuario="Usuario", razon="Motivo (déjalo vacío para quitarlo de la lista)")
    @es_staff()
    async def blacklist(self, interaction: discord.Interaction, usuario: discord.Member, razon: str = None):
        if razon:
            await db.agregar_blacklist(interaction.guild_id, usuario.id, razon, interaction.user.id)
            await interaction.response.send_message(
                f"{usuario.mention} fue agregado a la lista negra. Motivo: {razon}", ephemeral=True
            )
        else:
            await db.quitar_blacklist(interaction.guild_id, usuario.id)
            await interaction.response.send_message(
                f"{usuario.mention} fue quitado de la lista negra.", ephemeral=True
            )

    @app_commands.command(name="staff-stats", description="Muestra cuántas reseñas ha gestionado cada miembro del staff")
    @es_staff()
    async def staff_stats(self, interaction: discord.Interaction):
        filas = await db.staff_stats(interaction.guild_id)
        if not filas:
            await interaction.response.send_message("Todavía no hay datos.", ephemeral=True)
            return
        embed = discord.Embed(title="Estadísticas del staff", color=COLOR_INFO)
        for f in filas:
            embed.add_field(
                name=f"<@{f['staff_id']}>",
                value=f"Aprobadas: {f['aprobadas'] or 0} | Rechazadas: {f['rechazadas'] or 0}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
