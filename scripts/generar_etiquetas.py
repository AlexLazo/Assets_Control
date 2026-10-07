"""Genera un PDF con una etiqueta QR por cada equipo en la base de datos,
lista para imprimir en una impresora normal sobre papel adhesivo/laminado
comprado aparte (no hay impresora térmica de etiquetas).

Hay dos formatos, porque los equipos se etiquetan en superficies distintas:
  - Teléfonos: etiqueta chica en grilla de 3 x 8 por hoja.
  - Impresoras: etiqueta de 9.5 cm (ancho) x 4.5 cm (alto), 2 x 5 por hoja
    tamaño carta. Cada recuadro punteado mide exactamente esas medidas y los
    recuadros comparten borde, así que se corta por las líneas sin desperdicio.
Si el PDF trae de los dos tipos, primero van las hojas de teléfonos y luego
las de impresoras (no se mezclan en una misma hoja porque miden distinto).

Se lee de la base de datos, no de los Excel: desde que se corrió
importar_catalogo.py, la base es la fuente de verdad de los id_interno.

El QR codifica solo el texto plano del id_interno (ej. "IMP-047"), nunca
una URL -- así, si el día de mañana el servidor cambia de PC o de IP, no
hace falta reimprimir y repegar todas las etiquetas físicas. Junto al QR se
imprime el ID y el serial (impresoras) o ruta y teléfono (teléfonos), como
respaldo por si el lector falla o el QR se ensucia.

Uso:
    python scripts/generar_etiquetas.py
"""
from __future__ import annotations

import io
import sqlite3

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm, inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import comun

DB_PATH = comun.BASE_DIR / "instance" / "activos.db"

# Los datos que se imprimen junto al QR. El QR sigue codificando SOLO el
# id_interno (corto y estable: se lee rápido y no caduca si cambia la ruta o
# el serial); el resto es texto de apoyo para identificar el equipo a simple
# vista.
EQUIPOS_SQL = """SELECT e.id_interno, e.tipo, e.serial_fabrica, e.numero_telefono, r.codigo AS ruta
                 FROM equipos e LEFT JOIN rutas r ON r.id = e.ruta_asignada_id"""
SALIDA = comun.BASE_DIR / "etiquetas_qr.pdf"

MARGEN = 0.4 * inch

# --- Teléfonos (grilla ajustable; el papel es adhesivo genérico)
TEL_COLUMNAS = 3
TEL_FILAS = 8
TEL_QR_PREFERIDO = 1.15 * inch

# --- Impresoras: medida de la etiqueta final. Para cambiarla, solo estas dos.
IMP_ANCHO = 9.5 * cm
IMP_ALTO = 4.5 * cm
IMP_PADDING = 9  # aire entre el QR / texto y el borde de la etiqueta (puntos)


def obtener_equipos() -> list[sqlite3.Row]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    filas = con.execute(EQUIPOS_SQL + " ORDER BY e.id_interno").fetchall()
    con.close()
    return filas


def generar_qr(texto: str) -> ImageReader:
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=ERROR_CORRECT_M)
    qr.add_data(texto)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="black", back_color="white")
    # imagen es un qrcode.image.pil.PilImage, no un PIL.Image.Image real, y
    # ImageReader no lo reconoce directamente -- pasar por un buffer PNG en
    # memoria es la forma robusta de dárselo a reportlab.
    buffer = io.BytesIO()
    imagen.save(buffer)
    buffer.seek(0)
    return ImageReader(buffer)


def generar_qr_png_bytes(texto: str) -> bytes:
    """PNG de un solo QR, para reimprimir una sola etiqueta perdida sin
    tener que generar el PDF completo de nuevo."""
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=ERROR_CORRECT_M)
    qr.add_data(texto)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    imagen.save(buffer)
    return buffer.getvalue()


def _lineas_telefono(equipo) -> list[tuple[str, str, float]]:
    return [
        (equipo["id_interno"], "Helvetica-Bold", 13),
        (f"Ruta {equipo['ruta']}" if equipo["ruta"] else "Ruta sin asignar", "Helvetica", 9),
        (f"Tel {equipo['numero_telefono']}" if equipo["numero_telefono"] else "Tel sin dato", "Helvetica", 9),
    ]


def _ajustar(texto: str, fuente: str, tamano: float, ancho_max: float) -> float:
    """Reduce el tamaño de letra hasta que el texto quepa en `ancho_max`."""
    while tamano > 6 and stringWidth(texto, fuente, tamano) > ancho_max:
        tamano -= 0.5
    return tamano


def _hoja_telefonos(c: canvas.Canvas, equipos: list) -> None:
    ancho_pagina, alto_pagina = letter
    ancho_celda = (ancho_pagina - 2 * MARGEN) / TEL_COLUMNAS
    alto_celda = (alto_pagina - 2 * MARGEN) / TEL_FILAS
    por_pagina = TEL_COLUMNAS * TEL_FILAS
    qr_tamano = min(TEL_QR_PREFERIDO, alto_celda - 14, ancho_celda * 0.5)

    for indice, equipo in enumerate(equipos):
        pos = indice % por_pagina
        if indice > 0 and pos == 0:
            c.showPage()

        x0 = MARGEN + (pos % TEL_COLUMNAS) * ancho_celda
        y0 = alto_pagina - MARGEN - (pos // TEL_COLUMNAS + 1) * alto_celda

        # Guía de corte: ayuda a cortar derecho en papel sin líneas pre-impresas.
        c.setDash(2, 2)
        c.setLineWidth(0.4)
        c.rect(x0 + 2, y0 + 2, ancho_celda - 4, alto_celda - 4)
        c.setDash()

        qr_x = x0 + 8
        c.drawImage(generar_qr(equipo["id_interno"]), qr_x, y0 + (alto_celda - qr_tamano) / 2, width=qr_tamano, height=qr_tamano)

        tx = qr_x + qr_tamano + 8
        ancho_texto = x0 + ancho_celda - tx - 6
        lineas = _lineas_telefono(equipo)
        y = y0 + alto_celda / 2 + (len(lineas) * 13) / 2 - 6
        for texto, fuente, tamano in lineas:
            tamano = _ajustar(texto, fuente, tamano, ancho_texto)
            c.setFont(fuente, tamano)
            c.drawString(tx, y, texto)
            y -= tamano + 3.5


def formato_impresoras() -> tuple[int, int]:
    """(columnas, filas) de etiquetas de impresora que caben en una hoja carta."""
    ancho_pagina, alto_pagina = letter
    return int((ancho_pagina - 2 * MARGEN) // IMP_ANCHO), int((alto_pagina - 2 * MARGEN) // IMP_ALTO)


def _hoja_impresoras(c: canvas.Canvas, equipos: list) -> None:
    ancho_pagina, alto_pagina = letter
    columnas, filas = formato_impresoras()
    por_pagina = columnas * filas
    # La grilla queda centrada en la hoja; las etiquetas se tocan entre sí.
    izq = (ancho_pagina - columnas * IMP_ANCHO) / 2
    sup = (alto_pagina - filas * IMP_ALTO) / 2
    qr_tamano = IMP_ALTO - 2 * IMP_PADDING

    for indice, equipo in enumerate(equipos):
        pos = indice % por_pagina
        if indice > 0 and pos == 0:
            c.showPage()

        x0 = izq + (pos % columnas) * IMP_ANCHO
        y0 = alto_pagina - sup - (pos // columnas + 1) * IMP_ALTO

        # Guía de corte: el recuadro mide exactamente IMP_ANCHO x IMP_ALTO.
        c.setDash(2, 2)
        c.setLineWidth(0.4)
        c.rect(x0, y0, IMP_ANCHO, IMP_ALTO)
        c.setDash()

        qr_x = x0 + IMP_PADDING + 3
        c.drawImage(generar_qr(equipo["id_interno"]), qr_x, y0 + IMP_PADDING, width=qr_tamano, height=qr_tamano)

        tx = qr_x + qr_tamano + 12
        ancho_texto = x0 + IMP_ANCHO - tx - IMP_PADDING
        serial = equipo["serial_fabrica"]
        tiene_serial = bool(serial) and serial != "N/D"

        # (texto, fuente, tamaño, línea base respecto al centro vertical, gris)
        # Posiciones fijas: así el ID, la leyenda y el serial nunca se encimen.
        centro = y0 + IMP_ALTO / 2
        bloque = [
            (equipo["id_interno"], "Helvetica-Bold", 30, 10, 0),
            ("NÚMERO DE SERIE", "Helvetica", 7.5, -14, 0.45),
            (
                serial if tiene_serial else "Sin serial legible",
                "Helvetica-Bold" if tiene_serial else "Helvetica",
                15 if tiene_serial else 11,
                -31,
                0,
            ),
        ]
        for texto, fuente, tamano, base, gris in bloque:
            tamano = _ajustar(texto, fuente, tamano, ancho_texto)
            c.setFont(fuente, tamano)
            c.setFillGray(gris)
            c.drawString(tx, centro + base, texto)
        c.setFillGray(0)


def paginas_necesarias(equipos: list) -> dict[str, int]:
    """Cuántas hojas ocupa cada tipo, para informar al usuario."""
    col, fil = formato_impresoras()
    tel = sum(1 for e in equipos if e["tipo"] != "impresora")
    imp = len(equipos) - tel
    return {
        "telefono": -(-tel // (TEL_COLUMNAS * TEL_FILAS)),
        "impresora": -(-imp // (col * fil)),
    }


def generar_pdf(equipos: list[sqlite3.Row], destino=None) -> None:
    """`destino` puede ser una ruta de archivo o un objeto tipo-archivo
    (ej. io.BytesIO) -- reportlab acepta ambos, así la misma función sirve
    para el script de línea de comandos y para el endpoint web que arma el
    PDF en memoria para descargar."""
    if destino is None:
        destino = str(SALIDA)

    telefonos = [e for e in equipos if e["tipo"] != "impresora"]
    impresoras = [e for e in equipos if e["tipo"] == "impresora"]

    c = canvas.Canvas(destino, pagesize=letter)
    if telefonos:
        _hoja_telefonos(c, telefonos)
    if telefonos and impresoras:
        c.showPage()
    if impresoras:
        _hoja_impresoras(c, impresoras)
    c.save()


def main() -> None:
    equipos = obtener_equipos()
    if not equipos:
        print("No hay equipos en la base de datos. Corre primero scripts/importar_catalogo.py")
        return
    generar_pdf(equipos)
    p = paginas_necesarias(equipos)
    print(f"Generadas {len(equipos)} etiquetas: {p['telefono']} hoja(s) de teléfonos y {p['impresora']} de impresoras -> {SALIDA}")


if __name__ == "__main__":
    main()
