import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

import comun
import importar_catalogo

from . import migraciones, respaldos
from .auth import requiere_permiso
from .db import get_db

bp = Blueprint("datos", __name__, url_prefix="/admin/datos")

ARCHIVOS = {
    "telefonos": comun.ARCHIVO_TELEFONOS,
    "impresoras": comun.ARCHIVO_IMPRESORAS,
}


@bp.route("/")
@requiere_permiso("datos")
def ver():
    resumen = None
    try:
        telefonos = comun.cargar_telefonos()
        vista = comun.construir_vista_impresoras()["vista"]
        resumen = {
            "total_telefonos": len(telefonos),
            "telefonos_duplicados": int(telefonos["duplicado"].sum()),
            "total_impresoras": len(vista),
            "impresoras_requieren_revision": int(vista["requiere_revision"].sum()),
        }
    except Exception:
        # En el servidor los Excel de origen pueden no existir (la base ya
        # está cargada); la pantalla no debe fallar por eso.
        resumen = None

    fechas_archivos = {
        clave: datetime.fromtimestamp(ruta.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
        for clave, ruta in ARCHIVOS.items()
        if ruta.exists()
    }
    db = get_db()
    totales = {
        "equipos": db.execute("SELECT COUNT(*) AS n FROM equipos").fetchone()["n"],
        "movimientos": db.execute("SELECT COUNT(*) AS n FROM movimientos").fetchone()["n"],
    }
    lista = [
        {"nombre": r.name, "fecha": datetime.fromtimestamp(r.stat().st_mtime).strftime("%d/%m/%Y %H:%M"), "mb": round(r.stat().st_size / 1_048_576, 2)}
        for r in respaldos.listar(current_app)[:15]
    ]
    return render_template(
        "datos/ver.html",
        resumen=resumen,
        fechas_archivos=fechas_archivos,
        totales=totales,
        respaldos=lista,
        permitir_reinicio=current_app.config["PERMITIR_REINICIO"],
        produccion=current_app.config["PRODUCCION"],
        persistente=current_app.config["PERSISTENTE"],
        carpeta=current_app.config["DATA_DIR"],
    )


@bp.route("/subir", methods=["POST"])
@requiere_permiso("datos")
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
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        respaldo = destino.with_name(f"{destino.stem}_respaldo_{datetime.now():%Y%m%d_%H%M%S}{destino.suffix}")
        shutil.copy2(destino, respaldo)

    archivo.save(destino)
    flash(f"'{destino.name}' actualizado. Revisa el resumen abajo y usa 'Actualizar catálogo' cuando quieras aplicarlo a la base.")
    return redirect(url_for("datos.ver"))


@bp.route("/actualizar-catalogo", methods=["POST"])
@requiere_permiso("datos")
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


# ---------------------------------------------------------------- respaldos

@bp.route("/respaldo/ahora")
@requiere_permiso("datos")
def respaldo_ahora():
    """Genera un respaldo consistente de la base y lo descarga."""
    destino = respaldos.hacer_respaldo(current_app, "manual")
    return send_file(destino, as_attachment=True, download_name=f"activos_{datetime.now():%Y%m%d_%H%M%S}.db")


@bp.route("/respaldo/<nombre>")
@requiere_permiso("datos")
def respaldo_descargar(nombre):
    archivo = respaldos.carpeta(current_app) / Path(nombre).name
    if not archivo.exists() or not archivo.name.startswith(respaldos.PREFIJO) or archivo.suffix != ".db":
        abort(404)
    return send_file(archivo, as_attachment=True, download_name=archivo.name)


@bp.route("/restaurar", methods=["POST"])
@requiere_permiso("datos")
def restaurar():
    """Reemplaza el contenido de la base por el de un .db subido (por ejemplo
    la base de tu PC, para cargar los datos por primera vez en Railway)."""
    if request.form.get("confirmacion", "").strip().upper() != "RESTAURAR":
        flash("Escribe RESTAURAR (en mayúsculas) para confirmar. No se hizo ningún cambio.", "error")
        return redirect(url_for("datos.ver"))
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        flash("Selecciona el archivo .db a restaurar.", "error")
        return redirect(url_for("datos.ver"))

    with tempfile.TemporaryDirectory(dir=current_app.config["DATA_DIR"]) as tmp:
        temporal = Path(tmp) / "subida.db"
        archivo.save(temporal)
        try:
            con = sqlite3.connect(temporal)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("el archivo está dañado (falló la verificación de integridad)")
                tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                con.close()
            faltan = migraciones.TABLAS_BASE_V1 - tablas
            if faltan:
                raise ValueError(f"no parece una base de este sistema (faltan tablas: {sorted(faltan)})")
        except (sqlite3.Error, ValueError) as e:
            flash(f"No se restauró nada: {e}.", "error")
            return redirect(url_for("datos.ver"))

        get_db().commit()
        previo = respaldos.hacer_respaldo(current_app, "antes_de_restaurar")
        respaldos.copiar(temporal, current_app.config["DATABASE"])

    version = migraciones.actualizar(current_app.config["DATABASE"])
    flash(
        f"Base restaurada (esquema v{version}). Se guardó un respaldo de lo que había antes: {previo.name}. "
        "Tu sesión puede dejar de ser válida si los usuarios cambiaron: vuelve a iniciar sesión.",
    )
    return redirect(url_for("auth.login"))


@bp.route("/reiniciar", methods=["POST"])
@requiere_permiso("datos")
def reiniciar():
    if not current_app.config["PERMITIR_REINICIO"]:
        flash("Reiniciar la base está deshabilitado en este entorno (para evitar borrados accidentales en producción).", "error")
        return redirect(url_for("datos.ver"))
    if request.form.get("confirmacion", "").strip().upper() != "BORRAR":
        flash("Escribe BORRAR (en mayúsculas) para confirmar. No se hizo ningún cambio.", "error")
        return redirect(url_for("datos.ver"))

    db = get_db()
    db.commit()
    respaldo = respaldos.hacer_respaldo(current_app, "antes_de_reiniciar")

    for tabla in ("conteo_items", "conteos", "mantenimientos", "incidencias", "movimientos", "equipos", "rutas"):
        db.execute(f"DELETE FROM {tabla}")
    db.commit()

    flash(
        "Base reiniciada: se borraron equipos, rutas, movimientos, incidencias, mantenimientos y conteos. "
        f"Los usuarios NO se tocaron. Respaldo guardado como {respaldo.name} por si hace falta recuperar algo.",
        "error",
    )
    return redirect(url_for("datos.ver"))
