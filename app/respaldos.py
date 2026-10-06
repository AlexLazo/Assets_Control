"""Respaldos de la base de datos.

Se usa la API de respaldo de SQLite (no copiar el archivo a mano): produce una
copia consistente aunque la app esté escribiendo, algo que un simple copy del
.db no garantiza con journal_mode=WAL.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

PREFIJO = "activos_"
CADA_SEGUNDOS = 6 * 3600
_iniciado = False


def carpeta(app) -> Path:
    c = Path(app.config["DATA_DIR"]) / "respaldos"
    c.mkdir(parents=True, exist_ok=True)
    return c


def copiar(origen: str | Path, destino: str | Path) -> None:
    src = sqlite3.connect(origen)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def listar(app) -> list[Path]:
    return sorted(carpeta(app).glob(f"{PREFIJO}*.db"), reverse=True)


def hacer_respaldo(app, motivo: str = "auto") -> Path:
    destino = carpeta(app) / f"{PREFIJO}{datetime.now():%Y%m%d_%H%M%S}_{motivo}.db"
    copiar(app.config["DATABASE"], destino)
    _podar(app)
    return destino


def _podar(app) -> None:
    conservar = int(os.environ.get("RESPALDOS_MAX", "30"))
    for viejo in listar(app)[conservar:]:
        try:
            viejo.unlink()
        except OSError:
            pass


def _hay_datos(ruta: str) -> bool:
    try:
        con = sqlite3.connect(ruta)
        try:
            return con.execute("SELECT COUNT(*) FROM equipos").fetchone()[0] > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _ciclo(app) -> None:
    while True:
        try:
            ultimos = listar(app)
            vencido = not ultimos or (time.time() - ultimos[0].stat().st_mtime) > CADA_SEGUNDOS
            if vencido and _hay_datos(app.config["DATABASE"]):
                hacer_respaldo(app, "auto")
        except Exception as e:  # el respaldo nunca debe tumbar la app
            print(f"[respaldos] error: {e}")
        time.sleep(1800)


def iniciar(app) -> None:
    """Un hilo en segundo plano que respalda al arrancar y luego cada 6 h
    (solo si la base ya tiene equipos). Conserva los últimos RESPALDOS_MAX."""
    global _iniciado
    if _iniciado or os.environ.get("RESPALDOS_AUTO", "1") == "0":
        return
    _iniciado = True
    threading.Thread(target=_ciclo, args=(app,), name="respaldos-auto", daemon=True).start()
