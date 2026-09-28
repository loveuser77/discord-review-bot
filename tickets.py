import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import database as db
from utils import es_staff
from config import COLOR_PRINCIPAL, COLOR_ERROR, COLOR_AVISO



class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Abrir ticket", style=discord.ButtonStyle.primary, custom_id="panel:abrir_ticket"
    )
    async def abrir_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        cfg = await db.get_config(guild.id)

        if cfg["tickets_paused"]:
            await interaction.response.send_message(
                "La apertura de tickets está pausada temporalmente.", ephemeral=True
            )
            return

        if not cfg["ticket_category_id"]:
            await interaction.response.send_message(
                "El sistema de tickets no está configurado todavía. Usa /config primero.",
                ephemeral=True,
            )
            return

        # Evita que un usuario tenga varios tickets abiertos a la vez
        abiertos = await db.tickets_abiertos(guild.id)
        for t in abiertos:
            if t["user_id"] == interaction.user.id:
                canal_existente = guild.get_channel(t["channel_id"])
                if canal_existente:
                    await interaction.response.send_message(
                        f"Ya tienes un ticket abierto: {canal_existente.mention}",
                        ephemeral=True,
                    )
                    return

        categoria = guild.get_channel(cfg["ticket_category_id"])
        numero = await db.next_ticket_number(guild.id)
        nombre_canal = f"ticket-{numero}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        canal = await guild.create_text_channel(
            nombre_canal, category=categoria, overwrites=overwrites
        )
        await db.crear_ticket(guild.id, canal.id, interaction.user.id)

        embed = discord.Embed(
            title=f"Ticket #{numero}",
            description=cfg["ticket_message"],
            color=COLOR_PRINCIPAL,
        )
        embed.set_footer(text=f"Abierto por {interaction.user.display_name}")

        await canal.send(
            content=interaction.user.mention, embed=embed, view=TicketView()
        )
        await interaction.response.send_message(
            f"Tu ticket fue creado: {canal.mention}", ephemeral=True
        )


class MensajeTicketModal(discord.ui.Modal, title="Editar mensaje de bienvenida"):
    texto = discord.ui.TextInput(
        label="Texto que aparece al abrir un ticket",
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    def __init__(self, texto_actual: str):
        super().__init__()
        self.texto.default = texto_actual

    async def on_submit(self, interaction: discord.Interaction):
        await db.update_config(interaction.guild_id, ticket_message=str(self.texto))
        await interaction.response.send_message(
            "El mensaje de bienvenida de los tickets fue actualizado.", ephemeral=True
        )


class MotivoRechazoModal(discord.ui.Modal, title="Motivo del rechazo"):
    motivo = discord.ui.TextInput(
        label="¿Por qué se rechaza?",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300,
    )

    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    async def on_submit(self, interaction: discord.Interaction):
        await self._callback(interaction, str(self.motivo) or "No especificado")


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Reclamar reseña",
        style=discord.ButtonStyle.success,
        custom_id="ticket:reclamar_resena",
    )
    async def reclamar_resena(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.reviews import iniciar_reclamo_resena
        await iniciar_reclamo_resena(interaction)

    @discord.ui.button(
        label="Enviar pruebas",
        style=discord.ButtonStyle.primary,
        custom_id="ticket:enviar_pruebas",
    )
    async def enviar_pruebas(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.reviews import abrir_modal_pruebas
        await abrir_modal_pruebas(interaction)

    @discord.ui.button(
        label="Reclamar ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:reclamar_ticket",
    )
    async def reclamar_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Solo el staff puede reclamar tickets.", ephemeral=True
            )
            return
        await db.reclamar_ticket(interaction.channel_id, interaction.user.id)
        await interaction.response.send_message(
            f"{interaction.user.mention} reclamó este ticket."
        )

    @discord.ui.button(
        label="Cerrar ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:cerrar_ticket",
    )
    async def cerrar_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await db.get_ticket_por_canal(interaction.channel_id)
        if ticket is None:
            await interaction.response.send_message(
                "Este canal no está registrado como ticket.", ephemeral=True
            )
            return

        es_dueño = ticket["user_id"] == interaction.user.id
        if not (es_dueño or interaction.user.guild_permissions.manage_guild):
            await interaction.response.send_message(
                "No tienes permiso para cerrar este ticket.", ephemeral=True
            )
            return

        await db.cerrar_ticket(interaction.channel_id)
        await interaction.response.send_message("Este ticket se cerrará en unos segundos.")
        await interaction.channel.edit(name=f"cerrado-{interaction.channel.name}")
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.autolimpieza.start()

    def cog_unload(self):
        self.autolimpieza.cancel()

    @app_commands.command(name="panel", description="Publica el panel de tickets en este canal")
    @es_staff()
    async def panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Sistema de reseñas",
            description="Pulsa el botón para abrir un ticket y comenzar.",
            color=COLOR_PRINCIPAL,
        )
        await interaction.channel.send(embed=embed, view=PanelView())
        await db.update_config(interaction.guild_id, panel_channel_id=interaction.channel_id)
        await interaction.response.send_message("Panel publicado.", ephemeral=True)

    @app_commands.command(
        name="mensaje-ticket", description="Edita el texto que aparece al abrir un ticket"
    )
    @es_staff()
    async def mensaje_ticket(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        await interaction.response.send_modal(MensajeTicketModal(cfg["ticket_message"]))

    @app_commands.command(name="pausar-tickets", description="Activa o desactiva la apertura de tickets")
    @es_staff()
    async def pausar_tickets(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        nuevo_estado = 0 if cfg["tickets_paused"] else 1
        await db.update_config(interaction.guild_id, tickets_paused=nuevo_estado)
        estado_texto = "pausada" if nuevo_estado else "reanudada"
        await interaction.response.send_message(
            f"La apertura de tickets fue {estado_texto}.", ephemeral=True
        )

    @tasks.loop(minutes=30)
    async def autolimpieza(self):
        # Cierra automáticamente tickets abiertos sin actividad por más de 48 horas
        for guild in self.bot.guilds:
            abiertos = await db.tickets_abiertos(guild.id)
            for t in abiertos:
                canal = guild.get_channel(t["channel_id"])
                if canal is None:
                    continue
                try:
                    ultimo_mensaje = [m async for m in canal.history(limit=1)]
                except discord.Forbidden:
                    continue
                if not ultimo_mensaje:
                    continue
                inactivo_desde = discord.utils.utcnow() - ultimo_mensaje[0].created_at
                if inactivo_desde > datetime.timedelta(hours=48):
                    await db.cerrar_ticket(canal.id)
                    try:
                        await canal.send(
                            embed=discord.Embed(
                                description="Este ticket se cerró automáticamente por inactividad.",
                                color=COLOR_AVISO,
                            )
                        )
                        await canal.edit(name=f"cerrado-{canal.name}")
                    except discord.HTTPException:
                        pass

    @autolimpieza.before_loop
    async def antes_autolimpieza(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
