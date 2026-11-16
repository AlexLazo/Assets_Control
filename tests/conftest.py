"""Fixtures de las pruebas: una base nueva y descartable por prueba, con datos
mínimos sembrados. Nunca se toca una base real ni se usan datos reales."""
import sqlite3
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CLAVE = "claveprueba1"


def sembrar(ruta_db: str) -> None:
    """Usuarios: admin (super_admin), OscarAd (admin), BrianSup y AnaSup (supervisores).
    Rutas: DS0001/DS0002 (KASSANDRA HERNANDEZ), DS0003 (JUAN SIBRIAN).
    Equipos: TEL-001..005 e IMP-001..003 (TEL-005 sin ruta; IMP-003 en reparación)."""
    con = sqlite3.connect(ruta_db)
    h = generate_password_hash(CLAVE)
    con.executemany(
        "INSERT INTO usuarios (nombre, usuario, password_hash, rol) VALUES (?, ?, ?, ?)",
        [("Admin Prueba", "admin", h, "super_admin"), ("Óscar Cuéllar", "OscarAd", h, "admin"),
         ("Brian Lazo", "BrianSup", h, "supervisor"), ("Ana Pérez", "AnaSup", h, "supervisor")],
    )
    con.executemany(
        "INSERT INTO rutas (codigo, supervisor) VALUES (?, ?)",
        [("DS0001", "KASSANDRA HERNANDEZ"), ("DS0002", "KASSANDRA HERNANDEZ"), ("DS0003", "JUAN SIBRIAN")],
    )
    rutas = {c: i for i, c in con.execute("SELECT id, codigo FROM rutas")}
    equipos = [
        ("TEL-001", "telefono", None, "77000001", "1111", "activo", "DS0001"),
        ("TEL-002", "telefono", None, "77000002", "2222", "activo", "DS0001"),
        ("TEL-003", "telefono", None, "77000003", "3333", "activo", "DS0002"),
        ("TEL-004", "telefono", None, "77000004", "4444", "activo", "DS0003"),
        ("TEL-005", "telefono", None, "77000005", "5555", "activo", None),
        ("IMP-001", "impresora", "SER001", None, None, "activo", "DS0001"),
        ("IMP-002", "impresora", "SER002", None, None, "activo", "DS0003"),
        ("IMP-003", "impresora", "SER003", None, None, "reparacion", "DS0003"),
    ]
    for id_i, tipo, serial, tel, pin, estado, ruta in equipos:
        con.execute(
            """INSERT INTO equipos (id_interno, tipo, serial_fabrica, numero_telefono, pin_equipo, estado, ruta_asignada_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id_i, tipo, serial, tel, pin, estado, rutas.get(ruta)),
        )
    con.commit()
    con.close()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("RAILWAY_ENVIRONMENT", "PRODUCCION", "ADMIN_USUARIO", "ADMIN_PASSWORD", "ADMIN_FORZAR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import auth, create_app

    auth._fallos.clear()
    auth._bloqueados.clear()
    aplicacion = create_app()
    aplicacion.config["TESTING"] = True
    sembrar(aplicacion.config["DATABASE"])
    return aplicacion


@pytest.fixture()
def sql(app):
    """sql("SELECT ...", params) -> filas (dict). Para escribir, sql(..., commit=True)."""
    def ejecutar(consulta, parametros=(), commit=False):
        con = sqlite3.connect(app.config["DATABASE"])
        con.row_factory = sqlite3.Row
        try:
            filas = [dict(f) for f in con.execute(consulta, parametros).fetchall()]
            if commit:
                con.commit()
            return filas
        finally:
            con.close()
    return ejecutar


@pytest.fixture()
def entrar(app):
    """entrar('admin') -> cliente con la sesión iniciada."""
    def _entrar(usuario, clave=CLAVE):
        cliente = app.test_client()
        r = cliente.post("/login", data={"nombre": usuario, "password": clave})
        assert r.status_code == 302 and "/login" not in r.location, f"no se pudo entrar como {usuario}"
        return cliente
    return _entrar


@pytest.fixture()
def admin(entrar):
    return entrar("admin")


@pytest.fixture()
def sup(entrar):
    return entrar("BrianSup")
