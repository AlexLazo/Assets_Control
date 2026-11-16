from datetime import date

from flask import Blueprint, abort, render_template, request, send_file, url_for

from . import auditoria, reporte_excel
from .auth import requiere_permiso
from .db import get_db

bp = Blueprint("bitacora", __name__, url_prefix="/bitacora")

POR_PAGINA = 100

# Qué escaneos mostrar: "" = todo menos la carga inicial desde Excel (no son escaneos reales).
ORIGENES = {
    "": ("Todo (sin carga inicial)", "m.condicion IS NULL OR m.condicion NOT LIKE 'Carga inicial%'"),
    "real": ("Solo escaneos reales", "m.condicion IS NULL"),
    "auto": ("Solo retornos automáticos del sistema", "m.condicion IS NOT NULL AND m.condicion NOT LIKE 'Carga inicial%'"),
    "carga": ("Solo carga inicial", "m.condicion LIKE 'Carga inicial%'"),
}


def _rango_fechas(desde: str, hasta: str):
    try:
        date.fromisoformat(desde)
        date.fromisoformat(hasta)
    except ValueError:
        abort(400, "Fechas inválidas (usa AAAA-MM-DD).")
    return (hasta, desde) if desde > hasta else (desde, hasta)


@bp.route("/exportar")
@requiere_permiso("bitacora")
def exportar():
    """Excel de control del rango pedido (por defecto, hoy)."""
    desde = request.args.get("desde") or date.today().isoformat()
    hasta = request.args.get("hasta") or desde
    desde, hasta = _rango_fechas(desde, hasta)

    archivo = reporte_excel.generar_excel(get_db(), desde, hasta)
    nombre = f"bitacora_{desde}.xlsx" if desde == hasta else f"bitacora_{desde}_a_{hasta}.xlsx"
    return send_file(
        archivo,
        as_attachment=True,
        download_name=nombre,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _paginar(db, sql_total: str, sql_filas: str, parametros: list):
    """Devuelve (filas, paginacion). `sql_filas` termina antes de LIMIT."""
    total = db.execute(sql_total, parametros).fetchone()[0]
    paginas = max(1, -(-total // POR_PAGINA))
    pagina = min(max(request.args.get("pagina", 1, type=int), 1), paginas)
    filas = db.execute(f"{sql_filas} LIMIT ? OFFSET ?", [*parametros, POR_PAGINA, (pagina - 1) * POR_PAGINA]).fetchall()

    def enlace(n: int) -> str:
        return url_for(request.endpoint, **{**request.args.to_dict(), "pagina": n})

    pag = {
        "total": total, "pagina": pagina, "paginas": paginas,
        "desde": (pagina - 1) * POR_PAGINA + 1 if total else 0,
        "hasta": min(pagina * POR_PAGINA, total),
        "anterior": enlace(pagina - 1) if pagina > 1 else None,
        "siguiente": enlace(pagina + 1) if pagina < paginas else None,
    }
    return filas, pag


@bp.route("/")
@requiere_permiso("bitacora")
def ver():
    db = get_db()

    filtros = {
        "equipo": request.args.get("equipo", "").strip(),
        "ruta": request.args.get("ruta", "").strip(),
        "supervisor": request.args.get("supervisor", "").strip(),
        "operador": request.args.get("operador", "").strip(),
        "tipo_equipo": request.args.get("tipo_equipo", "").strip(),
        "tipo_movimiento": request.args.get("tipo_movimiento", "").strip(),
        "origen": request.args.get("origen", "").strip(),
        "orden": request.args.get("orden", "").strip(),
        "desde": request.args.get("desde", "").strip(),
        "hasta": request.args.get("hasta", "").strip(),
    }
    if filtros["origen"] not in ORIGENES:
        filtros["origen"] = ""

    condiciones = [f"({ORIGENES[filtros['origen']][1]})"]
    parametros = []

    if filtros["equipo"]:
        condiciones.append("e.id_interno LIKE ?")
        parametros.append(f"%{filtros['equipo'].upper()}%")
    if filtros["ruta"]:
        condiciones.append("r.codigo LIKE ?")
        parametros.append(f"%{filtros['ruta'].upper()}%")
    if filtros["supervisor"]:
        condiciones.append("r.supervisor LIKE ?")
        parametros.append(f"%{filtros['supervisor']}%")
    if filtros["operador"].isdigit():
        condiciones.append("m.operador_id = ?")
        parametros.append(int(filtros["operador"]))
    if filtros["tipo_equipo"] in ("telefono", "impresora"):
        condiciones.append("e.tipo = ?")
        parametros.append(filtros["tipo_equipo"])
    if filtros["tipo_movimiento"] in ("salida", "retorno"):
        condiciones.append("m.tipo = ?")
        parametros.append(filtros["tipo_movimiento"])
    if filtros["desde"]:
        condiciones.append("date(m.timestamp) >= date(?)")
        parametros.append(filtros["desde"])
    if filtros["hasta"]:
        condiciones.append("date(m.timestamp) <= date(?)")
        parametros.append(filtros["hasta"])

    where = f"WHERE {' AND '.join(condiciones)}"
    orden = "ASC" if filtros["orden"] == "antiguos" else "DESC"
    desde_sql = """FROM movimientos m
            JOIN equipos e ON e.id = m.equipo_id
            JOIN rutas r ON r.id = m.ruta_id
            JOIN usuarios o ON o.id = m.operador_id"""

    # `where` solo arma marcadores '?' fijos definidos arriba -- el valor
    # tecleado por el usuario siempre viaja en `parametros`, nunca dentro
    # del texto SQL.
    movimientos, pag = _paginar(
        db,
        f"SELECT COUNT(*) {desde_sql} {where}",
        f"""SELECT m.id, m.tipo, m.timestamp, m.condicion,
                   e.id_interno, e.tipo AS tipo_equipo,
                   r.codigo AS ruta_codigo, r.supervisor AS supervisor, o.nombre AS operador_nombre
            {desde_sql} {where}
            ORDER BY m.id {orden}""",
        parametros,
    )

    operadores = db.execute(
        "SELECT id, nombre FROM usuarios WHERE id IN (SELECT DISTINCT operador_id FROM movimientos) ORDER BY nombre COLLATE NOCASE"
    ).fetchall()

    return render_template(
        "bitacora/ver.html", movimientos=movimientos, filtros=filtros, pag=pag, operadores=operadores,
        origenes={k: v[0] for k, v in ORIGENES.items()},
    )


@bp.route("/actividad")
@requiere_permiso("bitacora")
def actividad():
    """Auditoría: quién creó/editó/borró qué, cierres de día, accesos, etc."""
    db = get_db()
    filtros = {
        "usuario": request.args.get("usuario", "").strip(),
        "accion": request.args.get("accion", "").strip(),
        "texto": request.args.get("texto", "").strip(),
        "desde": request.args.get("desde", "").strip(),
        "hasta": request.args.get("hasta", "").strip(),
    }
    condiciones, parametros = [], []
    if filtros["usuario"]:
        condiciones.append("usuario_nombre = ?")
        parametros.append(filtros["usuario"])
    if filtros["accion"] in auditoria.ACCIONES:
        condiciones.append("accion = ?")
        parametros.append(filtros["accion"])
    if filtros["texto"]:
        condiciones.append("(objetivo LIKE ? OR detalle LIKE ?)")
        parametros += [f"%{filtros['texto']}%"] * 2
    if filtros["desde"]:
        condiciones.append("date(fecha) >= date(?)")
        parametros.append(filtros["desde"])
    if filtros["hasta"]:
        condiciones.append("date(fecha) <= date(?)")
        parametros.append(filtros["hasta"])
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    filas, pag = _paginar(
        db,
        f"SELECT COUNT(*) FROM auditoria {where}",
        f"SELECT id, fecha, usuario_nombre, accion, objetivo, detalle FROM auditoria {where} ORDER BY id DESC",
        parametros,
    )
    usuarios = [f[0] for f in db.execute(
        "SELECT DISTINCT usuario_nombre FROM auditoria WHERE usuario_nombre IS NOT NULL ORDER BY usuario_nombre COLLATE NOCASE"
    )]
    return render_template("bitacora/actividad.html", filas=filas, filtros=filtros, pag=pag, usuarios=usuarios, acciones=auditoria.ACCIONES)
