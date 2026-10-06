"""Agrega las tablas del conteo físico (conteos y conteo_items).
Hace respaldo antes de tocar nada; seguro de correr más de una vez.

Uso:
    python scripts/migrar_conteo.py
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "instance" / "activos.db"

SQL = """
CREATE TABLE conteos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_inicio        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    fecha_cierre        TEXT,
    operador_inicio_id  INTEGER NOT NULL REFERENCES usuarios(id),
    estado              TEXT NOT NULL DEFAULT 'abierto' CHECK (estado IN ('abierto','cerrado')),
    ajuste_aplicado     BOOLEAN NOT NULL DEFAULT 0,
    notas               TEXT
);
CREATE UNIQUE INDEX idx_un_solo_conteo_abierto ON conteos(estado) WHERE estado = 'abierto';

CREATE TABLE conteo_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    conteo_id    INTEGER NOT NULL REFERENCES conteos(id),
    equipo_id    INTEGER NOT NULL REFERENCES equipos(id),
    operador_id  INTEGER NOT NULL REFERENCES usuarios(id),
    timestamp    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (conteo_id, equipo_id)
);
"""


def main() -> None:
    if not DB_PATH.exists():
        print("No existe instance/activos.db todavía.")
        return
    respaldo = DB_PATH.with_name(f"activos_antes_de_conteo_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH, respaldo)
    print(f"Respaldo creado: {respaldo}")
    con = sqlite3.connect(DB_PATH)
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "conteos" in tablas:
        print("Las tablas del conteo ya existen -- no se repite.")
    else:
        con.executescript(SQL)
        con.commit()
        print("Tablas 'conteos' y 'conteo_items' creadas.")
    print("Integridad referencial:", con.execute("PRAGMA foreign_key_check").fetchall() or "OK")
    con.close()


if __name__ == "__main__":
    main()
