from datetime import date, timedelta

from flask import Blueprint, render_template, request

from .auth import requiere_permiso
from .db import get_db

bp = Blueprint("historial", __name__, url_prefix="/historial-diario")


@bp.route("/")
@requiere_permiso("historial")
def ver():
    dias = request.args.get("dias", 30, type=int)
    dias = max(1, min(dias, 90))
    inicio = date.today() - timedelta(days=dias - 1)

    db = get_db()
    movimientos = db.execute(
        """SELECT date(timestamp) AS dia,
                  SUM(CASE WHEN tipo = 'salida' THEN 1 ELSE 0 END) AS salidas,
                  SUM(CASE WHEN tipo = 'retorno' THEN 1 ELSE 0 END) AS retornos
           FROM movimientos
           WHERE date(timestamp) >= date(?)
           GROUP BY dia""",
        (inicio.isoformat(),),
    ).fetchall()
    por_dia = {f["dia"]: {"salidas": f["salidas"], "retornos": f["retornos"]} for f in movimientos}

    incidencias = db.execute(
        """SELECT fecha, COUNT(*) AS n
           FROM incidencias
           WHERE fecha >= date(?)
           GROUP BY fecha""",
        (inicio.isoformat(),),
    ).fetchall()
    incidencias_por_dia = {f["fecha"]: f["n"] for f in incidencias}

    filas = []
    for i in range(dias):
        dia = (inicio + timedelta(days=i)).isoformat()
        datos = por_dia.get(dia, {"salidas": 0, "retornos": 0})
        filas.append(
            {
                "dia": dia,
                "salidas": datos["salidas"],
                "retornos": datos["retornos"],
                "diferencia": datos["salidas"] - datos["retornos"],
                "incidencias": incidencias_por_dia.get(dia, 0),
            }
        )
    filas.reverse()

    return render_template("historial/ver.html", filas=filas, dias=dias)
