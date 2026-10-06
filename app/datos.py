import shutil
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

import comun
import importar_catalogo

from .auth import requiere_admin
from .db import get_db

bp = Blueprint("datos", __name__, url_prefix="/admin/datos")

ARCHIVOS = {
    "telefonos": comun.ARCHIVO_TELEFONOS,
    "impresoras": comun.ARCHIVO_IMPRESORAS,
}


@bp.route("/")
@requiere_admin
def ver():
    telefonos = comun.cargar_telefonos()
    vista = comun.construir_vista_impresoras()["vista"]

    resumen = {
        "total_telefonos": len(telefonos),
        "telefonos_duplicados": int(telefonos["duplicado"].sum()),
        "total_impresoras": len(vista),
        "impresoras_requieren_revision": int(vista["requiere_revision"].sum()),
    }

    fechas_archivos = {
        clave: datetime.fromtimestamp(ruta.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
        for clave, ruta in ARCHIVOS.items()
        if ruta.exists()
    }

    return render_template("datos/ver.html", resumen=resumen, fechas_archivos=fechas_archivos)


@bp.route("/subir", methods=["POST"])
@requiere_admin
def subir():
    tipo = request.form.get("tipo")
    archivo = request.files.get("archivo")

    if tipo not in ARCHIVOS:
        flash("Tipo de archivo no reconocido.", "error")
        return redirect(url_for("datos.ver"))
    if not archivo or not archivo.filename:
        flash("Selecciona un archivo .xlsx antes de subir.", "error")
        return redirect(url_for("datos.ver"))
    if not archivo.filename.lower().endswith(".xlsx"):
        flash("El archivo debe ser un .xlsx.", "error")
        return redirect(url_for("datos.ver"))

    destino = ARCHIVOS[tipo]
    if destino.exists():
        respaldo = destino.with_name(f"{destino.stem}_respaldo_{datetime.now():%Y%m%d_%H%M%S}{destino.suffix}")
        shutil.copy2(destino, respaldo)

    archivo.save(destino)
    flash(f"'{destino.name}' actualizado. Revisa el resumen abajo y usa 'Actualizar catálogo' cuando quieras aplicarlo a la base.")
    return redirect(url_for("datos.ver"))


@bp.route("/actualizar-catalogo", methods=["POST"])
@requiere_admin
def actualizar_catalogo():
    db = get_db()
    resultado = importar_catalogo.actualizar_o_agregar(db, current_user.id)
    db.commit()
    flash(
        f"Catálogo actualizado: {resultado['telefonos_nuevos']} teléfono(s) nuevo(s), "
        f"{resultado['impresoras_nuevas']} impresora(s) nueva(s), "
        f"{resultado['impresoras_actualizadas']} impresora(s) existentes con datos actualizados. "
        "Ningún id_interno existente cambió."
    )
    return redirect(url_for("datos.ver"))


@bp.route("/reiniciar", methods=["POST"])
@requiere_admin
def reiniciar():
    if request.form.get("confirmacion", "").strip().upper() != "BORRAR":
        flash("Escribe BORRAR (en mayúsculas) para confirmar. No se hizo ningún cambio.", "error")
        return redirect(url_for("datos.ver"))

    db = get_db()
    db.commit()
    # La base usa journal_mode=WAL: parte de lo ya confirmado puede seguir
    # viviendo en el archivo -wal en vez del .db principal. Sin este
    # checkpoint, una copia directa del .db podría quedar incompleta.
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    origen = Path(current_app.config["DATABASE"])
    respaldo = origen.with_name(f"activos_antes_de_reiniciar_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(origen, respaldo)

    db.execute("DELETE FROM incidencias")
    db.execute("DELETE FROM movimientos")
    db.execute("DELETE FROM equipos")
    db.execute("DELETE FROM rutas")
    db.commit()

    flash(
        f"Base reiniciada: se borraron equipos, rutas, movimientos e incidencias. "
        f"Los usuarios NO se tocaron. Respaldo guardado como {respaldo.name} por si hace falta recuperar algo.",
        "error",
    )
    return redirect(url_for("datos.ver"))
