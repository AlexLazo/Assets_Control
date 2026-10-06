"""Etapa B: crea la base de datos (si hace falta) e importa el catálogo
inicial de equipos, generando los id_interno definitivos (TEL-xxx /
IMP-xxx) que luego se imprimen como etiquetas QR físicas.

Por defecto se niega a correr si 'equipos' ya tiene filas: una vez
impresas y pegadas las etiquetas físicas, renumerar sería catastrófico
(la etiqueta pegada en el equipo dejaría de coincidir con su registro).
Para agregar equipos nuevos o actualizar datos de los ya importados sin
tocar ningún id_interno existente, correr con --reimportar.

Se recomienda revisar antes reporte_conflictos.xlsx (generado por
generar_reporte_conflictos.py) -- esa es la misma lógica de conflictos
que aquí decide el campo requiere_revision, así que lo que se ve en el
reporte es exactamente lo que va a quedar marcado en la base.

Uso:
    python scripts/importar_catalogo.py
    python scripts/importar_catalogo.py --reimportar
"""
from __future__ import annotations

import argparse
import secrets
import sqlite3

import pandas as pd
from werkzeug.security import generate_password_hash

import comun

DB_PATH = comun.BASE_DIR / "instance" / "activos.db"
SCHEMA_PATH = comun.BASE_DIR / "app" / "schema.sql"
OPERADOR_CARGA_INICIAL = "Carga inicial (importación)"
NOTA_CARGA_INICIAL = "Carga inicial desde Excel -- no es un escaneo real."


def conectar() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def asegurar_esquema(con: sqlite3.Connection) -> None:
    ya_existe = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='equipos'"
    ).fetchone()
    if ya_existe:
        return
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def obtener_o_crear_ruta(con: sqlite3.Connection, codigo: str | None, supervisor: str | None) -> int | None:
    if not codigo:
        return None
    fila = con.execute("SELECT id FROM rutas WHERE codigo = ?", (codigo,)).fetchone()
    if fila:
        if supervisor:
            con.execute("UPDATE rutas SET supervisor = COALESCE(supervisor, ?) WHERE id = ?", (supervisor, fila["id"]))
        return fila["id"]
    cur = con.execute("INSERT INTO rutas (codigo, supervisor) VALUES (?, ?)", (codigo, supervisor))
    return cur.lastrowid


def obtener_o_crear_operador(con: sqlite3.Connection, nombre: str) -> int:
    fila = con.execute("SELECT id FROM usuarios WHERE nombre = ?", (nombre,)).fetchone()
    if fila:
        return fila["id"]
    # Password inutilizable a propósito: esta cuenta no es una persona real
    # y nunca debe poder loguearse -- queda además con activo=0 apenas se
    # usa (ver main()), esto es solo para satisfacer el NOT NULL de la
    # columna en una base que ya migró a login con contraseña.
    hash_inutilizable = generate_password_hash(secrets.token_hex(32))
    cur = con.execute(
        "INSERT INTO usuarios (nombre, password_hash, rol) VALUES (?, ?, 'operador')",
        (nombre, hash_inutilizable),
    )
    return cur.lastrowid


def sembrar_salida_inicial(con: sqlite3.Connection, equipo_id: int, ruta_id: int, operador_carga_id: int) -> None:
    con.execute(
        """INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo, condicion)
           VALUES (?, ?, ?, 'salida', ?)""",
        (equipo_id, ruta_id, operador_carga_id, NOTA_CARGA_INICIAL),
    )


def _valor(fila, clave):
    v = fila.get(clave) if hasattr(fila, "get") else fila[clave]
    return v if pd.notna(v) else None


def _construir_notas_impresora(fila) -> str | None:
    partes = []
    if fila.get("conflicto_ruta"):
        partes.append(f"[Conflicto de ruta] Hoja1={fila.get('ruta_h1')!r} vs Julio2026={fila.get('ruta_julio')!r}")
    if fila.get("sin_ruta_asignada"):
        partes.append(f"[Sin ruta asignada en censo Julio] valor original: {fila.get('ruta_julio')!r}")
    if fila.get("huerfano_de_it"):
        partes.append("[No aparece en el maestro DATA IT pese a estar activo en una ruta]")
    if fila.get("data_it_inconsistente"):
        partes.append(f"[Nombre en DATA IT no calza] Nombre={fila.get('nombre_it')!r}")
    if _valor(fila, "comentario_julio"):
        partes.append(f"[Comentario Julio2026] {fila.get('comentario_julio')}")
    if _valor(fila, "asignado_a_it"):
        partes.append(f"[IT] Asignado a persona: {fila.get('asignado_a_it')}")
    if fila.get("sticker_actualizado_julio") == "NOK":
        partes.append("[Sticker de fábrica no estaba actualizado/legible según censo Julio2026]")
    if str(fila.get("cambio_bateria_h1")).strip().upper() == "SI":
        partes.append("[Cambio de batería registrado en Hoja1]")
    return " ; ".join(partes) if partes else None


def _siguiente_numero(con: sqlite3.Connection, prefijo: str) -> int:
    filas = con.execute("SELECT id_interno FROM equipos WHERE id_interno LIKE ?", (f"{prefijo}-%",)).fetchall()
    numeros = [int(f["id_interno"].split("-")[1]) for f in filas]
    return (max(numeros) + 1) if numeros else 1


def importar_telefonos(con: sqlite3.Connection, operador_carga_id: int) -> tuple[int, int]:
    telefonos = comun.cargar_telefonos()
    duplicados = telefonos[telefonos["duplicado"]]
    limpios = telefonos[~telefonos["duplicado"]].sort_values("ruta_norm", na_position="last")

    n = 1
    for _, fila in limpios.iterrows():
        id_interno = f"TEL-{n:03d}"
        ruta_id = None
        if comun.ruta_esta_asignada(fila["ruta_norm"]):
            ruta_id = obtener_o_crear_ruta(con, fila["ruta_norm"], _valor(fila, "supervisor"))
        cur = con.execute(
            """INSERT INTO equipos (id_interno, tipo, numero_telefono, pin_equipo, notas, ruta_asignada_id)
               VALUES (?, 'telefono', ?, ?, ?, ?)""",
            (id_interno, fila["telefono"], _valor(fila, "pin"), _valor(fila, "observaciones"), ruta_id),
        )
        equipo_id = cur.lastrowid
        if ruta_id:
            sembrar_salida_inicial(con, equipo_id, ruta_id, operador_carga_id)
        n += 1

    return n - 1, len(duplicados)


def importar_impresoras(con: sqlite3.Connection, operador_carga_id: int) -> int:
    vista = comun.construir_vista_impresoras()["vista"].sort_values("serie_norm")

    n = 1
    for _, fila in vista.iterrows():
        id_interno = f"IMP-{n:03d}"

        estado = "activo"
        if fila.get("en_reparacion_h1") and not fila.get("retorno_reparacion_h1"):
            estado = "reparacion"

        ruta_norm = fila.get("ruta_final_norm")
        ruta_id = None
        if comun.ruta_esta_asignada(ruta_norm):
            supervisor = _valor(fila, "supervisor_julio") or _valor(fila, "supervisor_h1")
            ruta_id = obtener_o_crear_ruta(con, ruta_norm, supervisor)

        con.execute(
            """INSERT INTO equipos
               (id_interno, tipo, serial_fabrica, modelo, fabricante, estado, notas, requiere_revision, ruta_asignada_id)
               VALUES (?, 'impresora', ?, ?, ?, ?, ?, ?, ?)""",
            (
                id_interno,
                fila["serie_norm"],
                _valor(fila, "modelo_it"),
                _valor(fila, "fabricante_it"),
                estado,
                _construir_notas_impresora(fila),
                int(bool(fila["requiere_revision"])),
                ruta_id,
            ),
        )
        equipo_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        if ruta_id:
            sembrar_salida_inicial(con, equipo_id, ruta_id, operador_carga_id)
        n += 1

    return n - 1


def actualizar_o_agregar(con: sqlite3.Connection, operador_carga_id: int) -> dict:
    siguiente_tel = _siguiente_numero(con, "TEL")
    siguiente_imp = _siguiente_numero(con, "IMP")
    nuevos_tel = nuevos_imp = actualizados_imp = 0

    telefonos = comun.cargar_telefonos()
    for _, fila in telefonos[~telefonos["duplicado"]].iterrows():
        existente = con.execute(
            "SELECT id FROM equipos WHERE tipo='telefono' AND numero_telefono = ?", (fila["telefono"],)
        ).fetchone()
        if existente:
            con.execute(
                "UPDATE equipos SET pin_equipo = ?, notas = ? WHERE id = ?",
                (_valor(fila, "pin"), _valor(fila, "observaciones"), existente["id"]),
            )
            continue

        id_interno = f"TEL-{siguiente_tel:03d}"
        ruta_id = None
        if comun.ruta_esta_asignada(fila["ruta_norm"]):
            ruta_id = obtener_o_crear_ruta(con, fila["ruta_norm"], _valor(fila, "supervisor"))
        cur = con.execute(
            "INSERT INTO equipos (id_interno, tipo, numero_telefono, pin_equipo, notas, ruta_asignada_id) VALUES (?, 'telefono', ?, ?, ?, ?)",
            (id_interno, fila["telefono"], _valor(fila, "pin"), _valor(fila, "observaciones"), ruta_id),
        )
        if ruta_id:
            sembrar_salida_inicial(con, cur.lastrowid, ruta_id, operador_carga_id)
        print(f"Teléfono nuevo: {id_interno} ({fila['telefono']}) -- necesita etiqueta física nueva.")
        siguiente_tel += 1
        nuevos_tel += 1

    vista = comun.construir_vista_impresoras()["vista"]
    for _, fila in vista.iterrows():
        existente = con.execute(
            "SELECT id FROM equipos WHERE tipo='impresora' AND serial_fabrica = ?", (fila["serie_norm"],)
        ).fetchone()
        modelo = _valor(fila, "modelo_it")
        fabricante = _valor(fila, "fabricante_it")
        notas = _construir_notas_impresora(fila)
        requiere_revision = int(bool(fila["requiere_revision"]))

        if existente:
            con.execute(
                """UPDATE equipos SET modelo = COALESCE(?, modelo), fabricante = COALESCE(?, fabricante),
                   notas = ?, requiere_revision = ? WHERE id = ?""",
                (modelo, fabricante, notas, requiere_revision, existente["id"]),
            )
            actualizados_imp += 1
            continue

        id_interno = f"IMP-{siguiente_imp:03d}"
        estado = "activo"
        if fila.get("en_reparacion_h1") and not fila.get("retorno_reparacion_h1"):
            estado = "reparacion"

        ruta_norm = fila.get("ruta_final_norm")
        ruta_id = None
        if comun.ruta_esta_asignada(ruta_norm):
            supervisor = _valor(fila, "supervisor_julio") or _valor(fila, "supervisor_h1")
            ruta_id = obtener_o_crear_ruta(con, ruta_norm, supervisor)

        cur = con.execute(
            """INSERT INTO equipos (id_interno, tipo, serial_fabrica, modelo, fabricante, estado, notas, requiere_revision, ruta_asignada_id)
               VALUES (?, 'impresora', ?, ?, ?, ?, ?, ?, ?)""",
            (id_interno, fila["serie_norm"], modelo, fabricante, estado, notas, requiere_revision, ruta_id),
        )
        if ruta_id:
            sembrar_salida_inicial(con, cur.lastrowid, ruta_id, operador_carga_id)
        print(f"Impresora nueva: {id_interno} ({fila['serie_norm']}) -- necesita etiqueta física nueva.")
        siguiente_imp += 1
        nuevos_imp += 1

    return {
        "telefonos_nuevos": nuevos_tel,
        "impresoras_nuevas": nuevos_imp,
        "impresoras_actualizadas": actualizados_imp,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reimportar",
        action="store_true",
        help="Agrega equipos nuevos y actualiza datos de los existentes sin renumerar ningún id_interno ya asignado.",
    )
    args = parser.parse_args()

    con = conectar()
    asegurar_esquema(con)

    hay_datos = con.execute("SELECT COUNT(*) AS n FROM equipos").fetchone()["n"] > 0

    if hay_datos and not args.reimportar:
        print("La tabla 'equipos' ya tiene datos. Para no arriesgar renumerar id_interno ya usados en etiquetas")
        print("físicas, no se hace nada. Si quieres agregar equipos nuevos o actualizar datos de los existentes")
        print("sin tocar los id_interno ya asignados, corre: python importar_catalogo.py --reimportar")
        con.close()
        return

    operador_carga_id = obtener_o_crear_operador(con, OPERADOR_CARGA_INICIAL)

    if not hay_datos:
        n_tel, duplicados_tel = importar_telefonos(con, operador_carga_id)
        n_imp = importar_impresoras(con, operador_carga_id)
        # No es una persona real: no debe aparecer como opción al elegir
        # operador en la app, solo queda visible como autor de los
        # movimientos sembrados si alguien lo busca en la bitácora.
        con.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (operador_carga_id,))
        con.commit()
        print(f"Importación inicial completa: {n_tel} teléfonos, {n_imp} impresoras.")
        if duplicados_tel:
            print(f"AVISO: se omitieron {duplicados_tel} teléfono(s) con TELEFONO duplicado -- corrígelos en el Excel y vuelve a correr.")
        revision = con.execute("SELECT COUNT(*) AS n FROM equipos WHERE requiere_revision = 1").fetchone()["n"]
        print(f"Equipos marcados requiere_revision=1 (resolver desde /inventario más adelante): {revision}")
    else:
        resultado = actualizar_o_agregar(con, operador_carga_id)
        con.commit()
        print(
            f"--reimportar: {resultado['telefonos_nuevos']} teléfonos nuevos, "
            f"{resultado['impresoras_nuevas']} impresoras nuevas, "
            f"{resultado['impresoras_actualizadas']} impresoras existentes actualizadas."
        )

    con.close()


if __name__ == "__main__":
    main()
