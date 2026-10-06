from flask import Blueprint, render_template, request
from flask_login import login_required

from .db import get_db

bp = Blueprint("pendientes", __name__, url_prefix="/pendientes")


@bp.route("/")
@login_required
def ver():
    supervisor = request.args.get("supervisor", "").strip()
    solo_pendientes = request.args.get("solo") == "pendientes"

    db = get_db()
    filas = db.execute(
        """SELECT r.id AS ruta_id, r.codigo AS ruta, COALESCE(r.supervisor, '(sin supervisor)') AS supervisor,
                  e.id_interno, e.tipo, e.estado, v.ubicacion, v.desde
           FROM equipos e
           JOIN rutas r ON r.id = e.ruta_asignada_id
           LEFT JOIN v_estado_actual v ON v.equipo_id = e.id
           WHERE r.activa = 1 AND e.estado != 'baja'
           ORDER BY r.codigo, e.tipo, e.id_interno"""
    ).fetchall()

    rutas = {}
    for f in filas:
        r = rutas.setdefault(
            f["ruta_id"],
            {"ruta": f["ruta"], "supervisor": f["supervisor"], "equipos": [], "pendientes": 0},
        )
        estado = "reparacion" if f["estado"] == "reparacion" else (f["ubicacion"] or "en_bodega")
        r["equipos"].append({"id": f["id_interno"], "tipo": f["tipo"], "estado": estado})
        if estado == "en_ruta":
            r["pendientes"] += 1

    # Equipos que salieron sin ruta asignada: no pertenecen a ninguna ruta de
    # arriba, pero siguen fuera y alguien debe regularizarlos.
    sin_ruta = db.execute(
        """SELECT e.id_interno, e.tipo FROM equipos e
           JOIN v_estado_actual v ON v.equipo_id = e.id
           WHERE e.ruta_asignada_id IS NULL AND e.estado = 'activo' AND v.ubicacion = 'en_ruta'
           ORDER BY e.id_interno"""
    ).fetchall()
    if sin_ruta:
        rutas[-1] = {
            "ruta": "SIN RUTA ASIGNADA",
            "supervisor": "(sin supervisor)",
            "equipos": [{"id": f["id_interno"], "tipo": f["tipo"], "estado": "en_ruta"} for f in sin_ruta],
            "pendientes": len(sin_ruta),
        }

    todas = list(rutas.values())
    supervisores = sorted({r["supervisor"] for r in todas})

    por_supervisor = {}
    for r in todas:
        s = por_supervisor.setdefault(r["supervisor"], {"supervisor": r["supervisor"], "rutas": 0, "equipos": 0, "pendientes": 0})
        s["rutas"] += 1
        s["equipos"] += len(r["equipos"])
        s["pendientes"] += r["pendientes"]
    resumen = sorted(por_supervisor.values(), key=lambda s: (-s["pendientes"], s["supervisor"]))

    visibles = [
        r for r in todas
        if (not supervisor or r["supervisor"] == supervisor) and (not solo_pendientes or r["pendientes"] > 0)
    ]

    return render_template(
        "pendientes/ver.html",
        rutas=visibles,
        resumen=resumen,
        supervisores=supervisores,
        supervisor=supervisor,
        solo_pendientes=solo_pendientes,
        total_pendientes=sum(r["pendientes"] for r in todas),
        rutas_con_pendientes=sum(1 for r in todas if r["pendientes"] > 0),
        total_rutas=len(todas),
    )
