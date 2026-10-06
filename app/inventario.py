import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from .auth import requiere_admin
from .db import get_db

bp = Blueprint("inventario", __name__, url_prefix="/inventario")


def _siguiente_id_interno(db, tipo: str) -> str:
    prefijo = "TEL" if tipo == "telefono" else "IMP"
    filas = db.execute("SELECT id_interno FROM equipos WHERE id_interno LIKE ?", (f"{prefijo}-%",)).fetchall()
    numeros = [int(f["id_interno"].split("-")[1]) for f in filas]
    siguiente = (max(numeros) + 1) if numeros else 1
    return f"{prefijo}-{siguiente:03d}"


@bp.route("/")
@requiere_admin
def listado():
    db = get_db()
    solo_revision = request.args.get("revision") == "1"
    solo_sin_ruta = request.args.get("sin_ruta") == "1"
    buscar = request.args.get("buscar", "").strip()

    condiciones = []
    parametros = []
    if solo_revision:
        condiciones.append("e.requiere_revision = 1")
    if solo_sin_ruta:
        condiciones.append("e.ruta_asignada_id IS NULL")
    if buscar:
        condiciones.append("(e.id_interno LIKE ? OR e.serial_fabrica LIKE ? OR e.numero_telefono LIKE ?)")
        comodin = f"%{buscar.upper()}%"
        parametros += [comodin, comodin, comodin]
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    equipos = db.execute(
        f"""SELECT e.*, v.ubicacion, ra.codigo AS ruta_asignada_codigo
            FROM equipos e
            LEFT JOIN v_estado_actual v ON v.equipo_id = e.id
            LEFT JOIN rutas ra ON ra.id = e.ruta_asignada_id
            {where}
            ORDER BY e.requiere_revision DESC, e.id_interno""",
        parametros,
    ).fetchall()

    total_revision = db.execute("SELECT COUNT(*) AS n FROM equipos WHERE requiere_revision = 1").fetchone()["n"]
    total_sin_ruta = db.execute("SELECT COUNT(*) AS n FROM equipos WHERE ruta_asignada_id IS NULL").fetchone()["n"]

    return render_template(
        "inventario/listado.html",
        equipos=equipos,
        solo_revision=solo_revision,
        solo_sin_ruta=solo_sin_ruta,
        buscar=buscar,
        total_revision=total_revision,
        total_sin_ruta=total_sin_ruta,
    )


@bp.route("/nuevo", methods=["GET", "POST"])
@requiere_admin
def nuevo():
    db = get_db()
    if request.method == "POST":
        tipo = request.form.get("tipo")
        if tipo not in ("telefono", "impresora"):
            flash("Selecciona un tipo de equipo válido.", "error")
            return redirect(url_for("inventario.nuevo"))

        id_interno = _siguiente_id_interno(db, tipo)
        db.execute(
            """INSERT INTO equipos (id_interno, tipo, serial_fabrica, numero_telefono, modelo, fabricante, estado, notas, ruta_asignada_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                id_interno,
                tipo,
                (request.form.get("serial_fabrica") or None) if tipo == "impresora" else None,
                (request.form.get("numero_telefono") or None) if tipo == "telefono" else None,
                request.form.get("modelo") or None,
                request.form.get("fabricante") or None,
                request.form.get("estado") or "activo",
                request.form.get("notas") or None,
                request.form.get("ruta_asignada_id", type=int) or None,
            ),
        )
        db.commit()
        flash(f"Equipo {id_interno} creado. No olvides imprimirle su etiqueta QR en Admin → Etiquetas.")
        return redirect(url_for("inventario.editar", id_interno=id_interno))

    rutas = db.execute("SELECT id, codigo FROM rutas WHERE activa = 1 ORDER BY codigo").fetchall()
    return render_template("inventario/nuevo.html", rutas=rutas)


@bp.route("/<id_interno>/editar", methods=["GET", "POST"])
@requiere_admin
def editar(id_interno):
    db = get_db()
    equipo = db.execute("SELECT * FROM equipos WHERE id_interno = ?", (id_interno,)).fetchone()
    if equipo is None:
        flash(f"No existe el equipo {id_interno}.", "error")
        return redirect(url_for("inventario.listado"))

    if request.method == "POST":
        nuevo_estado = request.form.get("estado")

        # Si el estado cambia hacia/desde 'reparacion' aquí (en vez de por
        # la pantalla de Mantenimiento), se mantiene sincronizada la tabla
        # mantenimientos igual -- para que el historial no dependa de que
        # todos usen siempre la misma pantalla.
        if nuevo_estado == "reparacion" and equipo["estado"] != "reparacion":
            ultimo = db.execute(
                "SELECT ruta_id, tipo FROM movimientos WHERE equipo_id = ? ORDER BY id DESC LIMIT 1", (equipo["id"],)
            ).fetchone()
            if ultimo and ultimo["tipo"] == "salida":
                db.execute(
                    """INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo, condicion)
                       VALUES (?, ?, ?, 'retorno', ?)""",
                    (equipo["id"], ultimo["ruta_id"], current_user.id, "Retorno automático: el equipo se marcó en reparación."),
                )
            db.execute(
                "INSERT INTO mantenimientos (equipo_id, motivo, operador_envio_id) VALUES (?, 'Marcado manualmente desde Inventario.', ?)",
                (equipo["id"], current_user.id),
            )
        elif nuevo_estado != "reparacion" and equipo["estado"] == "reparacion":
            db.execute(
                """UPDATE mantenimientos SET estado = 'resuelto', fecha_retorno = datetime('now','localtime'),
                       resultado = 'Cerrado manualmente desde Inventario.', operador_retorno_id = ?
                   WHERE equipo_id = ? AND estado = 'en_reparacion'""",
                (current_user.id, equipo["id"]),
            )

        db.execute(
            """UPDATE equipos SET modelo = ?, fabricante = ?, serial_fabrica = ?, numero_telefono = ?,
                   estado = ?, notas = ?, requiere_revision = ?, ruta_asignada_id = ? WHERE id = ?""",
            (
                request.form.get("modelo") or None,
                request.form.get("fabricante") or None,
                request.form.get("serial_fabrica") or None,
                request.form.get("numero_telefono") or None,
                nuevo_estado,
                request.form.get("notas") or None,
                1 if request.form.get("requiere_revision") else 0,
                request.form.get("ruta_asignada_id", type=int) or None,
                equipo["id"],
            ),
        )
        db.commit()
        flash(f"{id_interno} actualizado.")
        return redirect(url_for("inventario.listado"))

    rutas = db.execute("SELECT id, codigo FROM rutas WHERE activa = 1 ORDER BY codigo").fetchall()
    return render_template("inventario/editar.html", equipo=equipo, rutas=rutas)


@bp.route("/<id_interno>/eliminar", methods=["POST"])
@requiere_admin
def eliminar(id_interno):
    db = get_db()
    equipo = db.execute("SELECT id FROM equipos WHERE id_interno = ?", (id_interno,)).fetchone()
    if equipo is None:
        flash(f"No existe el equipo {id_interno}.", "error")
        return redirect(url_for("inventario.listado"))
    try:
        db.execute("DELETE FROM equipos WHERE id = ?", (equipo["id"],))
        db.commit()
        flash(f"{id_interno} eliminado.")
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"No se puede eliminar {id_interno}: ya tiene movimientos registrados. Márcalo como 'Dado de baja' en su lugar.", "error")
    return redirect(url_for("inventario.listado"))


@bp.route("/rutas", methods=["GET", "POST"])
@requiere_admin
def rutas():
    db = get_db()
    if request.method == "POST":
        ruta_id = request.form.get("ruta_id", type=int)
        nueva_activa = request.form.get("nueva_activa", type=int)
        db.execute("UPDATE rutas SET activa = ? WHERE id = ?", (nueva_activa, ruta_id))
        db.commit()
        return redirect(url_for("inventario.rutas"))

    lista = db.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM v_estado_actual v
                   WHERE v.ruta_actual_id = r.id AND v.ubicacion = 'en_ruta') AS equipos_en_ruta
           FROM rutas r
           ORDER BY r.codigo"""
    ).fetchall()
    return render_template("inventario/rutas.html", rutas=lista)


@bp.route("/rutas/nueva", methods=["GET", "POST"])
@requiere_admin
def nueva_ruta():
    db = get_db()
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip().upper()
        supervisor = request.form.get("supervisor", "").strip() or None
        if not codigo:
            flash("El código de ruta no puede estar vacío.", "error")
            return redirect(url_for("inventario.nueva_ruta"))
        if db.execute("SELECT id FROM rutas WHERE codigo = ?", (codigo,)).fetchone():
            flash(f"Ya existe la ruta {codigo}.", "error")
            return redirect(url_for("inventario.nueva_ruta"))
        db.execute("INSERT INTO rutas (codigo, supervisor) VALUES (?, ?)", (codigo, supervisor))
        db.commit()
        flash(f"Ruta {codigo} creada.")
        return redirect(url_for("inventario.rutas"))

    return render_template("inventario/nueva_ruta.html")


@bp.route("/rutas/<int:ruta_id>/editar", methods=["GET", "POST"])
@requiere_admin
def editar_ruta(ruta_id):
    db = get_db()
    ruta = db.execute("SELECT * FROM rutas WHERE id = ?", (ruta_id,)).fetchone()
    if ruta is None:
        flash("Esa ruta no existe.", "error")
        return redirect(url_for("inventario.rutas"))

    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip().upper()
        supervisor = request.form.get("supervisor", "").strip() or None
        if not codigo:
            flash("El código no puede estar vacío.", "error")
            return render_template("inventario/editar_ruta.html", ruta=ruta)
        existente = db.execute("SELECT id FROM rutas WHERE codigo = ? AND id != ?", (codigo, ruta_id)).fetchone()
        if existente:
            flash(f"Ya existe otra ruta con el código {codigo}.", "error")
            return render_template("inventario/editar_ruta.html", ruta=ruta)
        db.execute("UPDATE rutas SET codigo = ?, supervisor = ? WHERE id = ?", (codigo, supervisor, ruta_id))
        db.commit()
        flash("Ruta actualizada.")
        return redirect(url_for("inventario.rutas"))

    return render_template("inventario/editar_ruta.html", ruta=ruta)


@bp.route("/rutas/<int:ruta_id>/eliminar", methods=["POST"])
@requiere_admin
def eliminar_ruta(ruta_id):
    db = get_db()
    ruta = db.execute("SELECT codigo FROM rutas WHERE id = ?", (ruta_id,)).fetchone()
    if ruta is None:
        flash("Esa ruta no existe.", "error")
        return redirect(url_for("inventario.rutas"))
    try:
        db.execute("DELETE FROM rutas WHERE id = ?", (ruta_id,))
        db.commit()
        flash(f"Ruta {ruta['codigo']} eliminada.")
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"No se puede eliminar {ruta['codigo']}: tiene movimientos registrados. Desactívala en su lugar.", "error")
    return redirect(url_for("inventario.rutas"))
