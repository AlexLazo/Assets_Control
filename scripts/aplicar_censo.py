"""Aplica un censo de impresoras (ej. "Levantamiento Impresoras Septiembre
2026.xlsx") sobre la base ya importada, sin renumerar ningún id_interno.

El archivo trae (detectadas por sus columnas, no por el nombre de hoja):
  - una hoja de censo con 'NUEVA RUTA', 'NUMERO DE SERIE' y 'Status ...'
  - opcionalmente una hoja de mantenimiento con 'S/N' y 'Status'

Qué hace con cada impresora que SÍ está en la base (se cruza por serial):
  - ruta asignada = la ruta del censo; 'Pendiente de Asignar' / 'DUMMY HOD'
    la dejan sin asignar (hay que asignarla desde Inventario).
  - Status NOK -> requiere_revision.
  - En la hoja de mantenimiento -> se abre/actualiza su mantenimiento.

Con --depurar (el censo es la lista oficial de impresoras que existen):
  - se ELIMINAN de la base las impresoras que el censo no menciona (con su
    historial; la lista queda en impresoras_eliminadas.xlsx y el .db se
    respalda antes), y se CREAN las del censo que no existen en la base.
  - 'N/D' = impresora real sin serial legible: son equipos distintos.
  - un serial del censo casi idéntico a UNO solo de la base que el censo no
    menciona se asume error de tecleo (misma impresora).

Por defecto es VISTA PREVIA: no escribe nada. Con --aplicar hace respaldo
del .db y aplica.

Uso:
    python scripts/aplicar_censo.py "C:\\ruta\\censo.xlsx" --depurar
    python scripts/aplicar_censo.py "C:\\ruta\\censo.xlsx" --depurar --aplicar
"""
from __future__ import annotations

import argparse
import difflib
import shutil
import sqlite3
from datetime import datetime

import pandas as pd

import comun

DB_PATH = comun.BASE_DIR / "instance" / "activos.db"
MARCA = "[Censo Sep 2026]"
SIN_RUTA = {"PENDIENTE DE ASIGNAR", "DUMMY HOD", "", "NAN", "NONE"}


def norm_serie(v) -> str | None:
    if pd.isna(v):
        return None
    s = str(v).strip().upper()
    if s.startswith("S/N"):
        s = s[3:].strip()
    return s or None


def cargar(archivo: str):
    xl = pd.ExcelFile(archivo)
    censo = mant = None
    for hoja in xl.sheet_names:
        df = xl.parse(hoja)
        cols = {str(c).strip().upper() for c in df.columns}
        if "NUEVA RUTA" in cols and "NUMERO DE SERIE" in cols:
            censo = df
        elif "S/N" in cols and "STATUS" in cols:
            mant = df
    if censo is None:
        raise SystemExit("No encontré una hoja de censo (con columnas NUEVA RUTA y NUMERO DE SERIE).")
    censo.columns = [str(c).strip() for c in censo.columns]
    censo["serie"] = censo["NUMERO DE SERIE"].apply(norm_serie)
    col_status = next((c for c in censo.columns if c.upper().startswith("STATUS")), None)
    censo["status"] = censo[col_status].astype(str).str.strip() if col_status else ""
    censo["ruta"] = censo["NUEVA RUTA"].astype(str).str.strip().str.upper()
    if mant is not None:
        mant.columns = [str(c).strip() for c in mant.columns]
        mant["serie"] = mant["S/N"].apply(norm_serie)

    # 'N/D' son impresoras distintas sin serial legible, no duplicados. Un
    # serial repetido con la MISMA ruta es la misma impresora listada dos
    # veces; con rutas distintas es un conflicto que se marca para revisión.
    nd = censo[censo["serie"] == "N/D"].copy()
    resto = censo[(censo["serie"] != "N/D") & censo["serie"].notna()].copy()
    rutas_por_serie = resto.groupby("serie")["ruta"].nunique()
    conflictos = set(rutas_por_serie[rutas_por_serie > 1].index)
    unico = resto.drop_duplicates("serie").copy()
    unico["conflicto"] = unico["serie"].isin(conflictos)
    return unico, mant, nd


def resolver_alias(con, censo, mant) -> dict:
    """Un serial del censo que no existe en la base pero es casi idéntico a UN
    solo serial de la base que el censo no menciona es un error de tecleo
    (ej. 2529N05F9 vs 25295N05F9): se trata como la misma impresora."""
    en_db = {r[0] for r in con.execute("SELECT serial_fabrica FROM equipos WHERE tipo='impresora'")}
    en_censo = set(censo["serie"].dropna())
    huerfanos = sorted(en_db - en_censo - {"N/D"})
    alias = {}
    for sc in sorted(en_censo - en_db):
        cercanos = [h for h in huerfanos if difflib.SequenceMatcher(None, sc, h).ratio() >= 0.9]
        if len(cercanos) == 1:
            alias[sc] = cercanos[0]
    if alias:
        censo["serie"] = censo["serie"].replace(alias)
        if mant is not None:
            mant["serie"] = mant["serie"].replace(alias)
        print("Seriales del censo asumidos como mal tecleados (se conserva el de la base):", alias)
    return alias


def plan(con: sqlite3.Connection, censo: pd.DataFrame, mant):
    db = {
        r["serial_fabrica"]: dict(r)
        for r in con.execute(
            """SELECT e.id, e.id_interno, e.serial_fabrica, e.estado, e.notas, e.ruta_asignada_id, r.codigo AS ruta
               FROM equipos e LEFT JOIN rutas r ON r.id = e.ruta_asignada_id WHERE e.tipo = 'impresora'"""
        )
    }
    p = {"rutas": [], "sin_ruta": [], "nok": [], "no_en_db": [], "duplicados": [], "ausentes": [], "mant": [], "mant_no_db": [], "rep_sin_confirmar": []}

    dup = set(censo.loc[censo["conflicto"], "serie"])
    p["duplicados"] = sorted(dup)
    vistos = set()
    for _, f in censo.iterrows():
        s = f["serie"]
        if s is None or s in dup:
            continue
        vistos.add(s)
        d = db.get(s)
        if d is None:
            p["no_en_db"].append((s, f["ruta"], f["status"]))
            continue
        nueva = None if f["ruta"] in SIN_RUTA else f["ruta"]
        if nueva != (d["ruta"] or None):
            (p["rutas"] if nueva else p["sin_ruta"]).append((d["id_interno"], s, d["ruta"], nueva))
        if f["status"].upper().startswith("NOK"):
            p["nok"].append((d["id_interno"], s))

    p["ausentes"] = [(d["id_interno"], s) for s, d in db.items() if s not in vistos and s not in dup and s != "N/D"]

    en_mant = set()
    if mant is not None:
        for _, f in mant.iterrows():
            s = f["serie"]
            d = db.get(s)
            if d is None:
                p["mant_no_db"].append((s, f["Status"]))
                continue
            en_mant.add(s)
            p["mant"].append((d["id_interno"], s, str(f["Status"]).strip(), d["estado"]))
        p["rep_sin_confirmar"] = [(d["id_interno"], s) for s, d in db.items() if d["estado"] == "reparacion" and s not in en_mant]
    return p, db


def resumen(p) -> None:
    print("\n=== VISTA PREVIA DEL CENSO ===")
    print(f"Cambian a OTRA ruta concreta ........ {len(p['rutas'])}")
    print(f"Pasan a 'sin ruta asignada' .......... {len(p['sin_ruta'])}")
    print(f"Marcadas NOK (requiere revisión) ..... {len(p['nok'])}")
    print(f"Mantenimiento: se abre/actualiza ..... {len(p['mant'])}   (serial no está en la base: {len(p['mant_no_db'])})")
    print(f"En reparación en la base pero NO en la hoja de mantenimiento (solo se reportan): {len(p['rep_sin_confirmar'])}")
    print(f"Seriales del censo que NO existen en la base: {len(p['no_en_db'])} -> {[x[0] for x in p['no_en_db']]}")
    print(f"Seriales repetidos con rutas distintas en el censo (se marcan para revisión): {len(p['duplicados'])} -> {p['duplicados']}")
    print(f"Impresoras de la base que el censo no menciona: {len(p['ausentes'])}")


def reporte(p, ruta_xlsx) -> None:
    with pd.ExcelWriter(ruta_xlsx) as w:
        pd.DataFrame(p["rutas"], columns=["id", "serial", "ruta_actual", "ruta_censo"]).to_excel(w, sheet_name="Cambia_ruta", index=False)
        pd.DataFrame(p["sin_ruta"], columns=["id", "serial", "ruta_actual", "ruta_censo"]).to_excel(w, sheet_name="Queda_sin_ruta", index=False)
        pd.DataFrame(p["nok"], columns=["id", "serial"]).to_excel(w, sheet_name="NOK", index=False)
        pd.DataFrame(p["mant"], columns=["id", "serial", "status_censo", "estado_actual"]).to_excel(w, sheet_name="Mantenimiento", index=False)
        pd.DataFrame(p["rep_sin_confirmar"], columns=["id", "serial"]).to_excel(w, sheet_name="Reparacion_sin_confirmar", index=False)
        pd.DataFrame(p["no_en_db"], columns=["serial", "ruta", "status"]).to_excel(w, sheet_name="Serial_no_existe", index=False)
        pd.DataFrame(p["ausentes"], columns=["id", "serial"]).to_excel(w, sheet_name="No_aparecen_en_censo", index=False)
    print(f"Reporte detallado: {ruta_xlsx}")


def _nota(notas: str | None, texto: str) -> str:
    base = [x for x in (notas or "").split(" ; ") if x and not x.startswith(MARCA)]
    return " ; ".join(base + [f"{MARCA} {texto}"])


def aplicar(con: sqlite3.Connection, censo, mant, db, admin_id: int) -> None:
    import importar_catalogo as ic

    por_serie = {f["serie"]: f for _, f in censo.iterrows() if f["serie"]}
    dup = set(censo.loc[censo["conflicto"], "serie"])
    for s, d in db.items():
        if s == "N/D":
            continue
        f = por_serie.get(s)
        if f is None or s in dup:
            texto = "Serial repetido con rutas distintas en el censo." if s in dup else "No aparece en el censo."
            con.execute("UPDATE equipos SET requiere_revision = 1, notas = ? WHERE id = ?", (_nota(d["notas"], texto), d["id"]))
            continue
        nueva = None if f["ruta"] in SIN_RUTA else f["ruta"]
        ruta_id = ic.obtener_o_crear_ruta(con, nueva, None) if nueva else None
        if ruta_id != d["ruta_asignada_id"]:
            con.execute("UPDATE equipos SET ruta_asignada_id = ? WHERE id = ?", (ruta_id, d["id"]))
            if ruta_id:
                # La salida sembrada en la carga inicial no es un hecho real:
                # si es el último movimiento, se mueve a la ruta nueva.
                con.execute(
                    """UPDATE movimientos SET ruta_id = ? WHERE id = (
                           SELECT id FROM movimientos WHERE equipo_id = ? ORDER BY id DESC LIMIT 1)
                       AND operador_id IN (SELECT id FROM usuarios WHERE activo = 0 AND nombre LIKE 'Carga inicial%')""",
                    (ruta_id, d["id"]),
                )
        nok = f["status"].upper().startswith("NOK")
        texto = f"Status {f['status']}" + (" -- revisar" if nok else "")
        con.execute(
            "UPDATE equipos SET notas = ?, requiere_revision = ? WHERE id = ?",
            (_nota(d["notas"], texto), 1 if nok else 0, d["id"]),
        )

    if mant is not None:
        for _, f in mant.iterrows():
            d = db.get(f["serie"])
            if d is None:
                continue
            motivo = f"{MARCA} {str(f['Status']).strip()}"
            abierto = con.execute(
                "SELECT id FROM mantenimientos WHERE equipo_id = ? AND estado = 'en_reparacion'", (d["id"],)
            ).fetchone()
            if abierto:
                con.execute("UPDATE mantenimientos SET motivo = ? WHERE id = ?", (motivo, abierto["id"]))
                continue
            ultimo = con.execute(
                "SELECT ruta_id, tipo FROM movimientos WHERE equipo_id = ? ORDER BY id DESC LIMIT 1", (d["id"],)
            ).fetchone()
            if ultimo and ultimo["tipo"] == "salida":
                con.execute(
                    "INSERT INTO movimientos (equipo_id, ruta_id, operador_id, tipo, condicion) VALUES (?, ?, ?, 'retorno', ?)",
                    (d["id"], ultimo["ruta_id"], admin_id, "Retorno automático: el censo lo reporta en mantenimiento."),
                )
            con.execute("INSERT INTO mantenimientos (equipo_id, motivo, operador_envio_id) VALUES (?, ?, ?)", (d["id"], motivo, admin_id))
            con.execute("UPDATE equipos SET estado = 'reparacion' WHERE id = ?", (d["id"],))


def depurar(con: sqlite3.Connection, censo, nd, db) -> dict:
    """Deja la base con EXACTAMENTE las impresoras del censo: elimina las que
    no aparecen (con todo su historial) y crea las del censo que no existen."""
    import importar_catalogo as ic

    en_censo = set(censo["serie"])
    sobran = [d for s, d in db.items() if s not in en_censo and s != "N/D"]
    if sobran:
        pd.DataFrame(sobran)[["id_interno", "serial_fabrica", "estado", "ruta"]].to_excel(
            comun.BASE_DIR / "impresoras_eliminadas.xlsx", index=False
        )
    for d in sobran:
        for tabla in ("incidencias", "conteo_items", "mantenimientos", "movimientos"):
            con.execute(f"DELETE FROM {tabla} WHERE equipo_id = ?", (d["id"],))
        con.execute("DELETE FROM equipos WHERE id = ?", (d["id"],))

    fila_modelo = con.execute(
        "SELECT modelo FROM equipos WHERE tipo='impresora' AND modelo IS NOT NULL GROUP BY modelo ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    fila_fab = con.execute(
        "SELECT fabricante FROM equipos WHERE tipo='impresora' AND fabricante IS NOT NULL GROUP BY fabricante ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    modelo = fila_modelo[0] if fila_modelo else None
    fabricante = fila_fab[0] if fila_fab else None

    creadas = []
    siguiente = ic._siguiente_numero(con, "IMP")

    def crear(serial, nota):
        nonlocal siguiente
        id_interno = f"IMP-{siguiente:03d}"
        con.execute(
            """INSERT INTO equipos (id_interno, tipo, serial_fabrica, modelo, fabricante, notas, requiere_revision)
               VALUES (?, 'impresora', ?, ?, ?, ?, 1)""",
            (id_interno, serial, modelo, fabricante, nota),
        )
        siguiente += 1
        creadas.append((id_interno, serial))

    for s in sorted(en_censo - set(db)):
        crear(s, f"{MARCA} Serial del censo que no existía en la base; revisar si es un error de tecleo.")

    existentes_nd = con.execute("SELECT COUNT(*) FROM equipos WHERE tipo='impresora' AND serial_fabrica='N/D'").fetchone()[0]
    for _ in range(max(0, len(nd) - existentes_nd)):
        crear("N/D", f"{MARCA} Impresora sin serial legible; se identifica solo por su etiqueta QR.")
    return {"eliminadas": len(sobran), "creadas": creadas}


def aplicar_nd(con: sqlite3.Connection, nd) -> None:
    import importar_catalogo as ic

    ids = [r[0] for r in con.execute("SELECT id FROM equipos WHERE tipo='impresora' AND serial_fabrica='N/D' ORDER BY id")]
    for equipo_id, (_, f) in zip(ids, nd.iterrows()):
        ruta_id = None if f["ruta"] in SIN_RUTA else ic.obtener_o_crear_ruta(con, f["ruta"], None)
        con.execute("UPDATE equipos SET ruta_asignada_id = ? WHERE id = ?", (ruta_id, equipo_id))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivo")
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--depurar", action="store_true", help="Elimina las impresoras que el censo no menciona y crea las que faltan.")
    args = ap.parse_args()

    censo, mant, nd = cargar(args.archivo)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    resolver_alias(con, censo, mant)
    p, db = plan(con, censo, mant)
    resumen(p)
    if args.depurar:
        extra_nd = max(0, len(nd) - sum(1 for s in db if s == "N/D"))
        print(
            f"\n--depurar: se ELIMINARÍAN {len(p['ausentes'])} impresoras y se CREARÍAN "
            f"{len(p['no_en_db'])} (serial no existente) + {extra_nd} (N/D, {len(nd)} en el censo)."
        )
        print(f"Impresoras que quedarían: {len(db) - len(p['ausentes']) + len(p['no_en_db']) + extra_nd}")
    reporte(p, comun.BASE_DIR / "reporte_censo_septiembre.xlsx")

    if not args.aplicar:
        print("\nVista previa solamente: no se escribió nada. Agrega --aplicar para aplicarlo.")
        con.close()
        return

    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    respaldo = DB_PATH.with_name(f"activos_antes_de_censo_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH, respaldo)
    print(f"\nRespaldo creado: {respaldo}")
    admin = con.execute("SELECT id FROM usuarios WHERE rol = 'admin' AND activo = 1 ORDER BY id LIMIT 1").fetchone()
    if args.depurar:
        r = depurar(con, censo, nd, db)
        con.commit()
        print(f"Depuración: {r['eliminadas']} impresoras eliminadas (lista en impresoras_eliminadas.xlsx), {len(r['creadas'])} creadas: {r['creadas']}")
        _, db = plan(con, censo, mant)
        aplicar_nd(con, nd)
    aplicar(con, censo, mant, db, admin["id"])
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM equipos WHERE tipo='impresora'").fetchone()[0]
    print(f"Impresoras en la base ahora: {total}")
    print("Integridad referencial:", con.execute("PRAGMA foreign_key_check").fetchall() or "OK")
    con.close()


if __name__ == "__main__":
    main()
