from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from .auth import requiere_permiso
from .db import get_db

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _historial_movimientos(db, dias: int = 14):
    """Salidas/retornos por día de los últimos `dias` días, incluyendo los
    días sin actividad (en 0), para alimentar el gráfico del dashboard."""
    inicio = date.today() - timedelta(days=dias - 1)
    filas = db.execute(
        """SELECT date(timestamp) AS dia, tipo, COUNT(*) AS total
           FROM movimientos
           WHERE date(timestamp) >= date(?)
           GROUP BY dia, tipo""",
        (inicio.isoformat(),),
    ).fetchall()

    por_dia = {}
    for f in filas:
        por_dia.setdefault(f["dia"], {"salida": 0, "retorno": 0})[f["tipo"]] = f["total"]

    etiquetas, salidas, retornos = [], [], []
    for i in range(dias):
        dia = (inicio + timedelta(days=i)).isoformat()
        etiquetas.append(dia[5:])
        datos = por_dia.get(dia, {"salida": 0, "retorno": 0})
        salidas.append(datos["salida"])
        retornos.append(datos["retorno"])
    return etiquetas, salidas, retornos


@bp.route("/")
@login_required
def ver():
    db = get_db()

    contadores = db.execute(
        """SELECT e.tipo AS tipo_equipo, v.ubicacion, COUNT(*) AS total
           FROM v_estado_actual v
           JOIN equipos e ON e.id = v.equipo_id
           WHERE e.estado = 'activo'
           GROUP BY e.tipo, v.ubicacion"""
    ).fetchall()

    en_ruta = sum(f["total"] for f in contadores if f["ubicacion"] == "en_ruta")
    en_bodega = sum(f["total"] for f in contadores if f["ubicacion"] == "en_bodega")
    total_activos = en_ruta + en_bodega
    en_reparacion = db.execute("SELECT COUNT(*) AS n FROM equipos WHERE estado = 'reparacion'").fetchone()["n"]

    incidencias = db.execute(
        """SELECT i.id, e.id_interno, e.tipo, r.codigo AS ruta_codigo, i.fecha, i.fecha_deteccion
           FROM incidencias i
           JOIN equipos e ON e.id = i.equipo_id
           JOIN rutas r ON r.id = i.ruta_id
           WHERE i.estado = 'abierta'
           ORDER BY i.fecha_deteccion DESC"""
    ).fetchall()

    etiquetas, salidas, retornos = _historial_movimientos(db)

    return render_template(
        "dashboard/ver.html",
        contadores=contadores,
        en_ruta=en_ruta,
        en_bodega=en_bodega,
        total_activos=total_activos,
        en_reparacion=en_reparacion,
        incidencias=incidencias,
        etiquetas=etiquetas,
        salidas=salidas,
        retornos=retornos,
    )


@bp.route("/cerrar-dia", methods=["POST"])
@requiere_permiso("cerrar_dia")
def cerrar_dia():
    db = get_db()
    hoy = date.today().isoformat()

    pendientes = db.execute(
        """SELECT v.equipo_id AS equipo_id, v.ruta_actual_id AS ruta_id,
                  (SELECT m.id FROM movimientos m
                   WHERE m.equipo_id = v.equipo_id ORDER BY m.id DESC LIMIT 1) AS movimiento_id
           FROM v_estado_actual v
           JOIN equipos e ON e.id = v.equipo_id
           WHERE v.ubicacion = 'en_ruta' AND e.estado = 'activo'"""
    ).fetchall()

    nuevas = 0
    for fila in pendientes:
        cur = db.execute(
            """INSERT OR IGNORE INTO incidencias (equipo_id, ruta_id, salida_movimiento_id, fecha, operador_cierre_id)
               VALUES (?, ?, ?, ?, ?)""",
            (fila["equipo_id"], fila["ruta_id"], fila["movimiento_id"], hoy, current_user.id),
        )
        nuevas += cur.rowcount

    db.commit()
    if nuevas:
        flash(f"Cierre de día registrado: {nuevas} equipo(s) marcados como incidencia (no retornaron).", "error")
    else:
        flash("Cierre de día registrado: no hay equipos pendientes de retorno. Sin incidencias nuevas.")
    return redirect(url_for("dashboard.ver"))
