"""Reiniciar un día: borra los movimientos de un día (por ejemplo, pruebas) y
deja el estado de cada equipo como estaba antes.

Como "en ruta / en bodega" se calcula siempre del último movimiento, al borrar
los del día cada equipo vuelve solo a su estado anterior. Solo Super Admin.

Garantías: respaldo automático antes de borrar, validación de que ningún
equipo quede con una secuencia imposible (ej. un retorno sin su salida), y un
registro permanente en la tabla `reinicios` con el detalle de lo borrado.
"""
from __future__ import annotations

import json
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import respaldos
from .auth import requiere_permiso
from .db import get_db

bp = Blueprint("reinicio_dia", __name__, url_prefix="/admin/reiniciar-dia")

# Las salidas "sembradas" por la carga inicial desde Excel no son del día en
# que se cargaron: no se tocan nunca desde aquí.
NO_SEMBRADO = "(m.condicion IS NULL OR m.condicion NOT LIKE 'Carga inicial%')"


def _secuencia_valida(tipos: list[str]) -> bool:
    """Un equipo alterna salida/retorno empezando por salida."""
    esperado = "salida"
    for t in tipos:
        if t != esperado:
            return False
        esperado = "retorno" if t == "salida" else "salida"
    return True


def _calcular(db, dia: str, usuario_id: int | None) -> dict:
    sql = f"""SELECT m.id, m.equipo_id, m.tipo, m.timestamp, m.condicion, e.id_interno AS equipo,
                     r.codigo AS ruta, u.nombre AS usuario
              FROM movimientos m
              JOIN equipos e ON e.id = m.equipo_id
              JOIN rutas r ON r.id = m.ruta_id
              JOIN usuarios u ON u.id = m.operador_id
              WHERE date(m.timestamp) = ? AND {NO_SEMBRADO}"""
    params: list = [dia]
    if usuario_id:
        sql += " AND m.operador_id = ?"
        params.append(usuario_id)
    movimientos = [dict(f) for f in db.execute(sql + " ORDER BY m.id", params)]
    ids = {m["id"] for m in movimientos}

    # Incidencias que dependen de esos movimientos (y, si se borra el día
    # completo, las generadas por el "cerrar el día" de esa fecha).
    incidencias = []
    for i in db.execute("SELECT id, salida_movimiento_id, fecha, equipo_id FROM incidencias"):
        if i["salida_movimiento_id"] in ids or (not usuario_id and i["fecha"] == dia):
            incidencias.append(dict(i))

    # ¿Quedaría algún equipo con una secuencia imposible?
    problemas = []
    for equipo_id in sorted({m["equipo_id"] for m in movimientos}):
        restantes = [
            r["tipo"]
            for r in db.execute("SELECT id, tipo FROM movimientos WHERE equipo_id = ? ORDER BY id", (equipo_id,))
            if r["id"] not in ids
        ]
        if not _secuencia_valida(restantes):
            problemas.append(next(m["equipo"] for m in movimientos if m["equipo_id"] == equipo_id))

    por_usuario: dict[str, int] = {}
    for m in movimientos:
        por_usuario[m["usuario"]] = por_usuario.get(m["usuario"], 0) + 1
    return {"movimientos": movimientos, "incidencias": incidencias, "problemas": problemas, "por_usuario": por_usuario}


def _parametros():
    dia = request.values.get("dia") or date.today().isoformat()
    try:
        date.fromisoformat(dia)
    except ValueError:
        return None, None, "Fecha inválida (usa AAAA-MM-DD)."
    usuario_id = request.values.get("usuario_id", type=int) or None
    return dia, usuario_id, None


@bp.route("/", methods=["GET"])
@requiere_permiso("datos")
def vista_previa():
    dia, usuario_id, error = _parametros()
    if error:
        flash(error, "error")
        return redirect(url_for("datos.ver"))
    db = get_db()
    calculo = _calcular(db, dia, usuario_id)
    usuario = db.execute("SELECT nombre FROM usuarios WHERE id = ?", (usuario_id,)).fetchone() if usuario_id else None
    return render_template(
        "datos/reiniciar_dia.html",
        dia=dia,
        usuario_id=usuario_id,
        usuario_nombre=usuario["nombre"] if usuario else None,
        c=calculo,
        total=len(calculo["movimientos"]),
    )


@bp.route("/ejecutar", methods=["POST"])
@requiere_permiso("datos")
def ejecutar():
    dia, usuario_id, error = _parametros()
    if error:
        flash(error, "error")
        return redirect(url_for("datos.ver"))
    if request.form.get("confirmacion", "").strip().upper() != "REINICIAR":
        flash("Escribe REINICIAR (en mayúsculas) para confirmar. No se borró nada.", "error")
        return redirect(url_for("reinicio_dia.vista_previa", dia=dia, usuario_id=usuario_id or ""))

    db = get_db()
    db.commit()
    c = _calcular(db, dia, usuario_id)
    if not c["movimientos"]:
        flash("No hay movimientos que borrar con ese filtro.", "error")
        return redirect(url_for("datos.ver"))
    if c["problemas"]:
        flash(
            "No se borró nada: dejaría equipos con una secuencia imposible (hay movimientos posteriores que dependen de estos): "
            + ", ".join(c["problemas"][:10]),
            "error",
        )
        return redirect(url_for("reinicio_dia.vista_previa", dia=dia, usuario_id=usuario_id or ""))

    respaldo = respaldos.hacer_respaldo(current_app, "antes_de_reiniciar_dia")
    usuario = db.execute("SELECT nombre FROM usuarios WHERE id = ?", (usuario_id,)).fetchone() if usuario_id else None
    detalle = json.dumps(
        [{k: m[k] for k in ("id", "equipo", "tipo", "ruta", "usuario", "timestamp", "condicion")} for m in c["movimientos"]],
        ensure_ascii=False,
    )

    ids_inc = [i["id"] for i in c["incidencias"]]
    ids_mov = [m["id"] for m in c["movimientos"]]
    for lote in (ids_inc[i:i + 500] for i in range(0, len(ids_inc), 500)):
        db.execute(f"DELETE FROM incidencias WHERE id IN ({','.join('?' * len(lote))})", lote)
    for lote in (ids_mov[i:i + 500] for i in range(0, len(ids_mov), 500)):
        db.execute(f"DELETE FROM movimientos WHERE id IN ({','.join('?' * len(lote))})", lote)
    db.execute(
        """INSERT INTO reinicios (dia, usuario_filtro, operador_id, movimientos_borrados, incidencias_borradas, respaldo, detalle)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (dia, usuario["nombre"] if usuario else None, current_user.id, len(ids_mov), len(ids_inc), respaldo.name, detalle),
    )
    db.commit()
    flash(
        f"Día {dia} reiniciado: {len(ids_mov)} movimiento(s) y {len(ids_inc)} incidencia(s) borrados"
        + (f" (solo de {usuario['nombre']})" if usuario else "")
        + f". Respaldo previo: {respaldo.name}. Queda registrado en el historial de reinicios."
    )
    return redirect(url_for("datos.ver"))
