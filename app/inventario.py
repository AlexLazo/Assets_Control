import io
import re
import sqlite3
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from . import auditoria
from .auth import puede, requiere_permiso
from .db import get_db
from .migraciones import clave_supervisor
from .paginacion import paginar

bp = Blueprint("inventario", __name__, url_prefix="/inventario")


def _siguiente_id_interno(db, tipo: str) -> str:
    prefijo = "TEL" if tipo == "telefono" else "IMP"
    filas = db.execute("SELECT id_interno FROM equipos WHERE id_interno LIKE ?", (f"{prefijo}-%",)).fetchall()
    numeros = [int(f["id_interno"].split("-")[1]) for f in filas]
    siguiente = (max(numeros) + 1) if numeros else 1
    return f"{prefijo}-{siguiente:03d}"


# Días que lleva fuera un equipo en ruta (días completos desde su última salida).
DIAS_FUERA_SQL = "CASE WHEN v.ubicacion = 'en_ruta' THEN CAST(julianday('now','localtime') - julianday(v.desde) AS INTEGER) END"
ORDENES = {
    "": ("Pendientes de revisión primero, luego por ID", "e.requiere_revision DESC, e.id_interno"),
    "id": ("Por ID", "e.id_interno"),
    "dias": ("Más días fuera primero", f"({DIAS_FUERA_SQL}) IS NULL, {DIAS_FUERA_SQL} DESC, e.id_interno"),
    "ruta": ("Por ruta asignada", "ra.codigo IS NULL, ra.codigo, e.id_interno"),
}
ESTADOS = {"activo": "Activo", "reparacion": "En reparación", "baja": "Dado de baja"}

FROM_INVENTARIO = """FROM equipos e
            LEFT JOIN v_estado_actual v ON v.equipo_id = e.id
            LEFT JOIN rutas ra ON ra.id = e.ruta_asignada_id"""


def _filtros_inventario():
    """Lee los filtros de la URL y arma el WHERE (siempre con marcadores '?':
    lo que teclea el usuario viaja en `parametros`, nunca dentro del SQL)."""
    a = request.args
    f = {
        "buscar": a.get("buscar", "").strip(),
        "tipo": a.get("tipo", "").strip(),
        "estado": a.get("estado", "").strip(),
        "ubicacion": a.get("ubicacion", "").strip(),
        "ruta": a.get("ruta", "").strip(),
        "supervisor": a.get("supervisor", "").strip(),
        "revision": a.get("revision") == "1",
        "sin_ruta": a.get("sin_ruta") == "1",
        "orden": a.get("orden", "").strip(),
    }
    if f["orden"] not in ORDENES:
        f["orden"] = ""
    cond, par = [], []
    if f["revision"]:
        cond.append("e.requiere_revision = 1")
    if f["sin_ruta"]:
        cond.append("e.ruta_asignada_id IS NULL")
    if f["buscar"]:
        cond.append("(e.id_interno LIKE ? OR e.serial_fabrica LIKE ? OR e.numero_telefono LIKE ?)")
        par += [f"%{f['buscar'].upper()}%"] * 3
    if f["tipo"] in ("telefono", "impresora"):
        cond.append("e.tipo = ?")
        par.append(f["tipo"])
    if f["estado"] in ESTADOS:
        cond.append("e.estado = ?")
        par.append(f["estado"])
    if f["ubicacion"] in ("en_ruta", "en_bodega"):
        cond.append("v.ubicacion = ?")
        par.append(f["ubicacion"])
    if f["ruta"]:
        cond.append("ra.codigo LIKE ?")
        par.append(f"%{f['ruta'].upper()}%")
    if f["supervisor"]:
        cond.append("ra.supervisor LIKE ?")
        par.append(f"%{f['supervisor']}%")
    return f, (f"WHERE {' AND '.join(cond)}" if cond else ""), par


@bp.route("/")
@login_required
def listado():
    db = get_db()
    filtros, where, parametros = _filtros_inventario()

    equipos, pag = paginar(
        db,
        f"SELECT COUNT(*) {FROM_INVENTARIO} {where}",
        f"""SELECT e.*, v.ubicacion, v.desde, ra.codigo AS ruta_asignada_codigo, ra.supervisor AS supervisor,
                   {DIAS_FUERA_SQL} AS dias_fuera
            {FROM_INVENTARIO} {where}
            ORDER BY {ORDENES[filtros['orden']][1]}""",
        parametros,
    )

    total_revision = db.execute("SELECT COUNT(*) AS n FROM equipos WHERE requiere_revision = 1").fetchone()["n"]
    total_sin_ruta = db.execute("SELECT COUNT(*) AS n FROM equipos WHERE ruta_asignada_id IS NULL").fetchone()["n"]
    total_equipos = db.execute("SELECT COUNT(*) AS n FROM equipos").fetchone()["n"]
    rutas = db.execute("SELECT id, codigo FROM rutas WHERE activa = 1 ORDER BY codigo").fetchall() if puede("inventario_editar") else []

    return render_template(
        "inventario/listado.html",
        equipos=equipos, pag=pag, filtros=filtros, ordenes={k: v[0] for k, v in ORDENES.items()}, estados=ESTADOS,
        total_revision=total_revision, total_sin_ruta=total_sin_ruta, total_equipos=total_equipos, rutas=rutas,
        volver=request.full_path.rstrip("?"),
    )


@bp.route("/exportar")
@requiere_permiso("inventario_editar")
def exportar():
    """Excel del inventario con los mismos filtros de la pantalla (sin PIN)."""
    db = get_db()
    filtros, where, parametros = _filtros_inventario()
    filas = db.execute(
        f"""SELECT e.id_interno, e.tipo, e.serial_fabrica, e.numero_telefono, e.modelo, e.fabricante, e.estado,
                   e.requiere_revision, e.notas, v.ubicacion, {DIAS_FUERA_SQL} AS dias_fuera,
                   ra.codigo AS ruta, ra.supervisor AS supervisor
            {FROM_INVENTARIO} {where}
            ORDER BY {ORDENES[filtros['orden']][1]}""",
        parametros,
    ).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"
    cabecera = ["ID", "Tipo", "Serial", "Teléfono", "Modelo", "Fabricante", "Estado", "Ubicación", "Días fuera",
                "Ruta asignada", "Supervisor", "Requiere revisión", "Notas"]
    ws.append(cabecera)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F2937")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for f in filas:
        ws.append([
            f["id_interno"], "Teléfono" if f["tipo"] == "telefono" else "Impresora", f["serial_fabrica"], f["numero_telefono"],
            f["modelo"], f["fabricante"], ESTADOS.get(f["estado"], f["estado"]),
            "En ruta" if f["ubicacion"] == "en_ruta" else "En bodega", f["dias_fuera"],
            f["ruta"], f["supervisor"], "Sí" if f["requiere_revision"] else "", f["notas"],
        ])
    ws.freeze_panes = "A2"
    if filas:
        ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        ancho = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(ancho + 2, 10), 42)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer, as_attachment=True, download_name=f"inventario_{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _volver() -> str:
    """A dónde regresar tras una acción: solo rutas internas del propio sitio."""
    destino = request.form.get("volver", "")
    if destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return destino
    return url_for("inventario.listado")


@bp.route("/asignar-ruta", methods=["POST"])
@requiere_permiso("inventario_editar")
def asignar_ruta():
    """Asigna (o quita) la ruta de varios equipos a la vez. Solo Admin y Super Admin."""
    db = get_db()
    ids = list(dict.fromkeys(i.strip().upper() for i in request.form.getlist("ids") if i.strip()))[:1000]
    seleccion = request.form.get("ruta_id", "")
    if not ids:
        flash("Marca al menos un equipo para asignarle ruta.", "error")
        return redirect(_volver())

    if seleccion == "quitar":
        ruta = None
    elif seleccion.isdigit():
        ruta = db.execute("SELECT id, codigo FROM rutas WHERE id = ? AND activa = 1", (int(seleccion),)).fetchone()
        if ruta is None:
            flash("Esa ruta no existe o está desactivada.", "error")
            return redirect(_volver())
    else:
        flash("Elige la ruta que quieres asignar (o 'Quitar ruta').", "error")
        return redirect(_volver())

    destino_id = ruta["id"] if ruta else None
    destino_txt = ruta["codigo"] if ruta else None
    cambiados = 0
    for id_interno in ids:
        e = db.execute(
            """SELECT e.id, ra.codigo AS ruta FROM equipos e LEFT JOIN rutas ra ON ra.id = e.ruta_asignada_id
               WHERE e.id_interno = ?""",
            (id_interno,),
        ).fetchone()
        if e is None or e["ruta"] == destino_txt:
            continue
        db.execute("UPDATE equipos SET ruta_asignada_id = ? WHERE id = ?", (destino_id, e["id"]))
        auditoria.registrar(
            db, "equipo_editar", id_interno,
            f"Ruta asignada: {e['ruta'] or '(vacío)'} → {destino_txt or '(vacío)'} (asignación masiva)",
        )
        cambiados += 1
    db.commit()

    if cambiados:
        accion = f"asignada la ruta {destino_txt}" if ruta else "quitada la ruta"
        flash(f"Listo: {cambiados} equipo(s) con {accion}." + (f" {len(ids) - cambiados} ya la tenían." if cambiados < len(ids) else ""))
    else:
        flash("Ningún equipo cambió: ya tenían esa ruta (o no existen).")
    return redirect(_volver())


@bp.route("/<id_interno>/ficha")
@login_required
def ficha(id_interno):
    """Hoja de vida del equipo: dónde está, y todo lo que le ha pasado."""
    db = get_db()
    equipo = db.execute(
        """SELECT e.*, ra.codigo AS ruta_asignada_codigo, ra.supervisor AS supervisor
           FROM equipos e LEFT JOIN rutas ra ON ra.id = e.ruta_asignada_id WHERE e.id_interno = ?""",
        (id_interno.strip().upper(),),
    ).fetchone()
    if equipo is None:
        flash(f"No existe el equipo {id_interno}.", "error")
        return redirect(url_for("inventario.listado"))

    actual = db.execute(
        f"""SELECT v.ubicacion, v.desde, r.codigo AS ruta, r.supervisor AS supervisor, {DIAS_FUERA_SQL} AS dias_fuera
            FROM v_estado_actual v LEFT JOIN rutas r ON r.id = v.ruta_actual_id
            WHERE v.equipo_id = ?""",
        (equipo["id"],),
    ).fetchone()

    movimientos = db.execute(
        """SELECT m.timestamp, m.tipo, m.condicion, r.codigo AS ruta, o.nombre AS operador
           FROM movimientos m JOIN rutas r ON r.id = m.ruta_id JOIN usuarios o ON o.id = m.operador_id
           WHERE m.equipo_id = ? ORDER BY m.id DESC LIMIT 200""",
        (equipo["id"],),
    ).fetchall()
    mantenimientos = db.execute(
        """SELECT m.fecha_envio, m.fecha_retorno, m.motivo, m.resultado, m.estado,
                  CAST(julianday(COALESCE(m.fecha_retorno, datetime('now','localtime'))) - julianday(m.fecha_envio) AS INTEGER) AS dias,
                  u.nombre AS envio_por, u2.nombre AS retorno_por
           FROM mantenimientos m JOIN usuarios u ON u.id = m.operador_envio_id
           LEFT JOIN usuarios u2 ON u2.id = m.operador_retorno_id
           WHERE m.equipo_id = ? ORDER BY m.id DESC""",
        (equipo["id"],),
    ).fetchall()
    incidencias = db.execute(
        """SELECT i.fecha, i.estado, i.resolucion_nota, i.fecha_resolucion, r.codigo AS ruta, u.nombre AS resuelta_por
           FROM incidencias i JOIN rutas r ON r.id = i.ruta_id LEFT JOIN usuarios u ON u.id = i.resuelta_por_id
           WHERE i.equipo_id = ? ORDER BY i.id DESC""",
        (equipo["id"],),
    ).fetchall()
    cambios = []
    if puede("bitacora"):
        cambios = db.execute(
            "SELECT fecha, usuario_nombre, accion, detalle FROM auditoria WHERE objetivo = ? ORDER BY id DESC LIMIT 100",
            (equipo["id_interno"],),
        ).fetchall()

    return render_template(
        "inventario/ficha.html", equipo=equipo, actual=actual, movimientos=movimientos, mantenimientos=mantenimientos,
        incidencias=incidencias, cambios=cambios, acciones=auditoria.ACCIONES,
    )


@bp.route("/nuevo", methods=["GET", "POST"])
@requiere_permiso("inventario_editar")
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
        auditoria.registrar(db, "equipo_crear", id_interno, f"Tipo: {tipo}")
        db.commit()
        flash(f"Equipo {id_interno} creado. No olvides imprimirle su etiqueta QR en Admin → Etiquetas.")
        return redirect(url_for("inventario.editar", id_interno=id_interno))

    rutas = db.execute("SELECT id, codigo FROM rutas WHERE activa = 1 ORDER BY codigo").fetchall()
    return render_template("inventario/nuevo.html", rutas=rutas)


@bp.route("/<id_interno>/editar", methods=["GET", "POST"])
@requiere_permiso("inventario_editar")
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

        nuevos = {
            "modelo": request.form.get("modelo") or None,
            "fabricante": request.form.get("fabricante") or None,
            "serial_fabrica": request.form.get("serial_fabrica") or None,
            "numero_telefono": request.form.get("numero_telefono") or None,
            "estado": nuevo_estado,
            "notas": request.form.get("notas") or None,
            "requiere_revision": 1 if request.form.get("requiere_revision") else 0,
            "ruta_asignada_id": request.form.get("ruta_asignada_id", type=int) or None,
        }
        db.execute(
            """UPDATE equipos SET modelo = ?, fabricante = ?, serial_fabrica = ?, numero_telefono = ?,
                   estado = ?, notas = ?, requiere_revision = ?, ruta_asignada_id = ? WHERE id = ?""",
            (*nuevos.values(), equipo["id"]),
        )
        # La ruta se guarda como id; para la auditoría se muestra su código.
        codigos = {r["id"]: r["codigo"] for r in db.execute("SELECT id, codigo FROM rutas")}
        antes = dict(equipo, ruta_asignada_id=codigos.get(equipo["ruta_asignada_id"]))
        despues = dict(nuevos, ruta_asignada_id=codigos.get(nuevos["ruta_asignada_id"]))
        cambio = auditoria.cambios(antes, despues, {
            "modelo": "Modelo", "fabricante": "Fabricante", "serial_fabrica": "Serial", "numero_telefono": "Teléfono",
            "estado": "Estado", "notas": "Notas", "requiere_revision": "Requiere revisión", "ruta_asignada_id": "Ruta asignada",
        })
        if cambio:
            auditoria.registrar(db, "equipo_editar", id_interno, cambio)
        db.commit()
        flash(f"{id_interno} actualizado.")
        return redirect(url_for("inventario.listado"))

    rutas = db.execute("SELECT id, codigo FROM rutas WHERE activa = 1 ORDER BY codigo").fetchall()
    return render_template("inventario/editar.html", equipo=equipo, rutas=rutas)


@bp.route("/<id_interno>/eliminar", methods=["POST"])
@requiere_permiso("inventario_editar")
def eliminar(id_interno):
    db = get_db()
    equipo = db.execute("SELECT id FROM equipos WHERE id_interno = ?", (id_interno,)).fetchone()
    if equipo is None:
        flash(f"No existe el equipo {id_interno}.", "error")
        return redirect(url_for("inventario.listado"))
    try:
        db.execute("DELETE FROM equipos WHERE id = ?", (equipo["id"],))
        auditoria.registrar(db, "equipo_eliminar", id_interno)
        db.commit()
        flash(f"{id_interno} eliminado.")
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"No se puede eliminar {id_interno}: ya tiene movimientos registrados. Márcalo como 'Dado de baja' en su lugar.", "error")
    return redirect(url_for("inventario.listado"))


def _supervisor_canonico(db, texto: str, excepto_id: int | None = None) -> str | None:
    """Evita que el mismo supervisor quede escrito de dos formas (tildes,
    mayúsculas, espacios): si ya existe, se usa EXACTAMENTE como está escrito en
    otra ruta; si es nuevo, se guarda en mayúsculas como el resto."""
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if not texto:
        return None
    clave = clave_supervisor(texto)
    for fila in db.execute(
        "SELECT DISTINCT supervisor FROM rutas WHERE supervisor IS NOT NULL AND id IS NOT ?", (excepto_id,)
    ):
        if clave_supervisor(fila["supervisor"]) == clave:
            return fila["supervisor"]
    return texto.upper()


def _supervisores(db) -> list[str]:
    return [f["supervisor"] for f in db.execute(
        "SELECT DISTINCT supervisor FROM rutas WHERE supervisor IS NOT NULL ORDER BY supervisor"
    )]


@bp.route("/rutas", methods=["GET", "POST"])
@requiere_permiso("inventario_editar")
def rutas():
    db = get_db()
    if request.method == "POST":
        ruta_id = request.form.get("ruta_id", type=int)
        nueva_activa = request.form.get("nueva_activa", type=int)
        db.execute("UPDATE rutas SET activa = ? WHERE id = ?", (nueva_activa, ruta_id))
        ruta = db.execute("SELECT codigo FROM rutas WHERE id = ?", (ruta_id,)).fetchone()
        if ruta:
            auditoria.registrar(db, "ruta_estado", ruta["codigo"], "Activada" if nueva_activa else "Desactivada")
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
@requiere_permiso("inventario_editar")
def nueva_ruta():
    db = get_db()
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip().upper()
        supervisor = _supervisor_canonico(db, request.form.get("supervisor", ""))
        if not codigo:
            flash("El código de ruta no puede estar vacío.", "error")
            return redirect(url_for("inventario.nueva_ruta"))
        if db.execute("SELECT id FROM rutas WHERE codigo = ?", (codigo,)).fetchone():
            flash(f"Ya existe la ruta {codigo}.", "error")
            return redirect(url_for("inventario.nueva_ruta"))
        db.execute("INSERT INTO rutas (codigo, supervisor) VALUES (?, ?)", (codigo, supervisor))
        auditoria.registrar(db, "ruta_crear", codigo, f"Supervisor: {supervisor or '(ninguno)'}")
        db.commit()
        flash(f"Ruta {codigo} creada.")
        return redirect(url_for("inventario.rutas"))

    return render_template("inventario/nueva_ruta.html", supervisores=_supervisores(db))


@bp.route("/rutas/<int:ruta_id>/editar", methods=["GET", "POST"])
@requiere_permiso("inventario_editar")
def editar_ruta(ruta_id):
    db = get_db()
    ruta = db.execute("SELECT * FROM rutas WHERE id = ?", (ruta_id,)).fetchone()
    if ruta is None:
        flash("Esa ruta no existe.", "error")
        return redirect(url_for("inventario.rutas"))

    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip().upper()
        supervisor = _supervisor_canonico(db, request.form.get("supervisor", ""), excepto_id=ruta_id)
        if not codigo:
            flash("El código no puede estar vacío.", "error")
            return render_template("inventario/editar_ruta.html", ruta=ruta, supervisores=_supervisores(db))
        existente = db.execute("SELECT id FROM rutas WHERE codigo = ? AND id != ?", (codigo, ruta_id)).fetchone()
        if existente:
            flash(f"Ya existe otra ruta con el código {codigo}.", "error")
            return render_template("inventario/editar_ruta.html", ruta=ruta, supervisores=_supervisores(db))
        db.execute("UPDATE rutas SET codigo = ?, supervisor = ? WHERE id = ?", (codigo, supervisor, ruta_id))
        cambio = auditoria.cambios(ruta, {"codigo": codigo, "supervisor": supervisor}, {"codigo": "Código", "supervisor": "Supervisor"})
        if cambio:
            auditoria.registrar(db, "ruta_editar", codigo, cambio)
        db.commit()
        flash("Ruta actualizada.")
        return redirect(url_for("inventario.rutas"))

    return render_template("inventario/editar_ruta.html", ruta=ruta, supervisores=_supervisores(db))


@bp.route("/rutas/<int:ruta_id>/eliminar", methods=["POST"])
@requiere_permiso("inventario_editar")
def eliminar_ruta(ruta_id):
    db = get_db()
    ruta = db.execute("SELECT codigo FROM rutas WHERE id = ?", (ruta_id,)).fetchone()
    if ruta is None:
        flash("Esa ruta no existe.", "error")
        return redirect(url_for("inventario.rutas"))
    try:
        db.execute("DELETE FROM rutas WHERE id = ?", (ruta_id,))
        auditoria.registrar(db, "ruta_eliminar", ruta["codigo"])
        db.commit()
        flash(f"Ruta {ruta['codigo']} eliminada.")
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"No se puede eliminar {ruta['codigo']}: tiene movimientos registrados. Desactívala en su lugar.", "error")
    return redirect(url_for("inventario.rutas"))
