"""Etapa A (solo lectura): cruza los Excel de origen y genera un reporte
de discrepancias para revisión humana en Excel, ANTES de importar nada a
la base de datos.

No escribe a la base de datos ni modifica los Excel originales. Se corre
las veces que haga falta mientras se revisan/corrigen las hojas fuente;
solo cuando el reporte se vea razonable se corre importar_catalogo.py.

Uso:
    python scripts/generar_reporte_conflictos.py
"""
from __future__ import annotations

import pandas as pd

import comun

SALIDA = comun.BASE_DIR / "reporte_conflictos.xlsx"

COLUMNAS_IMPRESORAS = {
    "serie_norm": "Serial",
    "ruta_h1": "Ruta (Hoja1)",
    "ruta_julio": "Ruta (Julio 2026)",
    "ruta_final_norm": "Ruta que se importará",
    "supervisor_h1": "Supervisor (Hoja1)",
    "supervisor_julio": "Supervisor (Julio 2026)",
    "modelo_it": "Modelo",
    "fabricante_it": "Fabricante",
    "nombre_it": "Nombre en DATA IT",
    "ubicacion_it": "Ubicación (DATA IT)",
    "asignado_a_it": "Asignado a (persona, DATA IT)",
    "comentario_julio": "Comentario (Julio 2026)",
    "sticker_actualizado_julio": "Sticker actualizado",
    "_en_hoja1": "¿Está en Hoja1?",
    "_en_julio": "¿Está en Julio 2026?",
    "_en_data_it": "¿Está en DATA IT?",
}


def _preparar(df: pd.DataFrame) -> pd.DataFrame:
    columnas = [c for c in COLUMNAS_IMPRESORAS if c in df.columns]
    return df[columnas].rename(columns=COLUMNAS_IMPRESORAS)


def main() -> None:
    telefonos = comun.cargar_telefonos()
    resultado = comun.construir_vista_impresoras()
    vista = resultado["vista"]

    conflictos_ruta = _preparar(vista[vista["conflicto_ruta"]])
    sin_ruta = _preparar(vista[vista["sin_ruta_asignada"]])
    huerfanos = _preparar(vista[vista["huerfano_de_it"]])
    it_inconsistente = _preparar(vista[vista["data_it_inconsistente"]])
    telefonos_duplicados = telefonos[telefonos["duplicado"]]

    resumen = pd.DataFrame([
        {"Categoría": "Impresoras: total (unión de las 3 hojas)", "Cantidad": len(vista)},
        {"Categoría": "Impresoras: conflicto de ruta entre Hoja1 y Julio2026", "Cantidad": len(conflictos_ruta)},
        {"Categoría": "Impresoras: sin ruta asignada en el censo de Julio", "Cantidad": len(sin_ruta)},
        {"Categoría": "Impresoras: activas en una ruta pero ausentes de DATA IT", "Cantidad": len(huerfanos)},
        {"Categoría": "Impresoras: duplicadas dentro de Hoja1", "Cantidad": len(resultado["duplicados_hoja1"])},
        {"Categoría": "Impresoras: duplicadas dentro de Julio2026", "Cantidad": len(resultado["duplicados_julio"])},
        {"Categoría": "Impresoras: Nombre en DATA IT no calza con MAZPRNES-{serie}", "Cantidad": len(it_inconsistente)},
        {"Categoría": "Impresoras: requieren revisión (unión de todo lo anterior)", "Cantidad": int(vista["requiere_revision"].sum())},
        {"Categoría": "Teléfonos: total", "Cantidad": len(telefonos)},
        {"Categoría": "Teléfonos: número duplicado", "Cantidad": len(telefonos_duplicados)},
    ])

    with pd.ExcelWriter(SALIDA, engine="openpyxl") as writer:
        resumen.to_excel(writer, sheet_name="Resumen", index=False)
        conflictos_ruta.to_excel(writer, sheet_name="Conflictos_Ruta", index=False)
        sin_ruta.to_excel(writer, sheet_name="Sin_Ruta_Asignada", index=False)
        huerfanos.to_excel(writer, sheet_name="Huerfanos_De_IT", index=False)
        it_inconsistente.to_excel(writer, sheet_name="DATA_IT_Inconsistencias", index=False)
        resultado["duplicados_hoja1"].to_excel(writer, sheet_name="Duplicados_Hoja1", index=False)
        resultado["duplicados_julio"].to_excel(writer, sheet_name="Duplicados_Julio2026", index=False)
        telefonos_duplicados.to_excel(writer, sheet_name="Telefonos_Duplicados", index=False)

    print(f"Reporte generado: {SALIDA}")
    print()
    print(resumen.to_string(index=False))


if __name__ == "__main__":
    main()
