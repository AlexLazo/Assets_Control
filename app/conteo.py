import sqlite3
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .auth import requiere_admin
from .db import get_db

bp = Blueprint("conteo", __name__, url_prefix="/conteo")

ETIQUETA_UBICACION = {"en_ruta": "en ruta", "en_bodega": "en bodega"}


def _conteo_abierto(db):
    return db.execute("SELECT * FROM conteos WHERE estado = 'abierto'").fetchone()


def _clasificar(db, conteo_id: int):
    """Compara lo escaneado en el conteo contra lo que el sistema cree."""
    escaneados = db.execute(
        """SELECT e.id, e.id_interno, e.tipo, e.estado, v.ubicacion, v.ruta_actual_id, r.codigo AS ruta
           FROM conteo_items ci
           JOIN equipos e ON e.id = ci.equipo_id
           LEFT JOIN v_estado_actual v ON v.equipo_id = e.id
           LEFT JOIN rutas r ON r.id = v.ruta_actual_id
           WHERE ci.conteo_id = ?
           ORDER BY e.id_interno""",
        (conteo_id,),
    ).fetchall()

    ok = [e for e in escaneados if e["estado"] == "activo" and e["ubicacion"] == "en_bodega"]
    # El sistema los tenía "en ruta" pero están físicamente en bodega.
    corregir = [e for e in escaneados if e["estado"] == "activo" and e["ubicacion"] == "en_ruta"]
    fuera_de_servicio = [e for e in escaneados if e["estado"] != "activo"]

    faltantes = db.execute(
        """SELECT e.id_interno, e.tipo, ra.codigo AS ruta_asignada
           FROM equipos e
           JOIN v_estado_actual v ON v.equipo_id = e.id
           LEFT JOIN rutas ra ON ra.id = e.ruta_asignada_id
           WHERE e.estado = 'activo' AND v.ubicacion = 'en_bodega'
             AND e.id NOT IN (SELECT equipo_id FROM conteo_items WHERE conteo_id = ?)
           ORDER BY e.id_interno""",
        (conteo_id,),
    ).fetchall()
    return escaneados, ok, corregir, fuera_de_servicio, faltantes


@bp.route("/")
@login_required
def ver():
    db = get_db()
    abierto = _conteo_abierto(db)
    if abierto:
        total = db.execute("SELECT COUNT(*) AS n FROM conteo_items WHERE conteo_id = ?", (abierto["id"],)).fetchone()["n"]
        ultimos = db.execute(
            """SELECT time(ci.timestamp) AS hora, e.id_interno, v.ubicacion
               FROM conteo_items ci
               JOIN equipos e ON e.id = ci.equipo_id
               LEFT JOIN v_estado_actual v ON v.equipo_id = e.id
               WHERE ci.conteo_id = ? ORDER BY ci.id DESC LIMIT 10""",
            (abierto["id"],),
        ).fetchall()
        ultimos = [[u["hora"], u["id_interno"], ETIQUETA_UBICACION.get(u["ubicacion"], "-")] for u in ultimos]
        return render_template("conteo/escanear.html", conteo=abierto, total=total, ultimos=ultimos)

    historial = []
    if current_user.rol == "admin":
        historial = db.execute(
            """SELECT c.*, u.nombre AS inicio_por,
                      (SELECT COUNT(*) FROM conteo_items WHERE conteo_id = c.id) AS total
               FROM conteos c JOIN usuarios u ON u.id = c.operador_inicio_id
               ORDER BY c.id DESC LIMIT 20"""
        ).fetchall()
    return render_template("conteo/inicio.html", historial=historial)


@bp.route("/iniciar", methods=["POST"])
@requiere_admin
def iniciar():
    db = get_db()
    try:
        db.execute("INSERT INTO conteos (operador_inicio_id) VALUES (?)", (current_user.id,))
        db.commit()
        flash("Conteo iniciado. Escanea todo lo que tengas físicamente en bodega.")
    except sqlite3.IntegrityError:
        db.rollback()
        flash("Ya hay un conteo abierto.", "error")
    return redirect(url_for("conteo.ver"))


@bp.route("/escanear", methods=["POST"])
@login_required
def escanear():
    db = get_db()
    conteo = _conteo_abierto(db)
    codigo = request.form.get("id_interno", "").strip().upper()

    def resp(ok, mensaje, fila=None):
        total = (
            db.execute("SELECT COUNT(*) AS n FROM conteo_items WHERE conteo_id = ?", (conteo["id"],)).fetchone()["n"]
            if conteo
            else 0
        )
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=ok, mensaje=mensaje, fila=fila, contador=total)
        flash(mensaje, "message" if ok else "error")
        return redirect(url_for("conteo.ver"))

    if conteo is None:
        return resp(False, "No hay un conteo abierto.")
    if not codigo:
        return redirect(url_for("conteo.ver"))

    equipo = db.execute(
        """SELECT e.id, e.id_interno, v.ubicacion FROM equipos e
           LEFT JOIN v_estado_actual v ON v.equipo_id = e.id WHERE e.id_interno = ?""",
        (codigo,),
    ).fetchone()
    if equipo is None:
        return resp(False, f"No existe ningún equipo con el código '{codigo}'.")

    try:
        db.execute(
            "INSERT INTO conteo_items (conteo_id, equipo_id, operador_id) VALUES (?, ?, ?)",
            (conteo["id"], equipo["id"], current_user.id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return resp(False, f"{equipo['id_interno']} ya estaba contado.")

    sistema = ETIQUETA_UBICACION.get(equipo["ubicacion"], "-")
    return resp(
        True,
        f"Contado: {equipo['id_interno']} (el sistema lo tiene {sistema}).",
        [datetime.now().strftime("%H:%M:%S"), equipo["id_interno"], sistema],
    )


@bp.route("/<int:conteo_id>/cerrar", methods=["POST"])
@requiere_admin
def cerrar(conteo_id):
    db = get_db()
    db.execute(
        "UPDATE conteos SET estado = 'cerrado', fecha_cierre = datetime('now','localtime') WHERE id = ? AND estado = 'abierto'",
        (conteo_id,),
    )
    db.commit()
    return redirect(url_for("conteo.resultado", conteo_id=conteo_id))


@bp.route("/<int:conteo_id>")
@requiere_admin
def resultado(conteo_id):
    db = get_db()
    conteo = db.execute("SELECT * FROM conteos WHERE id = ?", (conteo_id,)).fetchone()
    if conteo is None:
        flash("Ese conteo no existe.", "error")
        return redirect(url_for("conteo.ver"))
    escaneados, ok, corregir, fuera, faltantes = _clasificar(db, conteo_id)
    return render_template(
        "conteo/resultado.html",
        conteo=conteo,
        total=len(escaneados),
        ok=ok,
        corregir=corregir,
        fuera=fuera,
        faltantes=faltantes,
    )


@bp.route("/<int:conteo_id>/ajustar", methods=["POST"])
@requiere_admin
def ajustar(conteo_id):
    db = get_db()
    conteo = db.execute("SELECT * FROM conteos WHERE id = ?", (conteo_id,)).fetchone()
    if conteo is None or conteo["estado"] != "cerrado" or conteo["ajuste_aplicado"]:
        flash("Este conteo no se puede ajustar (no está cerrado o ya se ajustó).", "error")
        return redirect(url_for("conteo.ver"))

    _, _, corregir, _, _ = _clasificar(db, conteo_id)
    nota = f"Ajuste por conteo físico #{conteo_id}."
    for e in corregir:
        db.execute(
            "INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo, condicion) VALUES (?, ?, ?, 'retorno', ?)",
            (e["id"], e["ruta_actual_id"], current_user.id, nota),
        )
        db.execute(
            """UPDATE incidencias SET estado = 'resuelta', resolucion_nota = ?
               WHERE equipo_id = ? AND estado = 'abierta'""",
            (nota, e["id"]),
        )
    db.execute("UPDATE conteos SET ajuste_aplicado = 1 WHERE id = ?", (conteo_id,))
    db.commit()
    flash(f"Ajuste aplicado: {len(corregir)} equipo(s) registrados como retornados a bodega.")
    return redirect(url_for("conteo.resultado", conteo_id=conteo_id))
