import io
import re

import openpyxl


def _ids(html: str) -> list[str]:
    return re.findall(r"inventario/((?:TEL|IMP)-\d+)/ficha", html)


def _ruta_id(sql, codigo):
    return str(sql("SELECT id FROM rutas WHERE codigo = ?", (codigo,))[0]["id"])


def test_listado_filtra_por_tipo_estado_ruta_supervisor_y_sin_ruta(sup):
    assert sorted(_ids(sup.get("/inventario/?tipo=impresora").get_data(as_text=True))) == ["IMP-001", "IMP-002", "IMP-003"]
    assert _ids(sup.get("/inventario/?estado=reparacion").get_data(as_text=True)) == ["IMP-003"]
    assert sorted(_ids(sup.get("/inventario/?ruta=DS0003").get_data(as_text=True))) == ["IMP-002", "IMP-003", "TEL-004"]
    assert sorted(_ids(sup.get("/inventario/?supervisor=kassandra").get_data(as_text=True))) == ["IMP-001", "TEL-001", "TEL-002", "TEL-003"]
    assert _ids(sup.get("/inventario/?sin_ruta=1").get_data(as_text=True)) == ["TEL-005"]
    assert _ids(sup.get("/inventario/?buscar=SER002").get_data(as_text=True)) == ["IMP-002"]
    assert _ids(sup.get("/inventario/?buscar=77000003").get_data(as_text=True)) == ["TEL-003"]
    assert "Ningún equipo coincide" in sup.get("/inventario/?buscar=NADA").get_data(as_text=True)


def test_listado_filtra_por_ubicacion_y_ordena_por_dias_fuera(admin, sql):
    admin.post("/salida", data={"id_interno": "TEL-001"})
    admin.post("/salida", data={"id_interno": "TEL-002"})
    sql("UPDATE movimientos SET timestamp = datetime('now','localtime','-5 days') "
        "WHERE equipo_id = (SELECT id FROM equipos WHERE id_interno = 'TEL-002')", commit=True)
    assert _ids(admin.get("/inventario/?ubicacion=en_ruta&orden=dias").get_data(as_text=True)) == ["TEL-002", "TEL-001"]
    assert "TEL-001" not in _ids(admin.get("/inventario/?ubicacion=en_bodega").get_data(as_text=True))
    assert admin.get("/inventario/?orden=inventado&tipo=raro&estado=x").status_code == 200     # valores raros se ignoran


def test_listado_se_pagina(admin, sql):
    for i in range(6, 150):
        sql("INSERT INTO equipos (id_interno, tipo) VALUES (?, 'telefono')", (f"TEL-{i:03d}",), commit=True)
    html = admin.get("/inventario/").get_data(as_text=True)
    assert len(_ids(html)) == 100 and "Siguiente" in html
    assert len(_ids(admin.get("/inventario/?pagina=2").get_data(as_text=True))) == 149 - 100 + 3   # 5 TEL + 3 IMP + 144 nuevos = 152
    assert len(_ids(admin.get("/inventario/?pagina=2&tipo=telefono").get_data(as_text=True))) == 149 - 100


def test_ficha_muestra_la_historia_del_equipo(admin, sql):
    admin.post("/salida", data={"id_interno": "TEL-001"})
    admin.post("/retorno", data={"id_interno": "TEL-001"})
    admin.post("/mantenimiento/enviar", data={"id_interno": "TEL-001", "motivo": "pantalla rota"})
    admin.post("/inventario/TEL-001/editar", data={"estado": "reparacion", "numero_telefono": "77000001", "notas": "ojo"})
    html = admin.get("/inventario/TEL-001/ficha").get_data(as_text=True)
    assert "Situación actual" in html and "En reparación" in html
    assert "Salida" in html and "Retorno" in html and "pantalla rota" in html
    assert "Cambios y acciones sobre este equipo" in html and "Notas: (vacío) → ojo" in html
    assert "1111" not in html                      # el PIN nunca se muestra


def test_ficha_en_ruta_muestra_dias_fuera_y_supervisor(admin, sql):
    admin.post("/salida", data={"id_interno": "TEL-004"})
    sql("UPDATE movimientos SET timestamp = datetime('now','localtime','-3 days')", commit=True)
    html = admin.get("/inventario/TEL-004/ficha").get_data(as_text=True)
    assert "En ruta" in html and "3 días" in html and "DS0003" in html and "JUAN SIBRIAN" in html


def test_ficha_de_supervisor_no_incluye_auditoria_y_la_inexistente_redirige(admin, sup):
    admin.post("/inventario/TEL-001/editar", data={"estado": "activo", "numero_telefono": "77000001", "notas": "x"})
    html = sup.get("/inventario/TEL-001/ficha").get_data(as_text=True)
    assert "Situación actual" in html and "Cambios y acciones" not in html and "Editar</a>" not in html
    r = sup.get("/inventario/NOEXISTE/ficha")
    assert r.status_code == 302 and r.location.endswith("/inventario/")


def test_exportar_inventario_respeta_filtros_y_no_incluye_pin(admin, sup):
    r = admin.get("/inventario/exportar?tipo=telefono&sin_ruta=1")
    assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
    filas = [[c.value for c in f] for f in ws.iter_rows()]
    assert [f[0] for f in filas[1:]] == ["TEL-005"]
    assert "PIN" not in [str(c).upper() for c in filas[0]] and "5555" not in str(filas)
    assert sup.get("/inventario/exportar").status_code == 403


# ---------------------------------------------------------------- asignación masiva

def test_asignacion_masiva_solo_admin_y_super_admin(admin, entrar, sup, sql):
    ruta = _ruta_id(sql, "DS0002")
    assert sup.post("/inventario/asignar-ruta", data={"ids": ["TEL-005"], "ruta_id": ruta}).status_code == 403
    assert sql("SELECT ruta_asignada_id r FROM equipos WHERE id_interno = 'TEL-005'")[0]["r"] is None
    for quien in (admin, entrar("OscarAd")):
        quien.post("/inventario/asignar-ruta", data={"ids": ["TEL-005"], "ruta_id": ruta})
        assert sql("SELECT ruta_asignada_id r FROM equipos WHERE id_interno = 'TEL-005'")[0]["r"] == int(ruta)
        sql("UPDATE equipos SET ruta_asignada_id = NULL WHERE id_interno = 'TEL-005'", commit=True)


def test_la_pantalla_solo_muestra_la_asignacion_masiva_a_admins(admin, sup):
    assert "Asignar ruta a varios equipos" in admin.get("/inventario/").get_data(as_text=True)
    html = sup.get("/inventario/").get_data(as_text=True)
    assert "Asignar ruta a varios" not in html and 'name="ids"' not in html and "Descargar Excel" not in html


def test_asignacion_masiva_varios_equipos_y_auditoria_por_equipo(admin, sql):
    ruta = _ruta_id(sql, "DS0002")
    r = admin.post("/inventario/asignar-ruta", data={"ids": ["TEL-005", "TEL-001", "tel-004", "IMP-001"], "ruta_id": ruta})
    assert r.status_code == 302
    asignadas = sql("SELECT e.id_interno FROM equipos e JOIN rutas r ON r.id = e.ruta_asignada_id WHERE r.codigo = 'DS0002' ORDER BY 1")
    assert [f["id_interno"] for f in asignadas] == ["IMP-001", "TEL-001", "TEL-003", "TEL-004", "TEL-005"]
    aud = sql("SELECT objetivo, detalle FROM auditoria WHERE accion = 'equipo_editar' ORDER BY id")
    assert sorted(f["objetivo"] for f in aud) == ["IMP-001", "TEL-001", "TEL-004", "TEL-005"]
    assert "Ruta asignada: (vacío) → DS0002 (asignación masiva)" in [f["detalle"] for f in aud if f["objetivo"] == "TEL-005"][0]
    assert "DS0001 → DS0002" in [f["detalle"] for f in aud if f["objetivo"] == "TEL-001"][0]
    # la ficha de cada equipo lo refleja
    assert "asignación masiva" in admin.get("/inventario/TEL-005/ficha").get_data(as_text=True)


def test_asignacion_masiva_no_audita_lo_que_no_cambia(admin, sql):
    admin.post("/inventario/asignar-ruta", data={"ids": ["TEL-001", "TEL-002"], "ruta_id": _ruta_id(sql, "DS0001")})   # ya la tenían
    assert sql("SELECT COUNT(*) n FROM auditoria WHERE accion = 'equipo_editar'")[0]["n"] == 0


def test_asignacion_masiva_quitar_ruta(admin, sql):
    admin.post("/inventario/asignar-ruta", data={"ids": ["TEL-001", "TEL-002"], "ruta_id": "quitar"})
    assert sql("SELECT COUNT(*) n FROM equipos WHERE ruta_asignada_id IS NULL")[0]["n"] == 3        # + TEL-005


def test_asignacion_masiva_valida_la_entrada(admin, sql):
    antes = sql("SELECT id_interno, ruta_asignada_id FROM equipos ORDER BY id")
    admin.post("/inventario/asignar-ruta", data={"ruta_id": _ruta_id(sql, "DS0002")})                       # sin equipos marcados
    admin.post("/inventario/asignar-ruta", data={"ids": ["TEL-005"]})                                         # sin ruta elegida
    admin.post("/inventario/asignar-ruta", data={"ids": ["TEL-005"], "ruta_id": "99999"})                     # ruta inexistente
    admin.post("/inventario/asignar-ruta", data={"ids": ["TEL-005"], "ruta_id": "abc' OR 1=1 --"})            # basura
    admin.post("/inventario/asignar-ruta", data={"ids": ["NOEXISTE"], "ruta_id": _ruta_id(sql, "DS0002")})   # equipo inexistente
    assert sql("SELECT id_interno, ruta_asignada_id FROM equipos ORDER BY id") == antes
    sql("UPDATE rutas SET activa = 0 WHERE codigo = 'DS0002'", commit=True)
    admin.post("/inventario/asignar-ruta", data={"ids": ["TEL-005"], "ruta_id": _ruta_id(sql, "DS0002")})   # ruta desactivada
    assert sql("SELECT ruta_asignada_id r FROM equipos WHERE id_interno = 'TEL-005'")[0]["r"] is None


def test_asignacion_masiva_vuelve_a_la_misma_pagina_pero_nunca_a_otro_sitio(admin, sql):
    datos = {"ids": ["TEL-005"], "ruta_id": _ruta_id(sql, "DS0002")}
    r = admin.post("/inventario/asignar-ruta", data={**datos, "volver": "/inventario/?sin_ruta=1&pagina=1"})
    assert r.location == "/inventario/?sin_ruta=1&pagina=1"
    for malo in ("//evil.example.com", "https://evil.example.com", "/\\evil"):
        r = admin.post("/inventario/asignar-ruta", data={**datos, "volver": malo})
        assert r.location == "/inventario/", malo


# ---------------------------------------------------------------- supervisores

def test_ruta_nueva_reutiliza_el_supervisor_existente_aunque_se_escriba_distinto(admin, sql):
    admin.post("/inventario/rutas/nueva", data={"codigo": "DS0100", "supervisor": "  kassandra   hernández "})
    admin.post("/inventario/rutas/nueva", data={"codigo": "DS0101", "supervisor": "Nuevo Supervisor"})
    sup = dict((f["codigo"], f["supervisor"]) for f in sql("SELECT codigo, supervisor FROM rutas"))
    assert sup["DS0100"] == "KASSANDRA HERNANDEZ"          # igual que las demás: no se parte en dos
    assert sup["DS0101"] == "NUEVO SUPERVISOR"             # uno nuevo se guarda en mayúsculas
    assert len({v for v in sup.values() if v and "KASSANDRA" in v}) == 1


def test_editar_ruta_unifica_y_deja_vacio_el_supervisor(admin, sql):
    rid = _ruta_id(sql, "DS0003")
    admin.post(f"/inventario/rutas/{rid}/editar", data={"codigo": "DS0003", "supervisor": "Kassandra Hernández"})
    assert sql("SELECT supervisor s FROM rutas WHERE codigo = 'DS0003'")[0]["s"] == "KASSANDRA HERNANDEZ"
    admin.post(f"/inventario/rutas/{rid}/editar", data={"codigo": "DS0003", "supervisor": "  "})
    assert sql("SELECT supervisor s FROM rutas WHERE codigo = 'DS0003'")[0]["s"] is None
    assert sql("SELECT detalle FROM auditoria WHERE accion = 'ruta_editar' ORDER BY id")[0]["detalle"].startswith("Supervisor: JUAN SIBRIAN")


def test_pendientes_agrupa_un_solo_supervisor(admin, sql):
    admin.post("/inventario/rutas/nueva", data={"codigo": "DS0100", "supervisor": "KASSANDRA HERNÁNDEZ"})
    html = admin.get("/pendientes/").get_data(as_text=True)
    assert "HERNÁNDEZ" not in html
