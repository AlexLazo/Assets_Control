"""Alertas del sistema: cosas que llevan demasiado tiempo así y alguien debe mirar.

Los umbrales se pueden ajustar sin tocar el código con variables de entorno:
  ALERTA_DIAS_MANTENIMIENTO  días en reparación antes de alertar (por defecto 30)
  ALERTA_DIAS_FUERA          días en ruta sin retorno antes de alertar (por defecto 2)
"""
from __future__ import annotations

import os

from flask import g
from flask_login import current_user


def _entero(nombre: str, defecto: int) -> int:
    try:
        return max(1, int(os.environ.get(nombre, defecto)))
    except ValueError:
        return defecto


DIAS_MANTENIMIENTO = _entero("ALERTA_DIAS_MANTENIMIENTO", 30)
DIAS_FUERA = _entero("ALERTA_DIAS_FUERA", 2)

# Mismos "días" que muestra la pantalla de Mantenimiento: tiempo transcurrido
# desde el envío, en días completos.
_DIAS = "CAST(julianday('now','localtime') - julianday({col}) AS INTEGER)"


def calcular(db) -> dict:
    mantenimiento = db.execute(
        f"""SELECT * FROM (
                SELECT e.id_interno, e.tipo, m.fecha_envio, m.motivo, {_DIAS.format(col='m.fecha_envio')} AS dias
                FROM mantenimientos m JOIN equipos e ON e.id = m.equipo_id
                WHERE m.estado = 'en_reparacion')
            WHERE dias >= ? ORDER BY dias DESC, id_interno""",
        (DIAS_MANTENIMIENTO,),
    ).fetchall()

    fuera = db.execute(
        f"""SELECT * FROM (
                SELECT e.id_interno, e.tipo, r.codigo AS ruta, r.supervisor, v.desde,
                       {_DIAS.format(col='v.desde')} AS dias
                FROM v_estado_actual v
                JOIN equipos e ON e.id = v.equipo_id
                JOIN rutas r ON r.id = v.ruta_actual_id
                WHERE v.ubicacion = 'en_ruta' AND e.estado = 'activo')
            WHERE dias >= ? ORDER BY dias DESC, id_interno""",
        (DIAS_FUERA,),
    ).fetchall()

    return {
        "mantenimiento": mantenimiento,
        "fuera": fuera,
        "total": len(mantenimiento) + len(fuera),
        "dias_mantenimiento": DIAS_MANTENIMIENTO,
        "dias_fuera": DIAS_FUERA,
    }


def total_para_menu() -> int:
    """Número que se muestra como globo rojo en el menú. Se calcula una vez por
    petición y solo para quien ya inició sesión."""
    if not (current_user and current_user.is_authenticated):
        return 0
    if "alertas_total" not in g:
        from .db import get_db

        try:
            g.alertas_total = calcular(get_db())["total"]
        except Exception:  # el menú nunca debe tumbar una pantalla
            g.alertas_total = 0
    return g.alertas_total
