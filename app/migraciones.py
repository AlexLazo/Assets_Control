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
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

SCHEMA = Path(__file__).resolve().parent / "schema.sql"

VERSION_ACTUAL = 1

# (version, script SQL) -- solo cambios posteriores a la versión 1.
MIGRACIONES: list[tuple[int, str]] = []

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
                con.executescript(sql)
                con.execute(f"PRAGMA user_version = {ver}")
                version = ver
        con.commit()
        return version
    finally:
        con.close()


def asegurar_admin(ruta_db: str | Path) -> None:
    """Si no hay ningún Admin activo y están definidas ADMIN_USUARIO y
    ADMIN_PASSWORD, lo crea. Es la forma de entrar la primera vez a una base
    nueva (y de recuperar el acceso si se pierde la contraseña del único
    Admin)."""
    nombre = os.environ.get("ADMIN_USUARIO", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not nombre or not password:
        return
    con = sqlite3.connect(ruta_db)
    try:
        hay_admin = con.execute("SELECT 1 FROM usuarios WHERE rol='admin' AND activo=1 LIMIT 1").fetchone()
        if hay_admin:
            return
        con.execute(
            """INSERT INTO usuarios (nombre, password_hash, rol, activo) VALUES (?, ?, 'admin', 1)
               ON CONFLICT(nombre) DO UPDATE SET password_hash = excluded.password_hash, rol = 'admin', activo = 1""",
            (nombre, generate_password_hash(password)),
        )
        con.commit()
        print(f"[arranque] Se creó el Admin '{nombre}' desde las variables de entorno (la base no tenía ninguno).")
    finally:
        con.close()
