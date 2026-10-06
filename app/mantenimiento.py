from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from .auth import requiere_admin
from .db import get_db

bp = Blueprint("mantenimiento", __name__, url_prefix="/mantenimiento")


@bp.route("/")
@requiere_admin
def ver():
    db = get_db()

    en_reparacion = db.execute(
        """SELECT m.id, e.id_interno, e.tipo, m.fecha_envio, m.motivo,
                  CAST(julianday('now','localtime') - julianday(m.fecha_envio) AS INTEGER) AS dias
           FROM mantenimientos m
           JOIN equipos e ON e.id = m.equipo_id
           WHERE m.estado = 'en_reparacion'
           ORDER BY m.fecha_envio"""
    ).fetchall()

    historial = db.execute(
        """SELECT m.id, e.id_interno, m.fecha_envio, m.fecha_retorno, m.motivo, m.resultado,
                  CAST(julianday(m.fecha_retorno) - julianday(m.fecha_envio) AS INTEGER) AS dias
           FROM mantenimientos m
           JOIN equipos e ON e.id = m.equipo_id
           WHERE m.estado = 'resuelto'
           ORDER BY m.fecha_retorno DESC
           LIMIT 100"""
    ).fetchall()

    return render_template("mantenimiento/ver.html", en_reparacion=en_reparacion, historial=historial)


@bp.route("/enviar", methods=["POST"])
@requiere_admin
def enviar():
    id_interno = request.form.get("id_interno", "").strip().upper()
    motivo = request.form.get("motivo", "").strip() or None
    db = get_db()

    if not id_interno:
        return redirect(url_for("mantenimiento.ver"))

    equipo = db.execute("SELECT * FROM equipos WHERE id_interno = ?", (id_interno,)).fetchone()
    if equipo is None:
        flash(f"No existe ningún equipo con el código '{id_interno}'.", "error")
        return redirect(url_for("mantenimiento.ver"))
    if equipo["estado"] == "reparacion":
        flash(f"{id_interno} ya está en mantenimiento.", "error")
        return redirect(url_for("mantenimiento.ver"))

    # Un equipo no puede estar "en ruta" y "en reparación" a la vez -- si
    # tenía una salida activa, se le registra el retorno automáticamente
    # antes de mandarlo a mantenimiento.
    ultimo = db.execute(
        "SELECT ruta_id, tipo FROM movimientos WHERE equipo_id = ? ORDER BY id DESC LIMIT 1", (equipo["id"],)
    ).fetchone()
    if ultimo and ultimo["tipo"] == "salida":
        db.execute(
            """INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo, condicion)
               VALUES (?, ?, ?, 'retorno', ?)""",
            (equipo["id"], ultimo["ruta_id"], current_user.id, "Retorno automático: el equipo se envió a mantenimiento."),
        )

    db.execute(
        "INSERT INTO mantenimientos (equipo_id, motivo, operador_envio_id) VALUES (?, ?, ?)",
        (equipo["id"], motivo, current_user.id),
    )
    db.execute("UPDATE equipos SET estado = 'reparacion' WHERE id = ?", (equipo["id"],))
    db.commit()
    flash(f"{id_interno} enviado a mantenimiento.")
    return redirect(url_for("mantenimiento.ver"))


@bp.route("/<int:mantenimiento_id>/regreso", methods=["POST"])
@requiere_admin
def regreso(mantenimiento_id):
    resultado = request.form.get("resultado", "").strip() or None
    nuevo_estado = request.form.get("nuevo_estado", "activo")
    if nuevo_estado not in ("activo", "baja"):
        nuevo_estado = "activo"

    db = get_db()
    mant = db.execute(
        "SELECT * FROM mantenimientos WHERE id = ? AND estado = 'en_reparacion'", (mantenimiento_id,)
    ).fetchone()
    if mant is None:
        flash("Ese mantenimiento no existe o ya se cerró.", "error")
        return redirect(url_for("mantenimiento.ver"))

    db.execute(
        """UPDATE mantenimientos SET estado = 'resuelto', fecha_retorno = datetime('now','localtime'),
               resultado = ?, operador_retorno_id = ? WHERE id = ?""",
        (resultado, current_user.id, mantenimiento_id),
    )
    db.execute("UPDATE equipos SET estado = ? WHERE id = ?", (nuevo_estado, mant["equipo_id"]))
    db.commit()
    flash("Equipo regresado de mantenimiento.")
    return redirect(url_for("mantenimiento.ver"))
