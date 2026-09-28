# Bot de reseñas para Discord

## Instalación

1. Instala Python 3.10 o superior.
2. Dentro de la carpeta del proyecto, instala las dependencias:
   ```
   pip install -r requirements.txt
   ```
3. Copia `.env.example` como `.env` y pega el token de tu bot:
   ```
   DISCORD_TOKEN=tu_token_aqui
   ```
4. Ejecuta el bot:
   ```
   python bot.py
   ```

El bot necesita el permiso de intent **Server Members Intent** activado en el
Portal de Desarrolladores de Discord, y debe invitarse con permisos de
Administrador (o al menos: gestionar canales, gestionar roles, enviar
mensajes, insertar enlaces, adjuntar archivos).

## Primeros pasos dentro del servidor

1. `/config` — define la categoría de tickets, el canal de verificación y el
   canal de logs.
2. `/plantilla-reseña` — crea al menos una plantilla de reseña (nombre,
   instrucciones, valor en saldo que otorga).
3. `/restock` — agrega unidades de esa plantilla al stock disponible.
4. `/panel` — publica el panel de tickets en el canal que elijas.
5. `/mensaje-ticket` — opcional, para personalizar el texto que ve cada
   usuario al abrir un ticket.

## Lista de comandos

| Comando | Quién lo usa | Qué hace |
|---|---|---|
| `/panel` | Staff | Publica el panel con el botón de abrir ticket |
| `/mensaje-ticket` | Staff | Edita el texto de bienvenida de los tickets |
| `/pausar-tickets` | Staff | Activa/desactiva la apertura de nuevos tickets |
| `/config` | Staff | Configura categoría, canales y cooldown |
| `/plantilla-reseña` | Staff | Crea un tipo de reseña reutilizable |
| `/plantillas` | Staff | Lista las plantillas creadas |
| `/restock` | Staff | Agrega unidades de una plantilla al stock |
| `/stock` | Todos | Muestra cuántas reseñas quedan disponibles |
| `/blacklist` | Staff | Agrega o quita a alguien de la lista negra |
| `/staff-stats` | Staff | Aprobadas/rechazadas gestionadas por cada staff |
| `/saldo` | Todos | Muestra tu saldo y método de pago |
| `/metodo-pago` | Todos | Configura cómo quieres que te paguen |
| `/addsaldo` `/quitarsaldo` | Staff | Ajusta el saldo de un usuario |
| `/pagar` | Staff | Marca el saldo de un usuario como pagado y lo resetea |
| `/mis-reseñas` | Todos | Ver tus propias reseñas y su estado |
| `/reseña-info` | Staff | Detalle completo de una reseña por ID |
| `/eliminar-reseña` | Staff | Borra del historial una reseña aprobada (pide confirmación) |
| `/top` | Todos | Ranking de usuarios con más reseñas aprobadas |
| `/reportar` | Todos | Reporta un problema con una reseña propia |
| `/tiempo-restante` | Todos | Cuánto tiempo te queda para enviar pruebas |
| `/reset-cooldown` | Staff | Libera manualmente la reseña reclamada de alguien |
| `/recordatorio` | Staff | Le recuerda a alguien enviar sus pruebas |
| `/exportar` | Staff | Descarga un CSV con todo el historial |

## Flujo del sistema

1. El usuario abre un ticket desde el panel.
2. Dentro del ticket pulsa **Reclamar reseña** y el bot le asigna una
   automáticamente del stock, junto a las instrucciones.
3. Al terminar, pulsa **Enviar pruebas** y pega el enlace.
4. La prueba se manda al canal de verificación con botones de
   **Aprobar** / **Rechazar**.
   - Si se aprueba: se retira del stock de forma definitiva, se suma el
     saldo correspondiente y se avisa por mensaje directo al usuario.
   - Si se rechaza: vuelve a quedar disponible en el stock para que
     cualquiera la pueda reclamar de nuevo, y también se avisa por
     mensaje directo, indicando que no cumplió los requisitos.
5. El ticket se puede reclamar (staff) o cerrar en cualquier momento con los
   botones correspondientes.

## Notas

- El acceso a comandos de staff está basado en el permiso "Gestionar
  servidor" (`manage_guild`). Si prefieres usar un rol específico, se puede
  ajustar fácilmente en `utils.py`.
- Los tickets sin actividad durante 48 horas se cierran automáticamente.
- La base de datos es un archivo SQLite (`bot.db`) que se crea solo al
  iniciar el bot por primera vez.
