"""Agrega 'ruta_asignada_id' a equipos: la ruta fija de cada equipo.

Con esto, escanear una salida ya no requiere elegir ruta manualmente --
como cada QR es único, el equipo mismo "sabe" con qué ruta sale. Se puebla
inicialmente con la ruta de cada equipo según su último movimiento; los
equipos que hoy no tienen ningún movimiento (nunca se les sembró una
salida, ej. los que en el Excel estaban "Pendiente de Asignar") quedan sin
ruta asignada -- hay que dársela manualmente desde Inventario antes de
poder escanearlos en Salida.

Hace un respaldo del archivo .db antes de tocar nada. Seguro de correr más
de una vez: solo rellena equipos que sigan sin ruta_asignada_id, nunca
pisa una asignación ya hecha (a mano o por una corrida anterior).

Uso:
    python scripts/migrar_ruta_asignada.py
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "instance" / "activos.db"


def main() -> None:
    if not DB_PATH.exists():
        print("No existe instance/activos.db todavía.")
        return

    respaldo = DB_PATH.with_name(f"activos_antes_de_ruta_asignada_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH, respaldo)
    print(f"Respaldo creado: {respaldo}")

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")

    columnas = [f[1] for f in con.execute("PRAGMA table_info(equipos)").fetchall()]
    if "ruta_asignada_id" in columnas:
        print("'ruta_asignada_id' ya existe -- no se repite el ALTER TABLE.")
    else:
        con.execute("ALTER TABLE equipos ADD COLUMN ruta_asignada_id INTEGER REFERENCES rutas(id)")
        con.commit()
        print("Columna 'ruta_asignada_id' agregada a 'equipos'.")

    actualizados = con.execute(
        """UPDATE equipos SET ruta_asignada_id = (
               SELECT m.ruta_id FROM movimientos m
               WHERE m.equipo_id = equipos.id
               ORDER BY m.id DESC LIMIT 1
           )
           WHERE ruta_asignada_id IS NULL"""
    ).rowcount
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    sin_ruta = con.execute("SELECT COUNT(*) AS n FROM equipos WHERE ruta_asignada_id IS NULL").fetchone()[0]
    problemas = con.execute("PRAGMA foreign_key_check").fetchall()

    print(f"Equipos a los que se les pobló ruta_asignada_id desde su último movimiento: {actualizados}")
    print(f"Equipos que quedaron SIN ruta asignada (asígnalos desde Inventario antes de escanearlos): {sin_ruta}")
    if problemas:
        print("ATENCIÓN: problemas de integridad referencial:", problemas)
    else:
        print("Integridad referencial verificada: OK.")

    con.close()


if __name__ == "__main__":
    main()
