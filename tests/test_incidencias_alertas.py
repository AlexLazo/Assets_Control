from datetime import datetime, timedelta

from app import alertas


def _hace(dias: int) -> str:
    return (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")


def _incidencia_abierta(admin, sql, equipo="TEL-001"):
    admin.post("/salida", data={"id_interno": equipo})
    admin.post("/dashboard/cerrar-dia")
    return sql("SELECT id FROM incidencias WHERE estado = 'abierta'")[0]["id"]


def test_cierre_de_dia_crea_incidencia_de_lo_que_sigue_en_ruta(admin, sql):
    admin.post("/salida", data={"id_interno": "TEL-001"})
    admin.post("/salida", data={"id_interno": "TEL-002"})
    admin.post("/retorno", data={"id_interno": "TEL-002"})
    admin.post("/dashboard/cerrar-dia")
    abiertas = sql("SELECT e.id_interno FROM incidencias i JOIN equipos e ON e.id = i.equipo_id WHERE i.estado = 'abierta'")
    assert [f["id_interno"] for f in abiertas] == ["TEL-001"]


def test_resolver_a_mano_exige_nota_y_guarda_quien_y_cuando(admin, sql):
    inc = _incidencia_abierta(admin, sql)
    admin.post(f"/dashboard/incidencias/{inc}/resolver", data={"nota": "x"})
    assert sql("SELECT estado FROM incidencias WHERE id = ?", (inc,))[0]["estado"] == "abierta"       # nota corta: no resuelve
    admin.post(f"/dashboard/incidencias/{inc}/resolver", data={"nota": "   "})
    assert sql("SELECT estado FROM incidencias WHERE id = ?", (inc,))[0]["estado"] == "abierta"

    r = admin.post(f"/dashboard/incidencias/{inc}/resolver", data={"nota": "Teléfono extraviado en ruta"})
    assert r.status_code == 302
    fila = sql("SELECT estado, resolucion_nota, resuelta_por_id, fecha_resolucion FROM incidencias WHERE id = ?", (inc,))[0]
    assert fila["estado"] == "resuelta" and "extraviado" in fila["resolucion_nota"]
    assert fila["resuelta_por_id"] and fila["fecha_resolucion"]
    assert sql("SELECT objetivo, detalle FROM auditoria WHERE accion = 'incidencia_resolver'")[0]["objetivo"] == "TEL-001"


def test_no_se_puede_resolver_dos_veces_ni_una_inexistente(admin, sql):
    inc = _incidencia_abierta(admin, sql)
    admin.post(f"/dashboard/incidencias/{inc}/resolver", data={"nota": "primera vez"})
    admin.post(f"/dashboard/incidencias/{inc}/resolver", data={"nota": "segunda vez"})
    admin.post("/dashboard/incidencias/99999/resolver", data={"nota": "no existe"})
    assert sql("SELECT COUNT(*) n FROM auditoria WHERE accion = 'incidencia_resolver'")[0]["n"] == 1
    assert "primera vez" in sql("SELECT resolucion_nota n FROM incidencias WHERE id = ?", (inc,))[0]["n"]


def test_solo_admin_resuelve_incidencias(admin, sup, entrar, sql):
    inc = _incidencia_abierta(admin, sql)
    assert sup.post(f"/dashboard/incidencias/{inc}/resolver", data={"nota": "intento de supervisor"}).status_code == 403
    assert "Resolver a mano" not in sup.get("/dashboard/").get_data(as_text=True)
    assert "Resolver a mano" in entrar("OscarAd").get("/dashboard/").get_data(as_text=True)
    assert sql("SELECT estado FROM incidencias WHERE id = ?", (inc,))[0]["estado"] == "abierta"


def test_el_retorno_resuelve_la_incidencia_y_anota_quien_y_cuando(admin, sup, sql):
    inc = _incidencia_abierta(admin, sql)
    sup.post("/retorno", data={"id_interno": "TEL-001"})
    fila = sql("SELECT estado, resuelta_por_id, fecha_resolucion FROM incidencias WHERE id = ?", (inc,))[0]
    quien = sql("SELECT id FROM usuarios WHERE usuario = 'BrianSup'")[0]["id"]
    assert fila["estado"] == "resuelta" and fila["resuelta_por_id"] == quien and fila["fecha_resolucion"]


def test_el_ajuste_de_conteo_resuelve_incidencias(admin, sql):
    inc = _incidencia_abierta(admin, sql)
    admin.post("/conteo/iniciar")
    cid = sql("SELECT id FROM conteos")[0]["id"]
    admin.post("/conteo/escanear", data={"id_interno": "TEL-001"})      # aparece físicamente en bodega
    admin.post(f"/conteo/{cid}/cerrar")
    admin.post(f"/conteo/{cid}/ajustar")
    fila = sql("SELECT estado, resuelta_por_id, fecha_resolucion FROM incidencias WHERE id = ?", (inc,))[0]
    assert fila["estado"] == "resuelta" and fila["resuelta_por_id"] and fila["fecha_resolucion"]


# ---------------------------------------------------------------- alertas

def test_alerta_de_mantenimiento_solo_desde_el_umbral(admin, sql):
    admin.post("/mantenimiento/enviar", data={"id_interno": "TEL-001", "motivo": "viejo"})
    admin.post("/mantenimiento/enviar", data={"id_interno": "TEL-002", "motivo": "reciente"})
    sql("UPDATE mantenimientos SET fecha_envio = ? WHERE motivo = 'viejo'", (_hace(alertas.DIAS_MANTENIMIENTO + 1),), commit=True)
    sql("UPDATE mantenimientos SET fecha_envio = ? WHERE motivo = 'reciente'", (_hace(alertas.DIAS_MANTENIMIENTO - 1),), commit=True)
    con = admin.application.config["DATABASE"]
    import sqlite3
    c = sqlite3.connect(con)
    c.row_factory = sqlite3.Row
    a = alertas.calcular(c)
    c.close()
    assert [x["id_interno"] for x in a["mantenimiento"]] == ["TEL-001"]
    assert a["mantenimiento"][0]["dias"] == alertas.DIAS_MANTENIMIENTO + 1


def test_alerta_de_equipo_fuera_solo_desde_el_umbral(admin, sql):
    admin.post("/salida", data={"id_interno": "TEL-001"})
    admin.post("/salida", data={"id_interno": "TEL-002"})
    sql("UPDATE movimientos SET timestamp = ? WHERE equipo_id = (SELECT id FROM equipos WHERE id_interno = 'TEL-001')",
        (_hace(alertas.DIAS_FUERA + 1),), commit=True)
    import sqlite3
    c = sqlite3.connect(admin.application.config["DATABASE"])
    c.row_factory = sqlite3.Row
    a = alertas.calcular(c)
    c.close()
    assert [x["id_interno"] for x in a["fuera"]] == ["TEL-001"]       # TEL-002 salió hoy: no alerta
    assert a["fuera"][0]["ruta"] == "DS0001" and a["fuera"][0]["supervisor"] == "KASSANDRA HERNANDEZ"


def test_un_equipo_que_regresa_deja_de_alertar(admin, sql):
    admin.post("/salida", data={"id_interno": "TEL-001"})
    sql("UPDATE movimientos SET timestamp = ?", (_hace(10),), commit=True)
    admin.post("/retorno", data={"id_interno": "TEL-001"})
    import sqlite3
    c = sqlite3.connect(admin.application.config["DATABASE"])
    c.row_factory = sqlite3.Row
    assert alertas.calcular(c)["total"] == 0
    c.close()


def test_dashboard_y_menu_muestran_las_alertas(admin, sup, sql):
    html = admin.get("/dashboard/").get_data(as_text=True)
    assert "Alertas (" not in html and 'class="nav-globo"' not in html           # sin alertas no hay ruido
    admin.post("/salida", data={"id_interno": "TEL-001"})
    sql("UPDATE movimientos SET timestamp = ?", (_hace(5),), commit=True)
    for cliente in (admin, sup):                                          # todos los roles ven el aviso
        html = cliente.get("/dashboard/").get_data(as_text=True)
        assert "Alertas (1)" in html and 'class="nav-globo"' in html and "TEL-001" in html
        assert 'class="nav-globo"' in cliente.get("/inventario/").get_data(as_text=True)   # y en el menú de otras pantallas


def test_mantenimiento_marca_los_atrasados(admin, sql):
    admin.post("/mantenimiento/enviar", data={"id_interno": "TEL-001", "motivo": "viejo"})
    html = admin.get("/mantenimiento/").get_data(as_text=True)
    assert "atrasado" not in html
    sql("UPDATE mantenimientos SET fecha_envio = ?", (_hace(alertas.DIAS_MANTENIMIENTO + 5),), commit=True)
    html = admin.get("/mantenimiento/").get_data(as_text=True)
    assert "atrasado" in html and "llevan" in html
