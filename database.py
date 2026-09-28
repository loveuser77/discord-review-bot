import aiosqlite
import datetime
from config import DB_PATH

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA foreign_keys = ON")
    return _db


async def init_db():
    db = await get_db()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS config (
            guild_id INTEGER PRIMARY KEY,
            ticket_category_id INTEGER,
            verification_channel_id INTEGER,
            log_channel_id INTEGER,
            panel_channel_id INTEGER,
            ticket_message TEXT,
            ticket_counter INTEGER DEFAULT 0,
            stock_alert_threshold INTEGER DEFAULT 3,
            tickets_paused INTEGER DEFAULT 0,
            cooldown_horas INTEGER DEFAULT 24
        );

        CREATE TABLE IF NOT EXISTS plantillas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            nombre TEXT,
            instrucciones TEXT,
            valor REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            plantilla_id INTEGER,
            link TEXT,
            estado TEXT DEFAULT 'disponible',
            claimed_by INTEGER,
            claimed_at TEXT,
            ticket_channel_id INTEGER,
            proof_link TEXT,
            decided_by INTEGER,
            decided_at TEXT,
            reject_reason TEXT,
            FOREIGN KEY (plantilla_id) REFERENCES plantillas(id)
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            channel_id INTEGER,
            user_id INTEGER,
            claimed_staff_id INTEGER,
            status TEXT DEFAULT 'abierto',
            created_at TEXT,
            review_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS balances (
            guild_id INTEGER,
            user_id INTEGER,
            balance REAL DEFAULT 0,
            payment_method TEXT,
            PRIMARY KEY (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS blacklist (
            guild_id INTEGER,
            user_id INTEGER,
            reason TEXT,
            added_by INTEGER,
            added_at TEXT,
            PRIMARY KEY (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            review_id INTEGER,
            user_id INTEGER,
            reason TEXT,
            created_at TEXT
        );
        """
    )
    # Migración: agrega la columna link si la base de datos ya existía sin ella
    cur = await db.execute("PRAGMA table_info(reviews)")
    columnas = [c["name"] for c in await cur.fetchall()]
    if "link" not in columnas:
        await db.execute("ALTER TABLE reviews ADD COLUMN link TEXT")
    await db.commit()


def now() -> str:
    return datetime.datetime.utcnow().isoformat()


# ---------- CONFIG ----------

async def get_config(guild_id: int) -> aiosqlite.Row:
    db = await get_db()
    cur = await db.execute("SELECT * FROM config WHERE guild_id = ?", (guild_id,))
    row = await cur.fetchone()
    if row is None:
        from config import TEXTO_TICKET_DEFAULT
        await db.execute(
            "INSERT INTO config (guild_id, ticket_message) VALUES (?, ?)",
            (guild_id, TEXTO_TICKET_DEFAULT),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM config WHERE guild_id = ?", (guild_id,))
        row = await cur.fetchone()
    return row


async def update_config(guild_id: int, **campos):
    await get_config(guild_id)  # asegura que exista la fila
    db = await get_db()
    columnas = ", ".join(f"{k} = ?" for k in campos)
    valores = list(campos.values()) + [guild_id]
    await db.execute(f"UPDATE config SET {columnas} WHERE guild_id = ?", valores)
    await db.commit()


async def next_ticket_number(guild_id: int) -> int:
    await get_config(guild_id)
    db = await get_db()
    await db.execute(
        "UPDATE config SET ticket_counter = ticket_counter + 1 WHERE guild_id = ?",
        (guild_id,),
    )
    await db.commit()
    cur = await db.execute(
        "SELECT ticket_counter FROM config WHERE guild_id = ?", (guild_id,)
    )
    row = await cur.fetchone()
    return row["ticket_counter"]


# ---------- PLANTILLAS ----------

async def crear_plantilla(guild_id: int, nombre: str, instrucciones: str, valor: float) -> int:
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO plantillas (guild_id, nombre, instrucciones, valor) VALUES (?, ?, ?, ?)",
        (guild_id, nombre, instrucciones, valor),
    )
    await db.commit()
    return cur.lastrowid


async def listar_plantillas(guild_id: int):
    db = await get_db()
    cur = await db.execute("SELECT * FROM plantillas WHERE guild_id = ?", (guild_id,))
    return await cur.fetchall()


async def ultima_plantilla(guild_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM plantillas WHERE guild_id = ? ORDER BY id DESC LIMIT 1",
        (guild_id,),
    )
    return await cur.fetchone()


async def get_plantilla(plantilla_id: int):
    db = await get_db()
    cur = await db.execute("SELECT * FROM plantillas WHERE id = ?", (plantilla_id,))
    return await cur.fetchone()


# ---------- STOCK / REVIEWS ----------

async def agregar_stock(guild_id: int, plantilla_id: int, links: list) -> int:
    db = await get_db()
    # Evita duplicados contra las reseñas que siguen activas
    cur = await db.execute(
        "SELECT link FROM reviews WHERE guild_id = ? "
        "AND estado IN ('disponible', 'reclamada', 'pendiente_verificacion')",
        (guild_id,),
    )
    existentes = {r["link"] for r in await cur.fetchall()}
    nuevos = [l for l in dict.fromkeys(links) if l not in existentes]
    await db.executemany(
        "INSERT INTO reviews (guild_id, plantilla_id, link, estado) VALUES (?, ?, ?, 'disponible')",
        [(guild_id, plantilla_id, l) for l in nuevos],
    )
    await db.commit()
    return len(nuevos)


async def contar_stock(guild_id: int):
    db = await get_db()
    cur = await db.execute(
        """
        SELECT p.nombre AS nombre, COUNT(*) AS cantidad
        FROM reviews r
        JOIN plantillas p ON p.id = r.plantilla_id
        WHERE r.guild_id = ? AND r.estado = 'disponible'
        GROUP BY p.id
        """,
        (guild_id,),
    )
    return await cur.fetchall()


async def total_disponibles(guild_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) AS c FROM reviews WHERE guild_id = ? AND estado = 'disponible'",
        (guild_id,),
    )
    row = await cur.fetchone()
    return row["c"]


async def reclamar_review(guild_id: int, user_id: int, ticket_channel_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT id FROM reviews WHERE guild_id = ? AND estado = 'disponible' LIMIT 1",
        (guild_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    review_id = row["id"]
    await db.execute(
        """
        UPDATE reviews
        SET estado = 'reclamada', claimed_by = ?, claimed_at = ?, ticket_channel_id = ?
        WHERE id = ?
        """,
        (user_id, now(), ticket_channel_id, review_id),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM reviews WHERE id = ?", (review_id,))
    return await cur.fetchone()


async def get_review(review_id: int):
    db = await get_db()
    cur = await db.execute("SELECT * FROM reviews WHERE id = ?", (review_id,))
    return await cur.fetchone()


async def enviar_prueba(review_id: int, link: str):
    db = await get_db()
    await db.execute(
        "UPDATE reviews SET estado = 'pendiente_verificacion', proof_link = ? WHERE id = ?",
        (link, review_id),
    )
    await db.commit()


async def aprobar_review(review_id: int, staff_id: int):
    db = await get_db()
    await db.execute(
        """
        UPDATE reviews
        SET estado = 'aprobada', decided_by = ?, decided_at = ?
        WHERE id = ?
        """,
        (staff_id, now(), review_id),
    )
    await db.commit()


async def rechazar_review(review_id: int, staff_id: int, razon: str):
    db = await get_db()
    # Vuelve al stock: se libera para que cualquiera la pueda reclamar de nuevo
    await db.execute(
        """
        UPDATE reviews
        SET estado = 'disponible', claimed_by = NULL, claimed_at = NULL,
            ticket_channel_id = NULL, proof_link = NULL,
            decided_by = ?, decided_at = ?, reject_reason = ?
        WHERE id = ?
        """,
        (staff_id, now(), razon, review_id),
    )
    await db.commit()


async def eliminar_review(review_id: int):
    db = await get_db()
    await db.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    await db.commit()


async def reseñas_de_usuario(guild_id: int, user_id: int):
    db = await get_db()
    cur = await db.execute(
        """
        SELECT r.*, p.nombre AS plantilla_nombre
        FROM reviews r
        LEFT JOIN plantillas p ON p.id = r.plantilla_id
        WHERE r.guild_id = ? AND r.claimed_by = ?
        ORDER BY r.id DESC
        """,
        (guild_id, user_id),
    )
    return await cur.fetchall()


async def reseña_activa_de_usuario(guild_id: int, user_id: int):
    db = await get_db()
    cur = await db.execute(
        """
        SELECT * FROM reviews
        WHERE guild_id = ? AND claimed_by = ?
        AND estado IN ('reclamada', 'pendiente_verificacion')
        ORDER BY id DESC LIMIT 1
        """,
        (guild_id, user_id),
    )
    return await cur.fetchone()


async def top_usuarios(guild_id: int, limite: int = 10):
    db = await get_db()
    cur = await db.execute(
        """
        SELECT claimed_by AS user_id, COUNT(*) AS aprobadas
        FROM reviews
        WHERE guild_id = ? AND estado = 'aprobada'
        GROUP BY claimed_by
        ORDER BY aprobadas DESC
        LIMIT ?
        """,
        (guild_id, limite),
    )
    return await cur.fetchall()


async def staff_stats(guild_id: int):
    db = await get_db()
    cur = await db.execute(
        """
        SELECT decided_by AS staff_id,
               SUM(CASE WHEN estado = 'aprobada' THEN 1 ELSE 0 END) AS aprobadas,
               SUM(CASE WHEN estado = 'disponible' AND reject_reason IS NOT NULL THEN 1 ELSE 0 END) AS rechazadas
        FROM reviews
        WHERE guild_id = ? AND decided_by IS NOT NULL
        GROUP BY decided_by
        """,
        (guild_id,),
    )
    return await cur.fetchall()


async def todas_las_reseñas(guild_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM reviews WHERE guild_id = ? ORDER BY id", (guild_id,)
    )
    return await cur.fetchall()


# ---------- TICKETS ----------

async def crear_ticket(guild_id: int, channel_id: int, user_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO tickets (guild_id, channel_id, user_id, created_at) VALUES (?, ?, ?, ?)",
        (guild_id, channel_id, user_id, now()),
    )
    await db.commit()
    return cur.lastrowid


async def get_ticket_por_canal(channel_id: int):
    db = await get_db()
    cur = await db.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
    return await cur.fetchone()


async def reclamar_ticket(channel_id: int, staff_id: int):
    db = await get_db()
    await db.execute(
        "UPDATE tickets SET claimed_staff_id = ? WHERE channel_id = ?",
        (staff_id, channel_id),
    )
    await db.commit()


async def cerrar_ticket(channel_id: int):
    db = await get_db()
    await db.execute(
        "UPDATE tickets SET status = 'cerrado' WHERE channel_id = ?", (channel_id,)
    )
    await db.commit()


async def vincular_review_a_ticket(channel_id: int, review_id: int):
    db = await get_db()
    await db.execute(
        "UPDATE tickets SET review_id = ? WHERE channel_id = ?",
        (review_id, channel_id),
    )
    await db.commit()


async def tickets_abiertos(guild_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND status = 'abierto'", (guild_id,)
    )
    return await cur.fetchall()


# ---------- BALANCES ----------

async def get_balance(guild_id: int, user_id: int) -> float:
    db = await get_db()
    cur = await db.execute(
        "SELECT balance FROM balances WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    row = await cur.fetchone()
    return row["balance"] if row else 0.0


async def sumar_balance(guild_id: int, user_id: int, cantidad: float):
    db = await get_db()
    await db.execute(
        """
        INSERT INTO balances (guild_id, user_id, balance) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = balance + excluded.balance
        """,
        (guild_id, user_id, cantidad),
    )
    await db.commit()


async def set_balance(guild_id: int, user_id: int, cantidad: float):
    db = await get_db()
    await db.execute(
        """
        INSERT INTO balances (guild_id, user_id, balance) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = excluded.balance
        """,
        (guild_id, user_id, cantidad),
    )
    await db.commit()


async def set_metodo_pago(guild_id: int, user_id: int, metodo: str):
    db = await get_db()
    await db.execute(
        """
        INSERT INTO balances (guild_id, user_id, balance, payment_method) VALUES (?, ?, 0, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET payment_method = excluded.payment_method
        """,
        (guild_id, user_id, metodo),
    )
    await db.commit()


async def get_metodo_pago(guild_id: int, user_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT payment_method FROM balances WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    row = await cur.fetchone()
    return row["payment_method"] if row else None


# ---------- BLACKLIST ----------

async def agregar_blacklist(guild_id: int, user_id: int, reason: str, added_by: int):
    db = await get_db()
    await db.execute(
        """
        INSERT INTO blacklist (guild_id, user_id, reason, added_by, added_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET reason = excluded.reason
        """,
        (guild_id, user_id, reason, added_by, now()),
    )
    await db.commit()


async def quitar_blacklist(guild_id: int, user_id: int):
    db = await get_db()
    await db.execute(
        "DELETE FROM blacklist WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )
    await db.commit()


async def esta_en_blacklist(guild_id: int, user_id: int) -> bool:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM blacklist WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    return (await cur.fetchone()) is not None


# ---------- REPORTES ----------

async def crear_reporte(guild_id: int, review_id: int, user_id: int, reason: str) -> int:
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO reports (guild_id, review_id, user_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (guild_id, review_id, user_id, reason, now()),
    )
    await db.commit()
    return cur.lastrowid
