"""Migra la base de "elegir operador sin contraseña" a usuarios reales con
contraseña y rol (admin/operador).

Hace un respaldo del archivo .db ANTES de tocar nada -- ya hay datos reales
de producción (equipos importados) que no se pueden arriesgar. Es seguro
correr más de una vez: si la tabla ya tiene password_hash, no repite nada.

Uso:
    python scripts/migrar_login_roles.py
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "instance" / "activos.db"


def ya_migrado(con: sqlite3.Connection) -> bool:
    columnas = [f[1] for f in con.execute("PRAGMA table_info(usuarios)").fetchall()]
    return "password_hash" in columnas


def main() -> None:
    if not DB_PATH.exists():
        print("No existe instance/activos.db todavía -- no hay nada que migrar.")
        print("Si es una instalación nueva, corre primero scripts/importar_catalogo.py.")
        return

    respaldo = DB_PATH.with_name(f"activos_antes_de_roles_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH, respaldo)
    print(f"Respaldo creado: {respaldo}")

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")

    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    if "usuarios" not in tablas:
        if "operadores" not in tablas:
            print("No se encontró ni 'usuarios' ni 'operadores' en la base -- revísala manualmente.")
            con.close()
            return
        con.execute("ALTER TABLE operadores RENAME TO usuarios")
        print("Tabla 'operadores' renombrada a 'usuarios'.")

    if ya_migrado(con):
        print("La tabla 'usuarios' ya tenía password_hash/rol -- no se repite la migración.")
    else:
        con.execute("ALTER TABLE usuarios ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        con.execute(
            "ALTER TABLE usuarios ADD COLUMN rol TEXT NOT NULL DEFAULT 'operador' CHECK (rol IN ('admin','operador'))"
        )
        print("Columnas password_hash y rol agregadas a 'usuarios'.")

    con.commit()

    problemas = con.execute("PRAGMA foreign_key_check").fetchall()
    if problemas:
        print("ATENCIÓN: se encontraron problemas de integridad referencial:", problemas)
        print(f"La base quedó respaldada en {respaldo} por si hay que revertir.")
    else:
        print("Integridad referencial verificada: OK.")

    con.close()
    print("\nListo. Ahora corre: python scripts/crear_usuario.py   (para crear tu primer Admin)")


if __name__ == "__main__":
    main()
