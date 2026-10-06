"""Genera un PDF con una etiqueta QR por cada equipo en la base de datos,
lista para imprimir en una impresora normal sobre papel adhesivo/laminado
comprado aparte (no hay impresora térmica de etiquetas).

Se lee de la base de datos, no de los Excel: desde que se corrió
importar_catalogo.py, la base es la fuente de verdad de los id_interno.

El QR codifica solo el texto plano del id_interno (ej. "IMP-047"), nunca
una URL -- así, si el día de mañana el servidor cambia de PC o de IP, no
hace falta reimprimir y repegar todas las etiquetas físicas. Debajo del
QR se imprime el mismo ID en texto grande, como respaldo por si el lector
falla o el QR se ensucia (las pantallas de escaneo aceptan tecleo igual
que lectura).

Uso:
    python scripts/generar_etiquetas.py
"""
from __future__ import annotations

import io
import sqlite3

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import comun

DB_PATH = comun.BASE_DIR / "instance" / "activos.db"
SALIDA = comun.BASE_DIR / "etiquetas_qr.pdf"

# Grilla ajustable -- el papel es adhesivo genérico, no una hoja pre-cortada
# de marca conocida, así que estos valores se pueden retocar según lo que
# realmente se compre.
COLUMNAS = 3
FILAS = 8
MARGEN = 0.4 * inch
QR_TAMANO_PREFERIDO = 1.1 * inch
ALTO_TEXTO = 16       # banda reservada abajo de cada celda para el ID en texto
ESPACIO_SUPERIOR = 6  # aire entre el QR y el borde superior de la celda


def obtener_equipos() -> list[sqlite3.Row]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    filas = con.execute("SELECT id_interno, tipo FROM equipos ORDER BY id_interno").fetchall()
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


def generar_pdf(equipos: list[sqlite3.Row], destino=None) -> None:
    """`destino` puede ser una ruta de archivo o un objeto tipo-archivo
    (ej. io.BytesIO) -- reportlab acepta ambos, así la misma función sirve
    para el script de línea de comandos y para el endpoint web que arma el
    PDF en memoria para descargar."""
    if destino is None:
        destino = str(SALIDA)

    ancho_pagina, alto_pagina = letter
    ancho_celda = (ancho_pagina - 2 * MARGEN) / COLUMNAS
    alto_celda = (alto_pagina - 2 * MARGEN) / FILAS
    por_pagina = COLUMNAS * FILAS

    # El QR nunca puede ser más grande que el espacio que realmente le
    # queda libre en la celda (ancho completo, alto menos la banda de
    # texto) -- si se calcula mal, el QR y el ID de texto se encimarían.
    qr_tamano = min(
        QR_TAMANO_PREFERIDO,
        ancho_celda - 16,
        alto_celda - ALTO_TEXTO - ESPACIO_SUPERIOR,
    )

    c = canvas.Canvas(destino, pagesize=letter)

    for indice, equipo in enumerate(equipos):
        pos_en_pagina = indice % por_pagina
        if indice > 0 and pos_en_pagina == 0:
            c.showPage()

        fila = pos_en_pagina // COLUMNAS
        columna = pos_en_pagina % COLUMNAS

        x0 = MARGEN + columna * ancho_celda
        y0 = alto_pagina - MARGEN - (fila + 1) * alto_celda

        # Guía de corte -- ayuda a cortar derecho en papel genérico sin
        # líneas pre-impresas.
        c.setDash(2, 2)
        c.setLineWidth(0.4)
        c.rect(x0 + 2, y0 + 2, ancho_celda - 4, alto_celda - 4)
        c.setDash()

        qr_x = x0 + (ancho_celda - qr_tamano) / 2
        qr_y = y0 + ALTO_TEXTO
        c.drawImage(generar_qr(equipo["id_interno"]), qr_x, qr_y, width=qr_tamano, height=qr_tamano)

        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(x0 + ancho_celda / 2, y0 + 5, equipo["id_interno"])

    c.save()


def main() -> None:
    equipos = obtener_equipos()
    if not equipos:
        print("No hay equipos en la base de datos. Corre primero scripts/importar_catalogo.py")
        return
    generar_pdf(equipos)
    paginas = -(-len(equipos) // (COLUMNAS * FILAS))
    print(f"Generadas {len(equipos)} etiquetas en {paginas} página(s): {SALIDA}")


if __name__ == "__main__":
    main()
