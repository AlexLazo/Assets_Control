"""Crea y actualiza la base de datos sola, al arrancar la app.

- Base inexistente (ej. un volumen nuevo en Railway): se crea desde
  schema.sql y queda marcada con la versión actual.
- Base existente: se lee su versión (PRAGMA user_version) y se aplican, en
  orden, solo las migraciones que le falten. Nunca borra ni recrea datos.

Para un cambio futuro de esquema: sube VERSION_ACTUAL, agrega la misma
modificación a schema.sql (instalaciones nuevas) y una entrada en MIGRACIONES
(bases que ya existen). Cada deploy aplica lo pendiente automáticamente.
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from pathlib import Path

from werkzeug.security import generate_password_hash

SCHEMA = Path(__file__).resolve().parent / "schema.sql"

VERSION_ACTUAL = 5


def slug_usuario(texto: str) -> str:
    """'Bryan Galdámez' -> 'bryan.galdamez': base para el usuario de login de
    las cuentas que ya existían (el Admin puede cambiarlo después)."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", ".", sin_tildes.lower()).strip(".")
    return (slug or "usuario")[:30].strip(".") or "usuario"


def _v4_usuario_login(con: sqlite3.Connection) -> None:
    """v4: el login deja de ser el nombre completo. `usuario` es el nombre corto
    para entrar (AlexLz); `nombre` queda solo para mostrar en bitácora y reportes."""
    con.execute("ALTER TABLE usuarios ADD COLUMN usuario TEXT")
    con.execute("ALTER TABLE usuarios ADD COLUMN debe_cambiar_password BOOLEAN NOT NULL DEFAULT 0")
    usados: set[str] = set()
    for fila in con.execute("SELECT id, nombre FROM usuarios ORDER BY id").fetchall():
        base = slug_usuario(fila[1])
        candidato, n = base, 2
        while candidato in usados:
            sufijo = str(n)
            candidato = base[: 30 - len(sufijo)] + sufijo
            n += 1
        usados.add(candidato)
        con.execute("UPDATE usuarios SET usuario = ? WHERE id = ?", (candidato, fila[0]))
    con.execute("CREATE UNIQUE INDEX idx_usuarios_usuario ON usuarios(usuario COLLATE NOCASE)")
    con.commit()


# (version, script SQL o función(con)) -- solo cambios posteriores a la versión 1.
MIGRACIONES: list[tuple[int, object]] = [
    # v2: roles Supervisor / Facturador / Admin / Super Admin. SQLite no puede
    # cambiar un CHECK con ALTER TABLE, así que se reconstruye la tabla (el
    # procedimiento oficial: crear, copiar, borrar la vieja, renombrar).
    # Los 'operador' pasan a 'supervisor'; los 'admin' siguen 'admin' y el
    # primero se asciende a 'super_admin' para que alguien pueda gestionar todo.
    (
        2,
        """
        PRAGMA foreign_keys = OFF;
        BEGIN;
        CREATE TABLE usuarios_nueva (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre         TEXT NOT NULL UNIQUE,
            password_hash  TEXT NOT NULL,
            rol            TEXT NOT NULL DEFAULT 'supervisor'
                           CHECK (rol IN ('supervisor','facturador','admin','super_admin')),
            activo         BOOLEAN NOT NULL DEFAULT 1
        );
        INSERT INTO usuarios_nueva (id, nombre, password_hash, rol, activo)
            SELECT id, nombre, password_hash,
                   CASE WHEN rol = 'admin' THEN 'admin' ELSE 'supervisor' END, activo
            FROM usuarios;
        DROP TABLE usuarios;
        ALTER TABLE usuarios_nueva RENAME TO usuarios;
        UPDATE usuarios SET rol = 'super_admin'
            WHERE id = (SELECT MIN(id) FROM usuarios WHERE rol = 'admin' AND activo = 1)
              AND NOT EXISTS (SELECT 1 FROM usuarios WHERE rol = 'super_admin');
        COMMIT;
        PRAGMA foreign_keys = ON;
        """,
    ),
    # v3: registro permanente de los "reinicios de día" (quién borró qué).
    (
        3,
        """
        CREATE TABLE reinicios (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_accion          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            dia                   TEXT NOT NULL,
            usuario_filtro        TEXT,
            operador_id           INTEGER NOT NULL REFERENCES usuarios(id),
            movimientos_borrados  INTEGER NOT NULL,
            incidencias_borradas  INTEGER NOT NULL,
            respaldo              TEXT,
            detalle               TEXT
        );
        """,
    ),
    (4, _v4_usuario_login),
    # v5: auditoría (quién hizo qué) y quién/cuándo resolvió cada incidencia.
    # auditoria.usuario_id NO es llave foránea a propósito: el nombre queda
    # copiado en usuario_nombre y el registro sobrevive aunque se borre al usuario.
    (
        5,
        """
        CREATE TABLE auditoria (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            usuario_id      INTEGER,
            usuario_nombre  TEXT,
            accion          TEXT NOT NULL,
            objetivo        TEXT,
            detalle         TEXT
        );
        CREATE INDEX idx_auditoria_fecha   ON auditoria(fecha);
        CREATE INDEX idx_auditoria_usuario ON auditoria(usuario_id);
        CREATE INDEX idx_auditoria_accion  ON auditoria(accion);
        ALTER TABLE incidencias ADD COLUMN resuelta_por_id   INTEGER REFERENCES usuarios(id);
        ALTER TABLE incidencias ADD COLUMN fecha_resolucion  TEXT;
        """,
    ),
]

TABLAS_BASE_V1 = {"equipos", "rutas", "usuarios", "movimientos", "incidencias", "mantenimientos", "conteos", "conteo_items"}


def _tablas(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def actualizar(ruta_db: str | Path) -> int:
    """Crea o actualiza la base en `ruta_db`; devuelve la versión final."""
    ruta = Path(ruta_db)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nueva = not ruta.exists() or ruta.stat().st_size == 0

    con = sqlite3.connect(ruta)
    try:
        con.execute("PRAGMA journal_mode = WAL")
        if nueva:
            con.executescript(SCHEMA.read_text(encoding="utf-8"))
            con.execute(f"PRAGMA user_version = {VERSION_ACTUAL}")
            con.commit()
            return VERSION_ACTUAL

        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # Base creada antes de que existiera este mecanismo: si ya tiene
            # todas las tablas, está en la versión 1.
            faltan = TABLAS_BASE_V1 - _tablas(con)
            if faltan:
                raise RuntimeError(
                    f"La base {ruta} es de una versión antigua (faltan tablas: {sorted(faltan)}). "
                    "Aplica primero los scripts de scripts/migrar_*.py."
                )
            version = 1
            con.execute("PRAGMA user_version = 1")

        for ver, sql in sorted(MIGRACIONES):
            if ver > version:
                if callable(sql):
                    sql(con)
                else:
                    con.executescript(sql)
                con.execute(f"PRAGMA foreign_keys = ON")
                problemas = con.execute("PRAGMA foreign_key_check").fetchall()
                if problemas:
                    raise RuntimeError(f"La migración v{ver} dejó referencias rotas: {problemas[:5]}")
                con.execute(f"PRAGMA user_version = {ver}")
                version = ver
        con.commit()
        return version
    finally:
        con.close()


def asegurar_admin(ruta_db: str | Path) -> None:
    """Si no hay ningún Super Admin activo y están definidas ADMIN_USUARIO y
    ADMIN_PASSWORD, lo crea como Super Admin. Es la forma de entrar la primera vez a una base
    nueva. Con ADMIN_FORZAR=1 además restablece la contraseña de ese usuario
    aunque ya existan Admins (recuperación de acceso); hay que quitar la
    variable después, porque si no se repite en cada arranque."""
    nombre = os.environ.get("ADMIN_USUARIO", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not nombre or not password:
        return
    forzar = os.environ.get("ADMIN_FORZAR", "") == "1"
    con = sqlite3.connect(ruta_db)
    try:
        hay_admin = con.execute("SELECT 1 FROM usuarios WHERE rol='super_admin' AND activo=1 LIMIT 1").fetchone()
        if hay_admin and not forzar:
            return
        existente = con.execute(
            "SELECT id FROM usuarios WHERE nombre = ? OR usuario = ? COLLATE NOCASE ORDER BY id LIMIT 1", (nombre, nombre)
        ).fetchone()
        if existente:
            con.execute(
                """UPDATE usuarios SET password_hash = ?, rol = 'super_admin', activo = 1,
                          debe_cambiar_password = 0, usuario = COALESCE(usuario, ?) WHERE id = ?""",
                (generate_password_hash(password), nombre, existente[0]),
            )
        else:
            con.execute(
                "INSERT INTO usuarios (nombre, usuario, password_hash, rol, activo) VALUES (?, ?, ?, 'super_admin', 1)",
                (nombre, nombre, generate_password_hash(password)),
            )
        con.commit()
        accion = "Se restableció la contraseña del Admin" if hay_admin else "Se creó el Admin"
        print(f"[arranque] {accion} '{nombre}' desde las variables de entorno.")
    finally:
        con.close()
