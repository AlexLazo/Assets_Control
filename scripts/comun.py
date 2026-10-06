"""Lectura y cruce de los Excel de origen (teléfonos e impresoras).

Usado tanto por generar_reporte_conflictos.py (solo lectura, para revisión
humana en Excel) como por importar_catalogo.py (escribe a la base). Viven
en un solo lugar a propósito: si cada script normalizara distinto, el
reporte de conflictos ya no predeciría fielmente qué va a importar el
segundo script.

Nota de robustez: los nombres de columna del Excel real traen espacios al
final e inconsistencias de tildes/mayúsculas (ej. "NUMERO DE SERIE " con
espacio en unas hojas, "Número de serie" sin espacio en otra). `col()`
busca por nombre ignorando esas diferencias para que un espacio suelto no
rompa el script.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
ARCHIVO_TELEFONOS = BASE_DIR / "RTI SOYAPANGO.xlsx"
ARCHIVO_IMPRESORAS = BASE_DIR / "Levantamiento Impresoras 2026.xlsx"

# Valores que en "NUEVA RUTA" / "RUTA" significan "todavía sin ruta real",
# no una ruta válida. Confirmado contra los datos reales (31 + 7 casos).
RUTAS_SIN_ASIGNAR = {"PENDIENTE DE ASIGNAR", "DUMMY HOD", ""}


def _normalizar_nombre_columna(nombre: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().upper()


def col(df: pd.DataFrame, nombre_buscado: str) -> str:
    """Nombre real de columna en `df` para `nombre_buscado`, comparando sin
    tildes/espacios/mayúsculas. Lanza KeyError con las columnas disponibles
    si no encuentra nada parecido, en vez de fallar más abajo con un error
    críptico de pandas."""
    objetivo = _normalizar_nombre_columna(nombre_buscado)
    for real in df.columns:
        if _normalizar_nombre_columna(real) == objetivo:
            return real
    raise KeyError(f"No se encontró una columna parecida a {nombre_buscado!r}. Columnas disponibles: {list(df.columns)}")


def normalizar_serial(valor) -> str | None:
    if pd.isna(valor):
        return None
    texto = str(valor).strip().upper()
    if texto.startswith("S/N"):
        texto = texto[3:].strip()
    return texto or None


def normalizar_ruta(valor) -> str | None:
    if pd.isna(valor):
        return None
    texto = str(valor).strip().upper()
    return texto or None


def ruta_esta_asignada(ruta_norm) -> bool:
    # pd.isna() -- no basta con `bool(ruta_norm)`: un NaN de pandas (float)
    # es truthy en Python (`bool(float('nan'))` es True), así que un hueco
    # dejado por un outer merge se colaría como si fuera una ruta válida.
    if pd.isna(ruta_norm):
        return False
    return bool(ruta_norm) and ruta_norm not in RUTAS_SIN_ASIGNAR


def cargar_telefonos() -> pd.DataFrame:
    """Una fila por línea telefónica. Hoy esta fuente viene limpia (sin
    duplicados de TELEFONO), pero se marca igual por si algún día deja de
    estarlo -- más barato marcar la columna ahora que descubrir en
    producción que dos rutas comparten número."""
    df = pd.read_excel(ARCHIVO_TELEFONOS, sheet_name="BASE")
    telefonos = pd.DataFrame({
        "telefono": df[col(df, "TELEFONO")].astype(str).str.strip(),
        "ruta_norm": df[col(df, "RUTA")].apply(normalizar_ruta),
        "supervisor": df[col(df, "SUPERVISOR")],
        "observaciones": df[col(df, "OBSERVACIONES")],
        "pin": df[col(df, "CONTRASENA")].astype("string"),
    })
    telefonos["duplicado"] = telefonos["telefono"].duplicated(keep=False)
    return telefonos


def _cargar_hoja1() -> pd.DataFrame:
    df = pd.read_excel(ARCHIVO_IMPRESORAS, sheet_name="Hoja1")
    return pd.DataFrame({
        "serie_norm": df[col(df, "NUMERO DE SERIE")].apply(normalizar_serial),
        "ruta_h1": df[col(df, "RUTA")].apply(normalizar_ruta),
        "supervisor_h1": df[col(df, "SUPERVISOR")],
        "en_reparacion_h1": df[col(df, "IS EN REPARACION")].notna(),
        "retorno_reparacion_h1": df[col(df, "RETORNO D REPARACION")].notna(),
        "cambio_bateria_h1": df[col(df, "CAMBIO DE BATERIA")],
    })


def _cargar_julio() -> pd.DataFrame:
    df = pd.read_excel(ARCHIVO_IMPRESORAS, sheet_name="Impresoras Julio 2026")
    return pd.DataFrame({
        "serie_norm": df[col(df, "NUMERO DE SERIE")].apply(normalizar_serial),
        "ruta_julio": df[col(df, "NUEVA RUTA")].apply(normalizar_ruta),
        "supervisor_julio": df[col(df, "SUPERVISOR")],
        "modelo_julio": df[col(df, "Columna1")],
        "sticker_actualizado_julio": df[col(df, "STICKER ACTUALIZADO")],
        "comentario_julio": df[col(df, "COMENTARIO")],
    })


def _cargar_data_it() -> pd.DataFrame:
    df = pd.read_excel(ARCHIVO_IMPRESORAS, sheet_name="DATA IT")
    serie = df[col(df, "NUMERO DE SERIE")].apply(normalizar_serial)
    nombre = df[col(df, "Nombre")].astype(str).str.strip()
    nombre_esperado = "MAZPRNES-" + serie.fillna("")
    return pd.DataFrame({
        "serie_norm": serie,
        "nombre_it": nombre,
        "nombre_it_calza": nombre == nombre_esperado,
        "fabricante_it": df[col(df, "Fabricante")],
        "modelo_it": df[col(df, "Modelo")],
        "ubicacion_it": df[col(df, "Ubicacion")],
        "asignado_a_it": df[col(df, "Asignado a")],
        "comentarios_it": df[col(df, "Comentarios")],
    })


def construir_vista_impresoras() -> dict:
    """Devuelve un dict con:
    - 'vista': una fila por serial único (unión de las 3 hojas), con
      columnas de cada fuente + banderas de conflicto ya calculadas.
    - 'duplicados_hoja1' / 'duplicados_julio': filas crudas de seriales
      repetidos dentro de la misma hoja (van aparte del merge principal).

    Este es el ÚNICO lugar donde se decide qué es un conflicto: tanto el
    reporte como el importador leen las mismas banderas de aquí.
    """
    hoja1 = _cargar_hoja1()
    julio = _cargar_julio()
    data_it = _cargar_data_it()

    duplicados_hoja1 = hoja1[hoja1["serie_norm"].notna() & hoja1["serie_norm"].duplicated(keep=False)].sort_values("serie_norm")
    duplicados_julio = julio[julio["serie_norm"].notna() & julio["serie_norm"].duplicated(keep=False)].sort_values("serie_norm")

    hoja1 = hoja1.dropna(subset=["serie_norm"])
    julio = julio.dropna(subset=["serie_norm"])
    data_it = data_it.dropna(subset=["serie_norm"])

    hoja1["_en_hoja1"] = True
    julio["_en_julio"] = True
    data_it["_en_data_it"] = True

    hoja1_u = hoja1.drop_duplicates(subset="serie_norm", keep="first")
    julio_u = julio.drop_duplicates(subset="serie_norm", keep="first")
    data_it_u = data_it.drop_duplicates(subset="serie_norm", keep="first")

    vista = data_it_u.merge(hoja1_u, on="serie_norm", how="outer")
    vista = vista.merge(julio_u, on="serie_norm", how="outer")

    for marcador in ("_en_data_it", "_en_hoja1", "_en_julio"):
        vista[marcador] = vista[marcador].fillna(False)

    ruta_h1 = vista["ruta_h1"].fillna("")
    ruta_julio = vista["ruta_julio"].fillna("")
    ambas = vista["_en_hoja1"] & vista["_en_julio"]

    vista["conflicto_ruta"] = ambas & (ruta_h1 != ruta_julio)
    vista["sin_ruta_asignada"] = vista["_en_julio"] & ~vista["ruta_julio"].apply(ruta_esta_asignada)

    # OJO: "está en DATA IT pero no en ninguna hoja operativa" NO es una
    # anomalía (es normal para un repuesto guardado sin ruta asignada).
    # Lo que sí es sospechoso es el caso contrario: un equipo que alguien
    # está tratando como activo en una ruta (aparece en Hoja1 y/o Julio)
    # pero que ni siquiera existe en el maestro de IT.
    en_operativa = vista["_en_hoja1"] | vista["_en_julio"]
    vista["huerfano_de_it"] = en_operativa & ~vista["_en_data_it"]
    vista["data_it_inconsistente"] = vista["_en_data_it"] & ~vista["nombre_it_calza"].fillna(True)

    # Ruta final: la más reciente (Julio 2026) manda si existe; si no
    # aparece en el censo de julio, se usa la de Hoja1 como respaldo.
    vista["ruta_final_norm"] = vista["ruta_julio"].where(vista["_en_julio"], vista["ruta_h1"])

    vista["requiere_revision"] = (
        vista["conflicto_ruta"]
        | vista["sin_ruta_asignada"]
        | vista["huerfano_de_it"]
        | vista["data_it_inconsistente"]
    )

    return {
        "vista": vista,
        "duplicados_hoja1": duplicados_hoja1,
        "duplicados_julio": duplicados_julio,
    }
