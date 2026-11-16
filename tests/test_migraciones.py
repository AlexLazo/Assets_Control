import sqlite3
from pathlib import Path

from app import migraciones

SCHEMA_V3 = Path(__file__).parent / "schema_v3.sql"


def _base_v3(ruta):
    con = sqlite3.connect(ruta)
    con.executescript(SCHEMA_V3.read_text(encoding="utf-8"))
    con.execute("PRAGMA user_version = 3")
    con.commit()
    return con


def test_instalacion_nueva_queda_en_la_version_actual(tmp_path):
    ruta = tmp_path / "nueva.db"
    assert migraciones.actualizar(ruta) == migraciones.VERSION_ACTUAL
    con = sqlite3.connect(ruta)
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "auditoria" in tablas
    columnas = {r[1] for r in con.execute("PRAGMA table_info(usuarios)")}
    assert {"usuario", "debe_cambiar_password"} <= columnas


def test_base_v3_se_actualiza_sin_perder_datos(tmp_path):
    ruta = tmp_path / "vieja.db"
    con = _base_v3(ruta)
    con.executemany(
        "INSERT INTO usuarios (nombre, password_hash, rol) VALUES (?, 'x', ?)",
        [("Brian Lazo", "supervisor"), ("Bryan Galdámez", "supervisor"), ("Ana Pérez", "supervisor"),
         ("Ana Perez", "supervisor"), ("admin", "super_admin")],
    )
    con.execute("INSERT INTO rutas (codigo, supervisor) VALUES ('DS0001', 'X')")
    con.execute("INSERT INTO equipos (id_interno, tipo) VALUES ('TEL-001', 'telefono')")
    con.commit()
    con.close()

    assert migraciones.actualizar(ruta) == migraciones.VERSION_ACTUAL
    con = sqlite3.connect(ruta)
    usuarios = dict(con.execute("SELECT nombre, usuario FROM usuarios"))
    assert usuarios["Brian Lazo"] == "brian.lazo"
    assert usuarios["Bryan Galdámez"] == "bryan.galdamez"
    assert usuarios["admin"] == "admin"
    # dos nombres que dan el mismo usuario no chocan: el segundo recibe sufijo
    assert {usuarios["Ana Pérez"], usuarios["Ana Perez"]} == {"ana.perez", "ana.perez2"}
    assert con.execute("SELECT COUNT(*) FROM equipos").fetchone()[0] == 1
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    # el usuario es único sin importar mayúsculas
    try:
        con.execute("INSERT INTO usuarios (nombre, usuario, password_hash) VALUES ('Z', 'BRIAN.LAZO', 'x')")
        assert False, "debió fallar por usuario repetido"
    except sqlite3.IntegrityError:
        pass


def test_migrar_dos_veces_no_hace_nada_la_segunda(tmp_path):
    ruta = tmp_path / "vieja.db"
    _base_v3(ruta).close()
    migraciones.actualizar(ruta)
    assert migraciones.actualizar(ruta) == migraciones.VERSION_ACTUAL


def test_unifica_supervisores_escritos_distinto(tmp_path):
    ruta = tmp_path / "vieja.db"
    con = _base_v3(ruta)
    con.executemany(
        "INSERT INTO rutas (codigo, supervisor) VALUES (?, ?)",
        [("A1", "KASSANDRA HERNANDEZ"), ("A2", "KASSANDRA HERNANDEZ"), ("A3", "KASSANDRA HERNÁNDEZ"),
         ("B1", "BRYAN GALDÁMEZ"), ("B2", "Bryan  Galdamez"), ("B3", "BRYAN GALDAMEZ"),
         ("C1", "JUAN SIBRIAN"), ("C2", "JUAN CARLOS RAMIREZ SIBRIAN"), ("D1", "ALEXANDER SAÑA"), ("E1", None)],
    )
    con.commit()
    con.close()
    migraciones.actualizar(ruta)
    sup = dict(sqlite3.connect(ruta).execute("SELECT codigo, supervisor FROM rutas"))
    assert {sup["A1"], sup["A2"], sup["A3"]} == {"KASSANDRA HERNANDEZ"}      # gana la más usada
    assert {sup["B1"], sup["B2"], sup["B3"]} == {"BRYAN GALDAMEZ"}
    # nombres realmente distintos NO se mezclan; la ñ no se pierde
    assert sup["C1"] == "JUAN SIBRIAN" and sup["C2"] == "JUAN CARLOS RAMIREZ SIBRIAN"
    assert sup["D1"] == "ALEXANDER SAÑA" and sup["E1"] is None


def test_slug_y_clave_supervisor():
    assert migraciones.slug_usuario("Óscar Cuéllar") == "oscar.cuellar"
    assert migraciones.slug_usuario("   ") == "usuario"
    assert len(migraciones.slug_usuario("a" * 80)) <= 30
    assert migraciones.clave_supervisor(" kassandra  Hernández ") == "KASSANDRA HERNANDEZ"


def test_admin_inicial_por_variables_de_entorno(tmp_path, monkeypatch):
    ruta = tmp_path / "n.db"
    migraciones.actualizar(ruta)
    monkeypatch.setenv("ADMIN_USUARIO", "AdminInicial")
    monkeypatch.setenv("ADMIN_PASSWORD", "clavelarga123")
    migraciones.asegurar_admin(ruta)
    fila = sqlite3.connect(ruta).execute("SELECT nombre, usuario, rol, debe_cambiar_password FROM usuarios").fetchone()
    assert fila == ("AdminInicial", "AdminInicial", "super_admin", 0)
