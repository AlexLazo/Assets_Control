"""Agrega el módulo de mantenimiento: tabla 'mantenimientos' (qué equipo,
desde cuándo, por qué, y cuándo regresó) y dos triggers de seguridad:

- Un equipo con estado != 'activo' (en reparación o dado de baja) no puede
  registrar una salida -- respaldo a nivel de base de datos del chequeo que
  ya hace la app.
- Un equipo no puede tener dos mantenimientos abiertos a la vez.

Hace un respaldo del archivo .db antes de tocar nada. Seguro de correr más
de una vez: si 'mantenimientos' ya existe, no repite nada.

Uso:
    python scripts/migrar_mantenimiento.py
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "instance" / "activos.db"

SQL_MIGRACION = """
CREATE TABLE mantenimientos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id            INTEGER NOT NULL REFERENCES equipos(id),
    fecha_envio          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    fecha_retorno        TEXT,
    motivo               TEXT,
    resultado            TEXT,
    operador_envio_id    INTEGER NOT NULL REFERENCES usuarios(id),
    operador_retorno_id  INTEGER REFERENCES usuarios(id),
    estado               TEXT NOT NULL DEFAULT 'en_reparacion' CHECK (estado IN ('en_reparacion','resuelto'))
);
CREATE INDEX idx_mantenimientos_equipo ON mantenimientos(equipo_id, id);

CREATE TRIGGER trg_no_salida_si_no_activo
BEFORE INSERT ON movimientos
WHEN NEW.tipo = 'salida' AND (
    SELECT estado FROM equipos WHERE id = NEW.equipo_id
) != 'activo'
BEGIN
    SELECT RAISE(ABORT, 'Este equipo no está activo (en reparación o dado de baja); no se puede registrar una salida.');
END;

CREATE TRIGGER trg_no_doble_mantenimiento
BEFORE INSERT ON mantenimientos
WHEN NEW.estado = 'en_reparacion' AND EXISTS (
    SELECT 1 FROM mantenimientos WHERE equipo_id = NEW.equipo_id AND estado = 'en_reparacion'
)
BEGIN
    SELECT RAISE(ABORT, 'Este equipo ya tiene un mantenimiento abierto.');
END;
"""


def main() -> None:
    if not DB_PATH.exists():
        print("No existe instance/activos.db todavía.")
        return

    respaldo = DB_PATH.with_name(f"activos_antes_de_mantenimiento_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH, respaldo)
    print(f"Respaldo creado: {respaldo}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "mantenimientos" in tablas:
        print("La tabla 'mantenimientos' ya existe -- no se repite la migración.")
    else:
        con.executescript(SQL_MIGRACION)
        con.commit()
        print("Tabla 'mantenimientos' y triggers de seguridad creados.")

    # Cualquier equipo que ya estuviera marcado 'reparacion' desde antes
    # (por edición manual, sin este módulo) queda con un mantenimiento
    # abierto para que aparezca en el listado y no se pierda de vista.
    huerfanos = con.execute(
        """SELECT e.id FROM equipos e
           WHERE e.estado = 'reparacion'
             AND NOT EXISTS (SELECT 1 FROM mantenimientos m WHERE m.equipo_id = e.id AND m.estado = 'en_reparacion')"""
    ).fetchall()
    if huerfanos:
        admin = con.execute("SELECT id FROM usuarios WHERE rol = 'admin' ORDER BY id LIMIT 1").fetchone()
        if admin:
            for fila in huerfanos:
                con.execute(
                    """INSERT INTO mantenimientos (equipo_id, motivo, operador_envio_id)
                       VALUES (?, 'Marcado como reparación antes de que existiera este módulo.', ?)""",
                    (fila["id"], admin["id"]),
                )
            con.commit()
            print(f"Se abrió un registro de mantenimiento retroactivo para {len(huerfanos)} equipo(s) que ya estaban en 'reparacion'.")
        else:
            print(f"AVISO: {len(huerfanos)} equipo(s) están en 'reparacion' pero no hay ningún Admin para atribuirles el registro retroactivo -- créalos manualmente desde Mantenimiento.")

    problemas = con.execute("PRAGMA foreign_key_check").fetchall()
    print("Integridad referencial:", "OK" if not problemas else problemas)
    con.close()


if __name__ == "__main__":
    main()
