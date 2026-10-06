"""Excel de control de la bitácora: qué salió, qué no regresó y qué no salió.

Las salidas "sembradas" por la carga inicial desde Excel no son escaneos
reales y se excluyen de todo el reporte.
"""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

REAL = "(m.condicion IS NULL OR m.condicion NOT LIKE 'Carga inicial%')"
TIPO_EQUIPO = "CASE e.tipo WHEN 'telefono' THEN 'Teléfono' ELSE 'Impresora' END"

COLUMNAS_SALIDAS = [
    ("equipo", "Equipo"), ("tipo_equipo", "Tipo"), ("serial", "Serial"), ("telefono", "Teléfono"), ("ruta", "Ruta"),
    ("supervisor_ruta", "Supervisor de la ruta"), ("salida_fecha_hora", "Salida (fecha y hora)"),
    ("salida_registrada_por", "Salida registrada por"), ("retorno_fecha_hora", "Retorno (fecha y hora)"),
    ("retorno_registrado_por", "Retorno registrado por"), ("regreso", "¿Regresó?"),
]
HOJAS = {
    "por_ruta": [("ruta", "Ruta"), ("supervisor", "Supervisor"), ("asignados", "Equipos asignados"), ("salieron", "Salieron"),
                 ("regresaron", "Regresaron"), ("pendientes", "Pendientes")],
    "salieron": COLUMNAS_SALIDAS,
    "no_regresaron": COLUMNAS_SALIDAS,
    "no_salieron": [("equipo", "Equipo"), ("tipo_equipo", "Tipo"), ("serial", "Serial"), ("telefono", "Teléfono"), ("ruta", "Ruta"),
                    ("supervisor_ruta", "Supervisor de la ruta"), ("situacion", "Situación")],
    "por_usuario": [("usuario", "Usuario"), ("rol", "Rol"), ("salidas", "Salidas"), ("retornos", "Retornos"),
                    ("primer_escaneo", "Primer escaneo"), ("ultimo_escaneo", "Último escaneo")],
    "movimientos": [("fecha_hora", "Fecha y hora"), ("tipo", "Movimiento"), ("equipo", "Equipo"), ("tipo_equipo", "Tipo"),
                    ("serial", "Serial"), ("telefono", "Teléfono"), ("ruta", "Ruta"), ("usuario", "Usuario"), ("nota", "Nota")],
}
ORDEN = [("Resumen", "resumen"), ("Por ruta", "por_ruta"), ("Salieron", "salieron"), ("No regresaron", "no_regresaron"),
         ("No salieron", "no_salieron"), ("Por usuario", "por_usuario"), ("Movimientos", "movimientos")]


def construir(db, desde: str, hasta: str) -> dict:
    movimientos = [dict(f) for f in db.execute(
        f"""SELECT m.timestamp AS fecha_hora, m.tipo, e.id_interno AS equipo, {TIPO_EQUIPO} AS tipo_equipo,
                   e.serial_fabrica AS serial, e.numero_telefono AS telefono, r.codigo AS ruta,
                   u.nombre AS usuario, m.condicion AS nota
            FROM movimientos m
            JOIN equipos e ON e.id = m.equipo_id
            JOIN rutas r ON r.id = m.ruta_id
            JOIN usuarios u ON u.id = m.operador_id
            WHERE date(m.timestamp) BETWEEN ? AND ? AND {REAL}
            ORDER BY m.id""",
        (desde, hasta),
    )]

    # Cada salida del rango con su retorno (el siguiente del mismo equipo).
    salieron = [dict(f) for f in db.execute(
        f"""SELECT e.id_interno AS equipo, {TIPO_EQUIPO} AS tipo_equipo, e.serial_fabrica AS serial,
                   e.numero_telefono AS telefono, r.codigo AS ruta, r.supervisor AS supervisor_ruta,
                   m.timestamp AS salida_fecha_hora, u.nombre AS salida_registrada_por,
                   (SELECT x.timestamp FROM movimientos x
                     WHERE x.equipo_id = m.equipo_id AND x.tipo = 'retorno' AND x.id > m.id ORDER BY x.id LIMIT 1) AS retorno_fecha_hora,
                   (SELECT u2.nombre FROM movimientos x JOIN usuarios u2 ON u2.id = x.operador_id
                     WHERE x.equipo_id = m.equipo_id AND x.tipo = 'retorno' AND x.id > m.id ORDER BY x.id LIMIT 1) AS retorno_registrado_por
            FROM movimientos m
            JOIN equipos e ON e.id = m.equipo_id
            JOIN rutas r ON r.id = m.ruta_id
            JOIN usuarios u ON u.id = m.operador_id
            WHERE m.tipo = 'salida' AND date(m.timestamp) BETWEEN ? AND ? AND {REAL}
            ORDER BY m.id""",
        (desde, hasta),
    )]
    for f in salieron:
        f["regreso"] = "Sí" if f["retorno_fecha_hora"] else "NO"
    no_regresaron = [f for f in salieron if f["regreso"] == "NO"]

    # Activos con ruta asignada que NO tuvieron ninguna salida en el rango.
    no_salieron = [dict(f) for f in db.execute(
        f"""SELECT e.id_interno AS equipo, {TIPO_EQUIPO} AS tipo_equipo, e.serial_fabrica AS serial,
                   e.numero_telefono AS telefono, r.codigo AS ruta, r.supervisor AS supervisor_ruta,
                   CASE v.ubicacion WHEN 'en_ruta' THEN 'Sigue fuera desde antes' ELSE 'En bodega' END AS situacion
            FROM equipos e
            JOIN rutas r ON r.id = e.ruta_asignada_id
            LEFT JOIN v_estado_actual v ON v.equipo_id = e.id
            WHERE e.estado = 'activo'
              AND NOT EXISTS (SELECT 1 FROM movimientos m WHERE m.equipo_id = e.id AND m.tipo = 'salida'
                              AND date(m.timestamp) BETWEEN ? AND ? AND {REAL})
            ORDER BY r.codigo, e.id_interno""",
        (desde, hasta),
    )]

    por_ruta = {}
    for f in db.execute(
        """SELECT r.codigo, r.supervisor, COUNT(*) AS n FROM equipos e
           JOIN rutas r ON r.id = e.ruta_asignada_id WHERE e.estado = 'activo' GROUP BY r.id"""
    ):
        por_ruta[f["codigo"]] = {"ruta": f["codigo"], "supervisor": f["supervisor"], "asignados": f["n"],
                                 "salieron": 0, "regresaron": 0, "pendientes": 0}
    for f in salieron:
        r = por_ruta.setdefault(f["ruta"], {"ruta": f["ruta"], "supervisor": f["supervisor_ruta"], "asignados": 0,
                                            "salieron": 0, "regresaron": 0, "pendientes": 0})
        r["salieron"] += 1
        r["regresaron" if f["regreso"] == "Sí" else "pendientes"] += 1

    por_usuario = [dict(f) for f in db.execute(
        f"""SELECT u.nombre AS usuario, u.rol,
                   SUM(CASE WHEN m.tipo = 'salida' THEN 1 ELSE 0 END) AS salidas,
                   SUM(CASE WHEN m.tipo = 'retorno' THEN 1 ELSE 0 END) AS retornos,
                   MIN(m.timestamp) AS primer_escaneo, MAX(m.timestamp) AS ultimo_escaneo
            FROM movimientos m JOIN usuarios u ON u.id = m.operador_id
            WHERE date(m.timestamp) BETWEEN ? AND ? AND m.condicion IS NULL
            GROUP BY u.id ORDER BY salidas DESC""",
        (desde, hasta),
    )]

    resumen = [
        ["Desde", desde], ["Hasta", hasta], ["Generado", datetime.now().strftime("%Y-%m-%d %H:%M")], ["", ""],
        ["Equipos que SALIERON (salidas registradas)", len(salieron)],
        ["   ...y ya REGRESARON", len(salieron) - len(no_regresaron)],
        ["   ...y NO han regresado", len(no_regresaron)],
        ["Equipos activos con ruta que NO SALIERON", len(no_salieron)],
        ["Movimientos totales en el rango", len(movimientos)],
    ]
    return {
        "resumen": resumen, "salieron": salieron, "no_regresaron": no_regresaron, "no_salieron": no_salieron,
        "por_ruta": sorted(por_ruta.values(), key=lambda r: (-r["pendientes"], r["ruta"])),
        "por_usuario": por_usuario, "movimientos": movimientos,
    }


def generar_excel(db, desde: str, hasta: str) -> io.BytesIO:
    datos = construir(db, desde, hasta)
    wb = Workbook()
    wb.remove(wb.active)
    cabecera = PatternFill("solid", fgColor="1F2937")
    rojo = PatternFill("solid", fgColor="FECACA")

    for nombre, clave in ORDEN:
        ws = wb.create_sheet(nombre)
        if clave == "resumen":
            for fila in datos["resumen"]:
                ws.append(fila)
            ws.column_dimensions["A"].width = 46
            ws.column_dimensions["B"].width = 22
            for fila in ws.iter_rows(min_row=5, max_row=9):
                fila[0].font = Font(bold=True)
            continue

        columnas = HOJAS[clave]
        ws.append([titulo for _, titulo in columnas])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = cabecera
            c.alignment = Alignment(vertical="center", wrap_text=True)
        for f in datos[clave]:
            ws.append([f.get(k) for k, _ in columnas])
        ws.freeze_panes = "A2"
        if datos[clave]:
            ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ancho = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(ancho + 2, 10), 42)

        # Resalta lo que requiere atención.
        if clave in ("salieron", "no_regresaron"):
            for fila in ws.iter_rows(min_row=2):
                if fila[10].value == "NO":
                    for c in fila:
                        c.fill = rojo
        if clave == "por_ruta":
            for fila in ws.iter_rows(min_row=2):
                if fila[5].value:
                    fila[5].fill = rojo

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
