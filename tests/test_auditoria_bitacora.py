import io
import re

import openpyxl


def _total(html: str) -> int:
    return int(re.search(r"Movimientos \((\d+)\)", html).group(1))


def _registrar_movimientos(sql, filas):
    """filas: (equipo_id_interno, tipo, operador_usuario, condicion, timestamp)"""
    for equipo, tipo, operador, condicion, ts in filas:
        sql(
            """INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo, condicion, timestamp)
               VALUES ((SELECT id FROM equipos WHERE id_interno = ?),
                       (SELECT ruta_asignada_id FROM equipos WHERE id_interno = ?),
                       (SELECT id FROM usuarios WHERE usuario = ?), ?, ?, ?)""",
            (equipo, equipo, operador, tipo, condicion, ts), commit=True,
        )


def test_acciones_de_inventario_quedan_auditadas_con_el_cambio(admin, sql):
    admin.post("/inventario/TEL-001/editar", data={"estado": "activo", "numero_telefono": "77000001", "modelo": "Zebra", "ruta_asignada_id": ""})
    det = sql("SELECT objetivo, detalle FROM auditoria WHERE accion = 'equipo_editar'")[0]
    assert det["objetivo"] == "TEL-001"
    assert "Modelo: (vacío) → Zebra" in det["detalle"] and "Ruta asignada: DS0001 → (vacío)" in det["detalle"]
    assert "Estado" not in det["detalle"]            # lo que no cambió no se anota


def test_editar_sin_cambios_no_genera_ruido(admin, sql):
    ruta = sql("SELECT id FROM rutas WHERE codigo = 'DS0001'")[0]["id"]
    admin.post("/inventario/TEL-001/editar", data={"estado": "activo", "numero_telefono": "77000001", "ruta_asignada_id": str(ruta)})
    assert sql("SELECT COUNT(*) n FROM auditoria WHERE accion = 'equipo_editar'")[0]["n"] == 0


def test_flujo_de_mantenimiento_y_conteo_auditado(admin, sql):
    admin.post("/mantenimiento/enviar", data={"id_interno": "TEL-002", "motivo": "pantalla"})
    mid = sql("SELECT id FROM mantenimientos WHERE estado = 'en_reparacion' ORDER BY id DESC")[0]["id"]
    admin.post(f"/mantenimiento/{mid}/regreso", data={"resultado": "cambio", "nuevo_estado": "activo"})
    admin.post("/conteo/iniciar")
    cid = sql("SELECT id FROM conteos")[0]["id"]
    admin.post("/conteo/escanear", data={"id_interno": "TEL-001"})
    admin.post(f"/conteo/{cid}/cerrar")
    admin.post(f"/conteo/{cid}/ajustar")
    admin.post("/inventario/rutas/nueva", data={"codigo": "ZZ9", "supervisor": "Alguien"})
    admin.post("/dashboard/cerrar-dia")
    acciones = {f["accion"] for f in sql("SELECT accion FROM auditoria")}
    assert {"mantenimiento_enviar", "mantenimiento_regreso", "conteo_iniciar", "conteo_cerrar", "conteo_ajustar", "ruta_crear", "dia_cerrar"} <= acciones


def test_una_accion_fallida_no_deja_rastro_falso(admin, sql):
    admin.post("/inventario/NOEXISTE/eliminar")
    admin.post("/mantenimiento/enviar", data={"id_interno": "NOEXISTE"})
    assert sql("SELECT COUNT(*) n FROM auditoria WHERE accion IN ('equipo_eliminar', 'mantenimiento_enviar')")[0]["n"] == 0


def test_bitacora_oculta_la_carga_inicial_y_la_muestra_con_su_filtro(admin, sql):
    _registrar_movimientos(sql, [
        ("TEL-001", "salida", "admin", "Carga inicial desde Excel -- no es un escaneo real.", "2026-10-01 08:00:00"),
        ("TEL-002", "salida", "admin", None, "2026-10-02 08:00:00"),
        ("TEL-002", "retorno", "admin", "Retorno automático: el equipo se envió a mantenimiento.", "2026-10-02 09:00:00"),
    ])
    assert _total(admin.get("/bitacora/").get_data(as_text=True)) == 2
    assert _total(admin.get("/bitacora/?origen=real").get_data(as_text=True)) == 1
    assert _total(admin.get("/bitacora/?origen=auto").get_data(as_text=True)) == 1
    assert _total(admin.get("/bitacora/?origen=carga").get_data(as_text=True)) == 1
    assert _total(admin.get("/bitacora/?origen=invalido").get_data(as_text=True)) == 2   # valor raro = por defecto


def test_bitacora_filtros_por_operador_supervisor_y_orden(admin, sql):
    _registrar_movimientos(sql, [
        ("TEL-001", "salida", "BrianSup", None, "2026-10-02 08:00:00"),     # ruta DS0001 (KASSANDRA)
        ("TEL-004", "salida", "AnaSup", None, "2026-10-02 08:05:00"),       # ruta DS0003 (JUAN SIBRIAN)
    ])
    brian = sql("SELECT id FROM usuarios WHERE usuario = 'BrianSup'")[0]["id"]
    assert _total(admin.get(f"/bitacora/?operador={brian}").get_data(as_text=True)) == 1
    assert _total(admin.get("/bitacora/?supervisor=kassandra").get_data(as_text=True)) == 1
    assert _total(admin.get("/bitacora/?supervisor=sibrian&tipo_equipo=telefono").get_data(as_text=True)) == 1
    def orden(url):
        return re.findall(r"inventario/(TEL-\d+)/ficha", admin.get(url).get_data(as_text=True))

    assert orden("/bitacora/?orden=antiguos") == ["TEL-001", "TEL-004"]
    assert orden("/bitacora/") == ["TEL-004", "TEL-001"]


def test_bitacora_paginacion(admin, sql):
    filas = []
    for i in range(120):                    # alterna salida/retorno para respetar los triggers
        filas.append(("TEL-001", "salida" if i % 2 == 0 else "retorno", "admin", None, f"2026-09-01 08:{i // 60:02d}:{i % 60:02d}"))
    _registrar_movimientos(sql, filas)
    html = admin.get("/bitacora/").get_data(as_text=True)
    assert _total(html) == 120 and "Mostrando 1–100 de 120" in html and "Siguiente" in html
    html2 = admin.get("/bitacora/?pagina=2").get_data(as_text=True)
    assert "Mostrando 101–120 de 120" in html2 and "Siguiente" not in html2 and "Anterior" in html2
    assert admin.get("/bitacora/?pagina=999").status_code == 200       # se corrige a la última
    assert admin.get("/bitacora/?pagina=abc").status_code == 200
    assert admin.get("/bitacora/?pagina=-4").status_code == 200


def test_pestana_actividad_filtra_y_pagina(admin, sql):
    admin.post("/dashboard/cerrar-dia")
    admin.post("/inventario/rutas/nueva", data={"codigo": "ZZ9", "supervisor": "X"})
    html = admin.get("/bitacora/actividad").get_data(as_text=True)
    assert ">Cierre de día</span>" in html and ">Ruta creada</span>" in html
    html = admin.get("/bitacora/actividad?accion=ruta_crear").get_data(as_text=True)
    assert ">Ruta creada</span>" in html and ">Cierre de día</span>" not in html
    html = admin.get("/bitacora/actividad?texto=ZZ9").get_data(as_text=True)
    assert "ZZ9" in html
    assert "Todavía no hay actividad" in admin.get("/bitacora/actividad?usuario=Nadie").get_data(as_text=True)
    assert admin.get("/bitacora/actividad?accion=inventada").status_code == 200


def test_bitacora_solo_para_admins(sup, entrar):
    assert sup.get("/bitacora/").status_code == 403
    assert sup.get("/bitacora/actividad").status_code == 403
    assert sup.get("/bitacora/exportar").status_code == 403
    assert entrar("OscarAd").get("/bitacora/actividad").status_code == 200


def test_excel_incluye_hoja_de_actividad_y_excluye_carga_inicial(admin, sql):
    _registrar_movimientos(sql, [
        ("TEL-001", "salida", "admin", "Carga inicial desde Excel -- no es un escaneo real.", "2026-10-01 08:00:00"),
        ("TEL-002", "salida", "admin", None, "2026-10-01 08:30:00"),
    ])
    admin.post("/dashboard/cerrar-dia")
    r = admin.get("/bitacora/exportar?desde=2000-01-01&hasta=2100-01-01")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    assert "Actividad" in wb.sheetnames
    assert any("Cierre de día" in str(c.value) for fila in wb["Actividad"].iter_rows() for c in fila)
    equipos_mov = [fila[2].value for fila in wb["Movimientos"].iter_rows(min_row=2)]
    assert equipos_mov == ["TEL-002"]
    assert admin.get("/bitacora/exportar?desde=ayer").status_code == 400
