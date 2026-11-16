from conftest import CLAVE


def _login(cliente, usuario, clave=CLAVE):
    return cliente.post("/login", data={"nombre": usuario, "password": clave})


def test_login_con_usuario_corto_sin_importar_mayusculas(app):
    for escrito in ("OscarAd", "oscarad", "OSCARAD"):
        r = _login(app.test_client(), escrito)
        assert r.status_code == 302 and "/login" not in r.location, escrito


def test_login_con_nombre_completo_sigue_funcionando(app):
    r = _login(app.test_client(), "óscar cuéllar")
    assert "/login" not in r.location


def test_el_usuario_corto_tiene_prioridad_sobre_un_nombre_igual(app, sql):
    # alguien cuyo NOMBRE completo coincide con el USUARIO de otra persona
    sql("INSERT INTO usuarios (nombre, usuario, password_hash) VALUES ('AnaSup', 'otro', 'x')", commit=True)
    r = _login(app.test_client(), "AnaSup")  # debe entrar como la persona con usuario AnaSup
    assert "/login" not in r.location


def test_credenciales_malas_y_cuenta_inactiva(app, sql):
    c = app.test_client()
    assert "/login" in _login(c, "OscarAd", "incorrecta").location
    assert "/login" in _login(c, "noexiste").location
    sql("UPDATE usuarios SET activo = 0 WHERE usuario = 'AnaSup'", commit=True)
    r = _login(c, "AnaSup")
    assert "/login" in r.location
    assert "desactivada" in c.get("/login").get_data(as_text=True)


def test_acceso_fallido_se_audita_sin_guardar_lo_que_se_escribio(app, sql):
    c = app.test_client()
    _login(c, "OscarAd", "mala")                       # usuario real: se anota
    _login(c, "claveSecretaEscritaPorError", "x")      # desconocido: NO se anota
    filas = sql("SELECT accion, objetivo, usuario_nombre FROM auditoria")
    assert [f["accion"] for f in filas] == ["login_fallido"]
    assert filas[0]["usuario_nombre"] == "Óscar Cuéllar"
    assert "claveSecreta" not in str(filas)


def test_bloqueo_tras_varios_fallos(app):
    c = app.test_client()
    for _ in range(5):
        _login(c, "AnaSup", "mala")
    r = _login(c, "AnaSup", CLAVE)  # ni con la clave buena mientras dure el bloqueo
    assert "/login" in r.location
    assert "Demasiados intentos" in c.get("/login").get_data(as_text=True)


def test_login_ok_se_audita(app, sql):
    _login(app.test_client(), "BrianSup")
    assert sql("SELECT accion, usuario_nombre FROM auditoria")[0] == {"accion": "login", "usuario_nombre": "Brian Lazo"}


def test_next_externo_no_redirige_fuera_del_sitio(app):
    r = app.test_client().post("/login?next=//evil.example.com", data={"nombre": "BrianSup", "password": CLAVE})
    assert "evil" not in r.location


# ---------------------------------------------------------------- gestión de usuarios

def _nuevo(c, **campos):
    datos = {"usuario": "NuevoUs", "nombre": "Nuevo Usuario", "rol": "supervisor", "password": "temporal123", "confirmar": "temporal123"}
    datos.update(campos)
    return c.post("/admin/usuarios/nuevo", data=datos)


def test_crear_usuario_valida_usuario_y_clave(admin, sql):
    assert "no es v" in _nuevo(admin, usuario="con espacio").get_data(as_text=True)
    assert "no es v" in _nuevo(admin, usuario="ab").get_data(as_text=True)
    assert "al menos 8" in _nuevo(admin, password="corta1", confirmar="corta1").get_data(as_text=True)
    assert "no coinciden" in _nuevo(admin, confirmar="otra12345").get_data(as_text=True)
    assert "ya lo usa" in _nuevo(admin, usuario="brianSUP").get_data(as_text=True)          # repetido sin importar mayúsculas
    assert "Ya existe una persona" in _nuevo(admin, nombre="Brian Lazo").get_data(as_text=True)
    assert sql("SELECT COUNT(*) n FROM usuarios WHERE usuario = 'NuevoUs'")[0]["n"] == 0
    assert _nuevo(admin).status_code == 302
    assert sql("SELECT debe_cambiar_password d FROM usuarios WHERE usuario = 'NuevoUs'")[0]["d"] == 1


def test_clave_temporal_obliga_a_cambiarla_antes_de_usar_la_app(admin, app):
    _nuevo(admin)
    c = app.test_client()
    assert _login(c, "NuevoUs", "temporal123").location.endswith("/cuenta")
    assert c.get("/dashboard/").location.endswith("/cuenta")
    assert c.get("/inventario/").location.endswith("/cuenta")
    assert c.get("/static/js/chart.umd.js").status_code == 200          # lo estático no se bloquea
    assert c.get("/cuenta").status_code == 200

    r = c.post("/cuenta", data={"actual": "temporal123", "nueva": "corta", "confirmar": "corta"}, follow_redirects=True)
    assert "al menos 8" in r.get_data(as_text=True)
    assert c.get("/dashboard/").status_code == 302                       # sigue bloqueado
    r = c.post("/cuenta", data={"actual": "temporal123", "nueva": "miclavepropia1", "confirmar": "miclavepropia1"})
    assert r.status_code == 302
    assert c.get("/dashboard/").status_code == 200


def test_resetear_clave_de_otro_la_marca_temporal_pero_la_propia_no(admin, sql):
    uid = sql("SELECT id FROM usuarios WHERE usuario = 'AnaSup'")[0]["id"]
    admin.post(f"/admin/usuarios/{uid}/resetear-password", data={"password": "nuevaclave99", "confirmar": "nuevaclave99"})
    assert sql("SELECT debe_cambiar_password d FROM usuarios WHERE id = ?", (uid,))[0]["d"] == 1
    mi_id = sql("SELECT id FROM usuarios WHERE usuario = 'admin'")[0]["id"]
    admin.post(f"/admin/usuarios/{mi_id}/resetear-password", data={"password": "otraclave123", "confirmar": "otraclave123"})
    assert sql("SELECT debe_cambiar_password d FROM usuarios WHERE id = ?", (mi_id,))[0]["d"] == 0


def test_reset_exige_8_caracteres(admin, sql):
    uid = sql("SELECT id FROM usuarios WHERE usuario = 'AnaSup'")[0]["id"]
    r = admin.post(f"/admin/usuarios/{uid}/resetear-password", data={"password": "1234", "confirmar": "1234"})
    assert "al menos 8" in r.get_data(as_text=True)


def test_editar_usuario_y_nombre(admin, sql):
    uid = sql("SELECT id FROM usuarios WHERE usuario = 'AnaSup'")[0]["id"]
    assert "ya lo usa" in admin.post(f"/admin/usuarios/{uid}/editar", data={"usuario": "admin", "nombre": "Ana Pérez"}).get_data(as_text=True)
    assert admin.post(f"/admin/usuarios/{uid}/editar", data={"usuario": "AnaPz", "nombre": "Ana Pérez G."}).status_code == 302
    assert sql("SELECT usuario, nombre FROM usuarios WHERE id = ?", (uid,))[0] == {"usuario": "AnaPz", "nombre": "Ana Pérez G."}
    detalle = sql("SELECT detalle FROM auditoria WHERE accion = 'usuario_editar'")[0]["detalle"]
    assert "AnaSup → AnaPz" in detalle and "Ana Pérez → Ana Pérez G." in detalle
    # el usuario nuevo ya sirve para entrar y el viejo no
    assert "/login" in _login(admin, "AnaSup").location


def test_un_admin_no_gestiona_a_otro_admin_ni_crea_admins(entrar, sql):
    oscar = entrar("OscarAd")
    assert "Selecciona un rol" in _nuevo(oscar, rol="admin").get_data(as_text=True)
    uid_super = sql("SELECT id FROM usuarios WHERE usuario = 'admin'")[0]["id"]
    assert oscar.get(f"/admin/usuarios/{uid_super}/editar").status_code == 403
    assert oscar.post(f"/admin/usuarios/{uid_super}/resetear-password", data={"password": "hackeado123", "confirmar": "hackeado123"}).status_code == 403


def test_supervisor_no_entra_a_usuarios(sup):
    assert sup.get("/admin/usuarios/").status_code == 403
    assert sup.get("/admin/usuarios/nuevo").status_code == 403


def test_cuenta_propia_cambia_clave_y_queda_auditado(sup, sql):
    r = sup.post("/cuenta", data={"actual": CLAVE, "nueva": "otraclave123", "confirmar": "otraclave123"})
    assert r.status_code == 302
    assert sql("SELECT accion FROM auditoria WHERE accion = 'password_propia'")
    assert "no es correcta" in sup.post("/cuenta", data={"actual": "mal", "nueva": "otraclave456", "confirmar": "otraclave456"}).get_data(as_text=True)


def test_acciones_de_usuarios_quedan_auditadas(admin, sql):
    uid = sql("SELECT id FROM usuarios WHERE usuario = 'AnaSup'")[0]["id"]
    admin.post(f"/admin/usuarios/{uid}/desactivar", data={"nuevo_activo": "0"})
    admin.post(f"/admin/usuarios/{uid}/desactivar", data={"nuevo_activo": "1"})
    admin.post(f"/admin/usuarios/{uid}/cambiar-rol", data={"nuevo_rol": "facturador"})
    admin.post(f"/admin/usuarios/{uid}/eliminar")
    acciones = [f["accion"] for f in sql("SELECT accion FROM auditoria ORDER BY id")]
    assert acciones[-4:] == ["usuario_desactivar", "usuario_activar", "usuario_rol", "usuario_eliminar"]
    # el registro sobrevive al usuario eliminado, con su nombre copiado
    assert "AnaSup" in sql("SELECT objetivo FROM auditoria WHERE accion = 'usuario_eliminar'")[0]["objetivo"]
