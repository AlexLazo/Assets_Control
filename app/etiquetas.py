import io

from flask import Blueprint, Response, abort, render_template, request

import generar_etiquetas

from .auth import requiere_permiso
from .db import get_db

bp = Blueprint("etiquetas", __name__, url_prefix="/admin/etiquetas")


@bp.route("/")
@requiere_permiso("etiquetas")
def ver():
    db = get_db()
    total = db.execute("SELECT COUNT(*) AS n FROM equipos").fetchone()["n"]
    total_telefonos = db.execute("SELECT COUNT(*) AS n FROM equipos WHERE tipo='telefono'").fetchone()["n"]
    total_impresoras = db.execute("SELECT COUNT(*) AS n FROM equipos WHERE tipo='impresora'").fetchone()["n"]
    return render_template(
        "etiquetas/ver.html", total=total, total_telefonos=total_telefonos, total_impresoras=total_impresoras
    )


@bp.route("/pdf")
@requiere_permiso("etiquetas")
def pdf():
    tipo = request.args.get("tipo", "todos")
    solo_revision = request.args.get("revision") == "1"

    condiciones = []
    parametros = []
    if tipo in ("telefono", "impresora"):
        condiciones.append("e.tipo = ?")
        parametros.append(tipo)
    if solo_revision:
        condiciones.append("e.requiere_revision = 1")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    db = get_db()
    equipos = db.execute(
        f"{generar_etiquetas.EQUIPOS_SQL} {where} ORDER BY e.id_interno", parametros
    ).fetchall()
    if not equipos:
        abort(404, "No hay equipos que coincidan con ese filtro.")

    buffer = io.BytesIO()
    generar_etiquetas.generar_pdf(equipos, destino=buffer)
    buffer.seek(0)
    return Response(
        buffer.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=etiquetas_qr.pdf"},
    )


@bp.route("/<id_interno>.png")
@requiere_permiso("etiquetas")
def png(id_interno):
    db = get_db()
    equipo = db.execute("SELECT id_interno FROM equipos WHERE id_interno = ?", (id_interno,)).fetchone()
    if equipo is None:
        abort(404)
    datos_png = generar_etiquetas.generar_qr_png_bytes(equipo["id_interno"])
    return Response(datos_png, mimetype="image/png")
