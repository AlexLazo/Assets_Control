from flask import Blueprint, render_template, request

from .auth import requiere_admin
from .db import get_db

bp = Blueprint("bitacora", __name__, url_prefix="/bitacora")


@bp.route("/")
@requiere_admin
def ver():
    db = get_db()

    filtros = {
        "equipo": request.args.get("equipo", "").strip(),
        "ruta": request.args.get("ruta", "").strip(),
        "tipo_equipo": request.args.get("tipo_equipo", "").strip(),
        "tipo_movimiento": request.args.get("tipo_movimiento", "").strip(),
        "desde": request.args.get("desde", "").strip(),
        "hasta": request.args.get("hasta", "").strip(),
    }

    condiciones = []
    parametros = []

    if filtros["equipo"]:
        condiciones.append("e.id_interno LIKE ?")
        parametros.append(f"%{filtros['equipo'].upper()}%")
    if filtros["ruta"]:
        condiciones.append("r.codigo LIKE ?")
        parametros.append(f"%{filtros['ruta'].upper()}%")
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

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    # `where` solo arma marcadores '?' fijos definidos arriba -- el valor
    # tecleado por el usuario siempre viaja en `parametros`, nunca dentro
    # del texto SQL.
    movimientos = db.execute(
        f"""SELECT m.id, m.tipo, m.timestamp, m.condicion,
                   e.id_interno, e.tipo AS tipo_equipo,
                   r.codigo AS ruta_codigo, o.nombre AS operador_nombre
            FROM movimientos m
            JOIN equipos e ON e.id = m.equipo_id
            JOIN rutas r ON r.id = m.ruta_id
            JOIN usuarios o ON o.id = m.operador_id
            {where}
            ORDER BY m.id DESC
            LIMIT 500""",
        parametros,
    ).fetchall()

    return render_template("bitacora/ver.html", movimientos=movimientos, filtros=filtros)
