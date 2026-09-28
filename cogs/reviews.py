import csv
import datetime
import io

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from utils import es_staff
from config import COLOR_EXITO, COLOR_ERROR, COLOR_INFO, COLOR_AVISO, COLOR_PRINCIPAL



ESTADOS_LEGIBLES = {
    "disponible": "Disponible",
    "reclamada": "Reclamada, pendiente de pruebas",
    "pendiente_verificacion": "En verificación",
    "aprobada": "Aprobada",
}


async def iniciar_reclamo_resena(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user = interaction.user

    if await db.esta_en_blacklist(guild_id, user.id):
        await interaction.response.send_message(
            "No puedes reclamar reseñas: estás en la lista negra.", ephemeral=True
        )
        return

    activa = await db.reseña_activa_de_usuario(guild_id, user.id)
    if activa is not None:
        await interaction.response.send_message(
            "Ya tienes una reseña reclamada pendiente. Termínala antes de pedir otra.",
            ephemeral=True,
        )
        return

    review = await db.reclamar_review(guild_id, user.id, interaction.channel_id)
    if review is None:
        await interaction.response.send_message(
            "No hay reseñas disponibles en este momento. Inténtalo más tarde.",
            ephemeral=True,
        )
        return

    await db.vincular_review_a_ticket(interaction.channel_id, review["id"])
    plantilla = await db.get_plantilla(review["plantilla_id"]) if review["plantilla_id"] else None
    instrucciones = plantilla["instrucciones"] if plantilla else "Sigue las indicaciones del staff para esta reseña."
    valor = plantilla["valor"] if plantilla else 0

    embed = discord.Embed(
        title=f"Reseña asignada #{review['id']}",
        description=instrucciones,
        color=COLOR_INFO,
    )
    embed.add_field(name="Enlace de la reseña", value=review["link"] or "No disponible", inline=False)
    embed.add_field(name="Recompensa", value=f"{valor}", inline=True)
    embed.set_footer(text="Cuando termines, usa el botón de Enviar pruebas.")

    await interaction.response.send_message(embed=embed)

    # Avisa si el stock quedó bajo tras esta asignación
    admin = interaction.client.get_cog("Admin")
    if admin is not None:
        await admin._revisar_aviso_stock(guild_id, interaction.guild)


class EnviarPruebasModal(discord.ui.Modal, title="Enviar pruebas"):
    link = discord.ui.TextInput(
        label="Enlace de la prueba",
        placeholder="https://...",
        max_length=500,
    )

    def __init__(self, review_id: int):
        super().__init__()
        self.review_id = review_id

    async def on_submit(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg["verification_channel_id"]:
            await interaction.response.send_message(
                "El canal de verificación no está configurado. Avisa al staff.",
                ephemeral=True,
            )
            return

        await db.enviar_prueba(self.review_id, str(self.link))
        canal_verificacion = interaction.guild.get_channel(cfg["verification_channel_id"])

        review = await db.get_review(self.review_id)
        plantilla = await db.get_plantilla(review["plantilla_id"]) if review["plantilla_id"] else None

        embed = discord.Embed(
            title=f"Verificación de reseña #{review['id']}",
            color=COLOR_AVISO,
        )
        embed.add_field(name="Usuario", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="Plantilla", value=plantilla["nombre"] if plantilla else "N/A", inline=True)
        embed.add_field(name="Ticket", value=interaction.channel.mention, inline=True)
        embed.add_field(name="Reseña asignada", value=review["link"] or "N/A", inline=False)
        embed.add_field(name="Prueba enviada", value=str(self.link), inline=False)

        if canal_verificacion:
            await canal_verificacion.send(embed=embed, view=VerificationView())

        await interaction.response.send_message(
            "Tus pruebas fueron enviadas. Espera la verificación del staff.", ephemeral=True
        )


async def abrir_modal_pruebas(interaction: discord.Interaction):
    review = await db.reseña_activa_de_usuario(interaction.guild_id, interaction.user.id)
    if review is None or review["estado"] != "reclamada":
        await interaction.response.send_message(
            "No tienes ninguna reseña reclamada esperando pruebas.", ephemeral=True
        )
        return
    await interaction.response.send_modal(EnviarPruebasModal(review["id"]))


class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Aprobar", style=discord.ButtonStyle.success, custom_id="verif:aprobar")
    async def aprobar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        review_id = _extraer_review_id(interaction.message.embeds[0].title)
        review = await db.get_review(review_id)
        if review is None or review["estado"] != "pendiente_verificacion":
            await interaction.response.send_message(
                "Esta reseña ya fue procesada.", ephemeral=True
            )
            return

        await db.aprobar_review(review_id, interaction.user.id)
        plantilla = await db.get_plantilla(review["plantilla_id"]) if review["plantilla_id"] else None
        valor = plantilla["valor"] if plantilla else 0
        await db.sumar_balance(interaction.guild_id, review["claimed_by"], valor)

        for item in self.children:
            item.disabled = True
        embed = interaction.message.embeds[0]
        embed.color = COLOR_EXITO
        embed.add_field(name="Resultado", value=f"Aprobada por {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

        usuario = interaction.guild.get_member(review["claimed_by"])
        if usuario:
            try:
                dm = discord.Embed(
                    title="Reseña aprobada",
                    description=f"Tu reseña #{review_id} fue aprobada. Saldo actualizado: {valor}.",
                    color=COLOR_EXITO,
                )
                await usuario.send(embed=dm)
            except discord.Forbidden:
                pass

    @discord.ui.button(label="Rechazar", style=discord.ButtonStyle.danger, custom_id="verif:rechazar")
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("No tienes permiso.", ephemeral=True)
            return

        review_id = _extraer_review_id(interaction.message.embeds[0].title)

        async def al_confirmar(modal_interaction: discord.Interaction, motivo: str):
            review = await db.get_review(review_id)
            if review is None or review["estado"] != "pendiente_verificacion":
                await modal_interaction.response.send_message(
                    "Esta reseña ya fue procesada.", ephemeral=True
                )
                return

            usuario_id = review["claimed_by"]
            await db.rechazar_review(review_id, modal_interaction.user.id, motivo)

            for item in self.children:
                item.disabled = True
            embed = interaction.message.embeds[0]
            embed.color = COLOR_ERROR
            embed.add_field(
                name="Resultado",
                value=f"Rechazada por {modal_interaction.user.mention}. Motivo: {motivo}",
                inline=False,
            )
            await interaction.message.edit(embed=embed, view=self)
            await modal_interaction.response.send_message("Reseña rechazada.", ephemeral=True)

            usuario = interaction.guild.get_member(usuario_id)
            if usuario:
                try:
                    dm = discord.Embed(
                        title="Reseña no aprobada",
                        description=(
                            f"Tu reseña #{review_id} no cumplió con los requisitos. "
                            "La reseña volvió a quedar disponible en el stock."
                        ),
                        color=COLOR_ERROR,
                    )
                    if motivo:
                        dm.add_field(name="Motivo", value=motivo, inline=False)
                    await usuario.send(embed=dm)
                except discord.Forbidden:
                    pass

        from cogs.tickets import MotivoRechazoModal
        await interaction.response.send_modal(MotivoRechazoModal(al_confirmar))


def _extraer_review_id(titulo: str) -> int:
    # El título tiene el formato "Verificación de reseña #<id>"
    return int(titulo.split("#")[-1].strip())


class Reviews(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="mis-reseñas", description="Ver tus reseñas y su estado")
    async def mis_resenas(self, interaction: discord.Interaction):
        reseñas = await db.reseñas_de_usuario(interaction.guild_id, interaction.user.id)
        if not reseñas:
            await interaction.response.send_message("No tienes reseñas registradas.", ephemeral=True)
            return

        embed = discord.Embed(title="Tus reseñas", color=COLOR_INFO)
        for r in reseñas[:20]:
            estado = ESTADOS_LEGIBLES.get(r["estado"], r["estado"])
            embed.add_field(
                name=f"Reseña #{r['id']} - {r['plantilla_nombre'] or 'N/A'}",
                value=f"Estado: {estado}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reseña-info", description="Ver el detalle completo de una reseña")
    @app_commands.describe(id="ID de la reseña")
    @es_staff()
    async def resena_info(self, interaction: discord.Interaction, id: int):
        review = await db.get_review(id)
        if review is None:
            await interaction.response.send_message("No existe esa reseña.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Reseña #{id}", color=COLOR_INFO)
        embed.add_field(name="Estado", value=ESTADOS_LEGIBLES.get(review["estado"], review["estado"]))
        embed.add_field(name="Reclamada por", value=f"<@{review['claimed_by']}>" if review["claimed_by"] else "Nadie")
        embed.add_field(name="Reseña asignada", value=review["link"] or "Ninguno", inline=False)
        embed.add_field(name="Enlace de prueba", value=review["proof_link"] or "Ninguno", inline=False)
        if review["decided_by"]:
            embed.add_field(name="Decidida por", value=f"<@{review['decided_by']}>")
        if review["reject_reason"]:
            embed.add_field(name="Motivo de rechazo", value=review["reject_reason"], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="eliminar-reseña", description="Elimina del historial una reseña aprobada"
    )
    @app_commands.describe(id="ID de la reseña aprobada a eliminar")
    @es_staff()
    async def eliminar_resena(self, interaction: discord.Interaction, id: int):
        review = await db.get_review(id)
        if review is None or review["estado"] != "aprobada":
            await interaction.response.send_message(
                "Esa reseña no existe o no está aprobada.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Confirmar eliminación de la reseña #{id}",
            description=(
                f"Reseña asignada:\n{review['link']}\n\n"
                f"Prueba enviada:\n{review['proof_link']}"
            ),
            color=COLOR_AVISO,
        )

        view = ConfirmarEliminarView(id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="top", description="Ranking de usuarios con más reseñas aprobadas")
    async def top(self, interaction: discord.Interaction):
        filas = await db.top_usuarios(interaction.guild_id)
        if not filas:
            await interaction.response.send_message("Todavía no hay datos.", ephemeral=True)
            return
        embed = discord.Embed(title="Top de reseñas aprobadas", color=COLOR_PRINCIPAL)
        texto = "\n".join(
            f"{i+1}. <@{f['user_id']}> — {f['aprobadas']}" for i, f in enumerate(filas)
        )
        embed.description = texto
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="reportar", description="Reporta un problema con una reseña")
    @app_commands.describe(id="ID de la reseña", motivo="Describe el problema")
    async def reportar(self, interaction: discord.Interaction, id: int, motivo: str):
        review = await db.get_review(id)
        if review is None or review["claimed_by"] != interaction.user.id:
            await interaction.response.send_message(
                "No tienes una reseña con ese ID.", ephemeral=True
            )
            return

        await db.crear_reporte(interaction.guild_id, id, interaction.user.id, motivo)
        cfg = await db.get_config(interaction.guild_id)
        canal_logs = interaction.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
        if canal_logs:
            embed = discord.Embed(title="Nuevo reporte", color=COLOR_AVISO)
            embed.add_field(name="Reseña", value=f"#{id}")
            embed.add_field(name="Usuario", value=interaction.user.mention)
            embed.add_field(name="Motivo", value=motivo, inline=False)
            await canal_logs.send(embed=embed)

        await interaction.response.send_message("Tu reporte fue enviado al staff.", ephemeral=True)

    @app_commands.command(
        name="tiempo-restante", description="Muestra cuánto tiempo llevas con tu reseña reclamada"
    )
    async def tiempo_restante(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        review = await db.reseña_activa_de_usuario(interaction.guild_id, interaction.user.id)
        if review is None:
            await interaction.response.send_message(
                "No tienes ninguna reseña reclamada actualmente.", ephemeral=True
            )
            return

        claimed_at = datetime.datetime.fromisoformat(review["claimed_at"])
        limite = claimed_at + datetime.timedelta(hours=cfg["cooldown_horas"])
        restante = limite - datetime.datetime.utcnow()

        if restante.total_seconds() <= 0:
            await interaction.response.send_message(
                "El tiempo para esta reseña ya se agotó. El staff puede liberarla.", ephemeral=True
            )
            return

        horas = int(restante.total_seconds() // 3600)
        minutos = int((restante.total_seconds() % 3600) // 60)
        await interaction.response.send_message(
            f"Te quedan {horas}h {minutos}m para enviar las pruebas.", ephemeral=True
        )

    @app_commands.command(
        name="reset-cooldown", description="Libera la reseña reclamada de un usuario"
    )
    @app_commands.describe(usuario="Usuario a liberar")
    @es_staff()
    async def reset_cooldown(self, interaction: discord.Interaction, usuario: discord.Member):
        review = await db.reseña_activa_de_usuario(interaction.guild_id, usuario.id)
        if review is None:
            await interaction.response.send_message(
                "Ese usuario no tiene ninguna reseña reclamada.", ephemeral=True
            )
            return
        await db.rechazar_review(review["id"], interaction.user.id, "Liberada manualmente por staff")
        await interaction.response.send_message(
            f"Se liberó la reseña #{review['id']} de {usuario.mention}.", ephemeral=True
        )

    @app_commands.command(
        name="recordatorio", description="Recuerda a un usuario que debe enviar sus pruebas"
    )
    @app_commands.describe(usuario="Usuario a recordar")
    @es_staff()
    async def recordatorio(self, interaction: discord.Interaction, usuario: discord.Member):
        review = await db.reseña_activa_de_usuario(interaction.guild_id, usuario.id)
        if review is None or review["estado"] != "reclamada":
            await interaction.response.send_message(
                "Ese usuario no tiene una reseña pendiente de pruebas.", ephemeral=True
            )
            return

        canal = interaction.guild.get_channel(review["ticket_channel_id"])
        if canal:
            await canal.send(
                f"{usuario.mention}, recuerda enviar las pruebas de tu reseña reclamada."
            )
        try:
            await usuario.send(
                "Recuerda enviar las pruebas de tu reseña reclamada antes de que expire el tiempo."
            )
        except discord.Forbidden:
            pass
        await interaction.response.send_message("Recordatorio enviado.", ephemeral=True)

    @app_commands.command(name="exportar", description="Exporta el historial de reseñas en CSV")
    @es_staff()
    async def exportar(self, interaction: discord.Interaction):
        filas = await db.todas_las_reseñas(interaction.guild_id)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["id", "link", "estado", "claimed_by", "claimed_at", "proof_link", "decided_by", "decided_at"]
        )
        for r in filas:
            writer.writerow(
                [r["id"], r["link"], r["estado"], r["claimed_by"], r["claimed_at"], r["proof_link"], r["decided_by"], r["decided_at"]]
            )
        buffer.seek(0)
        archivo = discord.File(io.BytesIO(buffer.getvalue().encode()), filename="reseñas.csv")
        await interaction.response.send_message("Aquí tienes el historial completo.", file=archivo, ephemeral=True)


class ConfirmarEliminarView(discord.ui.View):
    def __init__(self, review_id: int):
        super().__init__(timeout=60)
        self.review_id = review_id

    @discord.ui.button(label="Confirmar eliminación", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.eliminar_review(self.review_id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"La reseña #{self.review_id} fue eliminada del historial.", embed=None, view=self
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Eliminación cancelada.", embed=None, view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reviews(bot))
