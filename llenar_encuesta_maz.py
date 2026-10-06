"""
Automatiza el llenado de la encuesta "Safety Culture MAZ" (Microsoft Forms).

Requisitos:
    pip install playwright
    playwright install chromium

Uso:
    python llenar_encuesta_maz.py

Al arrancar, pregunta cuántos formularios hay que llenar y enviar. Por
cada uno, llena automáticamente:
  - FECHA -> fecha actual
  - Unidad de Negocio (pregunta 2) -> T2
  - BU - T2 (pregunta 3) -> CAM
  - REGIONAL / PAIS (pregunta 4) -> EL SALVADOR
  - EL SALVADOR / ciudad (pregunta 5) -> CD SOYAPANGO
  - Preguntas 6 a 39 -> "SI", excepto entre 1 y 5 preguntas elegidas
    al azar en cada formulario (configurable) que se marcan "NO".

Envía cada formulario automáticamente (clic en "Submit") y luego
recarga la página para empezar el siguiente, hasta completar la
cantidad pedida. Si un formulario falla a mitad de camino, se reporta
el error y se continúa con el siguiente en vez de detener todo el lote.

Cómo identifica cada pregunta: Microsoft Forms envuelve cada pregunta en
un bloque [data-automation-id="questionItem"] que contiene el número real
impreso (p. ej. "12.") en [data-automation-id="questionOrdinal"]. El
script lee ese número directamente del DOM en vez de asumir un orden o
una cantidad fija de preguntas por hoja, así que no importa si Forms
tarda en montar alguna pregunta: el script vuelve a escanear la página
las veces que haga falta hasta que todo lo visible esté contestado, y
solo entonces intenta avanzar. Si Forms igual bloquea el "Next" (algún
control tardío sin responder), se repite el escaneo en la misma hoja en
vez de quedarse atascado.

"""

import random
import re
import time
from datetime import date

from playwright.sync_api import sync_playwright, Locator, Page

FORM_URL = (
    "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?"
    "id=GUvwznZ3lEq4mzdcd6j5NpTnnoRsvqJIq2bf0Y2NzVhUMzFNVlhMTFVXNlQzQ1FTRDg4R1M1SFpJSS4u"
    "&origin=QRCode"
)

# Respuestas fijas para las preguntas de ubicación (por número de pregunta).
RESPUESTAS_FIJAS = {
    2: "T2",
    3: "CAM",
    4: "EL SALVADOR",
    5: "CD SOYAPANGO",
}

FIRST_SI_NO_QUESTION = 6
LAST_SI_NO_QUESTION = 39
MIN_NO = 1
MAX_NO = 5

HEADLESS = False  # ver el navegador mientras trabaja; poner True para modo silencioso
PASADAS_POR_HOJA = 8  # reintentos de escaneo antes de intentar "Next"
VIEWPORT = {"width": 1280, "height": 800}


def pick_no_questions() -> set[int]:
    total = list(range(FIRST_SI_NO_QUESTION, LAST_SI_NO_QUESTION + 1))
    cantidad = random.randint(MIN_NO, MAX_NO)
    elegidas = set(random.sample(total, cantidad))
    print(f"Preguntas marcadas como NO ({cantidad}): {sorted(elegidas)}")
    return elegidas


def find_option(scope: Locator, value: str) -> Locator | None:
    """Busca, dentro de `scope` (un radiogroup), la opción cuyo texto sea
    `value` una vez normalizado. Algunas preguntas del formulario original
    traen espacios invisibles (p. ej. un NBSP pegado desde Word: "SI\xa0"
    en vez de "SI"), así que NO se puede usar un selector CSS de igualdad
    exacta [data-automation-value="SI"] — hay que comparar el texto ya
    limpio de cada opción presente."""
    options = scope.locator("[data-automation-value]")
    for i in range(options.count()):
        option = options.nth(i)
        raw = option.get_attribute("data-automation-value") or ""
        if raw.strip() == value:
            return option
    return None


def check_radio(scope: Locator, value: str) -> None:
    """Marca la opción `value` dentro de `scope` (un radiogroup) y confirma
    que quedó seleccionada, haciendo clic en el elemento visible (no en el
    input nativo oculto, que Forms no siempre procesa igual)."""
    option = find_option(scope, value)
    if option is None:
        raise RuntimeError(f"No se encontró la opción '{value}' en esta pregunta.")

    input_loc = option.locator('input[role="radio"]').first

    for _ in range(4):
        if input_loc.count() and input_loc.get_attribute("aria-checked") == "true":
            return
        try:
            option.scroll_into_view_if_needed(timeout=3000)
            option.click(timeout=3000)
        except Exception:
            try:
                option.click(force=True, timeout=3000)
            except Exception:
                pass
        option.page.wait_for_timeout(250)

    if not input_loc.count() or input_loc.get_attribute("aria-checked") != "true":
        raise RuntimeError(f"No se pudo marcar la opción '{value}'. Revisa el formulario manualmente.")


def fill_date(page: Page) -> None:
    hoy = date.today()
    fecha_str = f"{hoy.month}/{hoy.day}/{hoy.year}"
    campo = page.locator('input[aria-label="Date picker"]')

    for _ in range(3):
        campo.click()
        campo.fill("")
        campo.type(fecha_str, delay=30)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        if campo.input_value().strip() == fecha_str:
            print(f"Fecha ingresada: {fecha_str}")
            return

    raise RuntimeError("No se pudo escribir la fecha en el campo FECHA.")


def has_submit_button(page: Page) -> bool:
    return page.locator('[data-automation-id="submitButton"]').count() > 0


def click_submit(page: Page) -> None:
    page.locator('[data-automation-id="submitButton"]').click()


def has_validation_error(page: Page) -> bool:
    return page.locator("text=need to be completed").count() > 0


def ordinal_number(item: Locator) -> int | None:
    ordinal = item.locator('[data-automation-id="questionOrdinal"]')
    if ordinal.count() == 0:
        return None
    digits = re.sub(r"\D", "", ordinal.first.inner_text())
    return int(digits) if digits else None


def answer_visible_questions(page: Page, no_questions: set[int], answered_log: set[int]) -> None:
    """Recorre todas las preguntas actualmente presentes en el DOM y
    contesta las que tengan pregunta numerada conocida y aún no tengan
    respuesta marcada."""
    items = page.locator('[data-automation-id="questionItem"]')
    total = items.count()

    for i in range(total):
        item = items.nth(i)
        numero = ordinal_number(item)
        if numero is None:
            continue

        group = item.locator('[role="radiogroup"]').first
        if group.count() == 0:
            continue  # no es una pregunta de opción única (p.ej. la FECHA)

        if group.locator('input[aria-checked="true"]').count() > 0:
            continue  # ya contestada

        if numero in RESPUESTAS_FIJAS:
            valor = RESPUESTAS_FIJAS[numero]
        elif numero >= FIRST_SI_NO_QUESTION:
            valor = "NO" if numero in no_questions else "SI"
        else:
            continue

        if find_option(group, valor) is None:
            continue  # la opción todavía no está montada; se reintentará en la próxima pasada

        check_radio(group, valor)
        if numero not in answered_log:
            print(f"  Pregunta {numero}: {valor}")
            answered_log.add(numero)


def ask_cuantos_formularios() -> int:
    while True:
        respuesta = input("¿Cuántos formularios necesitas llenar y enviar? ").strip()
        if respuesta.isdigit() and int(respuesta) > 0:
            return int(respuesta)
        print("Ingresa un número entero mayor que 0.")


def fill_one_form(page: Page) -> int:
    """Llena un formulario completo (fecha, ubicación y las 34 preguntas
    SI/NO) y lo deja listo justo antes del botón Submit. Devuelve cuántas
    preguntas respondió."""
    no_questions = pick_no_questions()
    answered_log: set[int] = set()

    page.wait_for_selector('input[aria-label="Date picker"]', timeout=20000)
    page.wait_for_timeout(500)
    fill_date(page)

    while True:
        for _ in range(PASADAS_POR_HOJA):
            answer_visible_questions(page, no_questions, answered_log)
            page.wait_for_timeout(250)

        if has_submit_button(page):
            answer_visible_questions(page, no_questions, answered_log)
            break

        page.locator('[data-automation-id="nextButton"]').click()
        page.wait_for_timeout(1000)

        if has_validation_error(page):
            print("  Forms bloqueó el avance: quedaba algo sin responder en esta página. Reintentando...")
            continue

        page.wait_for_selector('[data-automation-id="questionItem"]', state="visible", timeout=15000)
        page.wait_for_timeout(500)

    return len(answered_log)


def main() -> None:
    total = ask_cuantos_formularios()
    enviados = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)

        for numero_formulario in range(1, total + 1):
            print(f"\n=== Formulario {numero_formulario} de {total} ===")
            # Contexto nuevo por formulario: Microsoft Forms guarda el
            # progreso en curso en cookies/localStorage, así que reusar la
            # misma pestaña para el siguiente formulario lo hace reanudar
            # el formulario anterior en vez de empezar uno en blanco. Un
            # contexto aislado por intento evita ese arrastre de estado.
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(FORM_URL)
                respondidas = fill_one_form(page)
                print(f"Preguntas respondidas: {respondidas}")

                click_submit(page)
                page.wait_for_timeout(2000)
                enviados += 1
                print(f"Formulario {numero_formulario} enviado correctamente.")
            except Exception as exc:
                print(f"ERROR en el formulario {numero_formulario}, se omite y se continúa: {exc}")
            finally:
                context.close()

            if numero_formulario < total:
                time.sleep(1.5)  # pausa breve antes de abrir el siguiente formulario

        print(f"\nListo. {enviados} de {total} formularios enviados correctamente.")
        browser.close()


if __name__ == "__main__":
    main()
