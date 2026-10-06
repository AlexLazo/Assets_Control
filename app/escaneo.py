import sqlite3
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .db import get_db

bp = Blueprint("escaneo", __name__)


def _buscar_equipo(db, id_interno: str):
    return db.execute(
        """SELECT e.id, e.id_interno, e.tipo, e.estado, e.ruta_asignada_id, r.codigo AS ruta_asignada_codigo
           FROM equipos e
           LEFT JOIN rutas r ON r.id = e.ruta_asignada_id
           WHERE e.id_interno = ?""",
        (id_interno.strip().upper(),),
    ).fetchone()


def _contador_turno(db, tipo: str) -> int:
    # condicion IS NULL: solo escaneos reales, no los retornos automáticos
    # que genera el sistema (mantenimiento, conteo).
    return db.execute(
        """SELECT COUNT(*) AS n FROM movimientos
           WHERE operador_id = ? AND tipo = ? AND condicion IS NULL
             AND date(timestamp) = date('now','localtime')""",
        (current_user.id, tipo),
    ).fetchone()["n"]


def _ultimos(db, tipo: str):
    filas = db.execute(
        """SELECT time(m.timestamp) AS hora, e.id_interno, r.codigo AS ruta
           FROM movimientos m
           JOIN equipos e ON e.id = m.equipo_id
           JOIN rutas r ON r.id = m.ruta_id
           WHERE m.operador_id = ? AND m.tipo = ? AND m.condicion IS NULL
             AND date(m.timestamp) = date('now','localtime')
           ORDER BY m.id DESC LIMIT 10""",
        (current_user.id, tipo),
    ).fetchall()
    return [[f["hora"], f["id_interno"], f["ruta"]] for f in filas]


def _responder(db, tipo: str, endpoint: str, ok: bool, mensaje: str, fila=None):
    """Mismo resultado para el formulario normal (flash + redirect) y para el
    envío rápido sin recargar (JSON que usa scan_ajax.js)."""
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=ok, mensaje=mensaje, fila=fila, contador=_contador_turno(db, tipo))
    flash(mensaje, "message" if ok else "error")
    return redirect(url_for(endpoint))


@bp.route("/salida", methods=["GET", "POST"])
@login_required
def salida():
    db = get_db()

    if request.method == "POST":
        id_interno = request.form.get("id_interno", "").strip()
        if not id_interno:
            return redirect(url_for("escaneo.salida"))

        def resp(ok, mensaje, fila=None):
            return _responder(db, "salida", "escaneo.salida", ok, mensaje, fila)

        equipo = _buscar_equipo(db, id_interno)
        if equipo is None:
            return resp(False, f"No existe ningún equipo con el código '{id_interno}'.")
        if equipo["estado"] == "reparacion":
            return resp(False, f"{equipo['id_interno']} está en mantenimiento; no se puede registrar salida.")
        if equipo["estado"] == "baja":
            return resp(False, f"{equipo['id_interno']} está dado de baja; no se puede registrar salida.")
        if equipo["ruta_asignada_id"] is None:
            return resp(
                False,
                f"{equipo['id_interno']} no tiene ruta asignada todavía. "
                "Asígnasela desde Inventario → Editar antes de poder escanearlo.",
            )

        try:
            db.execute(
                "INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo) VALUES (?, ?, ?, 'salida')",
                (equipo["id"], equipo["ruta_asignada_id"], current_user.id),
            )
            db.commit()
        except sqlite3.IntegrityError as e:
            db.rollback()
            return resp(False, f"{equipo['id_interno']}: {e}")
        return resp(
            True,
            f"Salida registrada: {equipo['id_interno']} -> ruta {equipo['ruta_asignada_codigo']}.",
            [datetime.now().strftime("%H:%M:%S"), equipo["id_interno"], equipo["ruta_asignada_codigo"]],
        )

    return render_template("escaneo/salida.html", contador=_contador_turno(db, "salida"), ultimos=_ultimos(db, "salida"))


@bp.route("/retorno", methods=["GET", "POST"])
@login_required
def retorno():
    db = get_db()

    if request.method == "POST":
        id_interno = request.form.get("id_interno", "").strip()
        if not id_interno:
            return redirect(url_for("escaneo.retorno"))

        def resp(ok, mensaje, fila=None):
            return _responder(db, "retorno", "escaneo.retorno", ok, mensaje, fila)

        equipo = _buscar_equipo(db, id_interno)
        if equipo is None:
            return resp(False, f"No existe ningún equipo con el código '{id_interno}'.")

        ultimo = db.execute(
            "SELECT ruta_id, tipo FROM movimientos WHERE equipo_id = ? ORDER BY id DESC LIMIT 1",
            (equipo["id"],),
        ).fetchone()
        if ultimo is None or ultimo["tipo"] != "salida":
            return resp(False, f"{equipo['id_interno']}: este equipo no tiene una salida activa; no se puede registrar un retorno.")

        try:
            db.execute(
                "INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo) VALUES (?, ?, ?, 'retorno')",
                (equipo["id"], ultimo["ruta_id"], current_user.id),
            )
            # Si este equipo tenía una alarma abierta por no haber retornado
            # antes, el propio retorno la resuelve -- no debería quedar una
            # incidencia abierta para un equipo que ya está de vuelta.
            db.execute(
                """UPDATE incidencias SET estado = 'resuelta',
                       resolucion_nota = 'Resuelta automáticamente: el equipo registró un retorno.'
                   WHERE equipo_id = ? AND estado = 'abierta'""",
                (equipo["id"],),
            )
            db.commit()
        except sqlite3.IntegrityError as e:
            db.rollback()
            return resp(False, f"{equipo['id_interno']}: {e}")

        ruta = db.execute("SELECT codigo FROM rutas WHERE id = ?", (ultimo["ruta_id"],)).fetchone()
        return resp(
            True,
            f"Retorno registrado: {equipo['id_interno']} (ruta {ruta['codigo']}).",
            [datetime.now().strftime("%H:%M:%S"), equipo["id_interno"], ruta["codigo"]],
        )

    return render_template("escaneo/retorno.html", contador=_contador_turno(db, "retorno"), ultimos=_ultimos(db, "retorno"))
