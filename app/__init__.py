import os
import secrets
import sys
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, send_from_directory, url_for
from flask_login import LoginManager

from . import db as db_module

# Los scripts de scripts/ (comun.py, importar_catalogo.py, generar_etiquetas.py)
# no son un paquete Python formal -- se agregan al path una sola vez aquí para
# que los blueprints (datos.py, etiquetas.py) puedan reutilizar exactamente la
# misma lógica de reconciliación/importación/QR que ya corre por línea de
# comandos, en vez de duplicarla.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Inicia sesión para continuar."


def _en_produccion() -> bool:
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PRODUCCION"))


def _directorio_datos(app: Flask, produccion: bool) -> tuple[Path, bool]:
    """Dónde viven la base, los respaldos y la llave de sesión, y si ese
    lugar es PERSISTENTE.

    En Railway TIENE que ser un volumen (RAILWAY_VOLUME_MOUNT_PATH o
    DATA_DIR): el disco normal del contenedor se borra en cada deploy y con él
    la base. Si estamos en producción y no hay volumen, la app NO arranca (el
    deploy falla a la vista y Railway deja corriendo la versión anterior) en
    vez de seguir funcionando y perder los datos en silencio."""
    configurado = os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    # En Railway solo cuenta como volumen real el que Railway mismo declara
    # (RAILWAY_VOLUME_MOUNT_PATH): un DATA_DIR puesto a mano apuntaría al disco
    # temporal y la protección quedaría engañada.
    if os.environ.get("RAILWAY_ENVIRONMENT") and not os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"):
        configurado = None
    if produccion and not configurado and os.environ.get("PERMITIR_SIN_VOLUMEN") != "1":
        raise RuntimeError(
            "PRODUCCIÓN SIN VOLUMEN PERSISTENTE: la base de datos se borraría en cada deploy. "
            "En Railway agrega un Volume al servicio (Settings -> Volumes, mount path /data). "
            "Railway define RAILWAY_VOLUME_MOUNT_PATH solo cuando el volumen está conectado a ESTE servicio."
        )
    carpeta = Path(configurado) if configurado else Path(app.instance_path)
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta, bool(configurado)


def _obtener_o_crear_secret_key(carpeta: Path) -> str:
    """Clave para firmar la cookie de sesión. Prioridad: variable SECRET_KEY;
    si no existe, un archivo en la carpeta de datos (se genera una sola vez,
    así las sesiones no se pierden entre reinicios)."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    archivo = carpeta / "secret_key.txt"
    if archivo.exists():
        return archivo.read_text(encoding="utf-8").strip()
    clave = secrets.token_hex(32)
    archivo.write_text(clave, encoding="utf-8")
    return clave


def create_app() -> Flask:
    # Las fechas de la bitácora usan datetime('now','localtime'): en un
    # servidor en la nube la hora local sería UTC y los cierres de día se
    # correrían. Se fija la zona de El Salvador salvo que se indique otra.
    os.environ.setdefault("TZ", "America/El_Salvador")
    if hasattr(time, "tzset"):
        time.tzset()

    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    produccion = _en_produccion()
    carpeta, persistente = _directorio_datos(app, produccion)

    app.config.from_mapping(
        SECRET_KEY=_obtener_o_crear_secret_key(carpeta),
        DATA_DIR=str(carpeta),
        DATABASE=str(carpeta / "activos.db"),
        PRODUCCION=produccion,
        PERSISTENTE=persistente,
        # El botón "Reiniciar base de datos" borra todo: en producción queda
        # apagado salvo que se active a propósito con PERMITIR_REINICIO=1.
        PERMITIR_REINICIO=os.environ.get("PERMITIR_REINICIO", "0" if produccion else "1") == "1",
        MAX_CONTENT_LENGTH=100 * 1024 * 1024,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=produccion,
        REMEMBER_COOKIE_SECURE=produccion,
        REMEMBER_COOKIE_HTTPONLY=True,
        # La sesión caduca tras 12 h sin usar la app (se renueva con cada
        # petición): alcanza para un turno completo y no queda abierta días en
        # un teléfono perdido o una PC compartida.
        REMEMBER_COOKIE_DURATION=timedelta(hours=12),
        REMEMBER_COOKIE_REFRESH_EACH_REQUEST=True,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    if produccion:
        # Railway termina el HTTPS y reenvía por HTTP interno: sin esto Flask
        # cree que está en http y arma mal los enlaces y las cookies seguras.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from . import migraciones, respaldos

    migraciones.actualizar(app.config["DATABASE"])
    migraciones.asegurar_admin(app.config["DATABASE"])
    import sqlite3 as _sqlite

    _con = _sqlite.connect(app.config["DATABASE"])
    _equipos = _con.execute("SELECT COUNT(*) FROM equipos").fetchone()[0]
    _con.close()
    print(f"[arranque] datos en {carpeta} | volumen persistente: {'SI' if persistente else 'NO (solo desarrollo local)'} | equipos en la base: {_equipos}")
    respaldos.iniciar(app)

    db_module.init_app(app)
    login_manager.init_app(app)

    from . import auth
    login_manager.user_loader(auth.cargar_usuario)
    app.context_processor(lambda: {"puede": auth.puede, "ROLES": auth.ROLES})

    from . import escaneo, dashboard, bitacora, inventario, usuarios, etiquetas, datos, historial, mantenimiento, conteo, pendientes, reinicio_dia

    app.register_blueprint(auth.bp)
    app.register_blueprint(escaneo.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(bitacora.bp)
    app.register_blueprint(inventario.bp)
    app.register_blueprint(usuarios.bp)
    app.register_blueprint(etiquetas.bp)
    app.register_blueprint(datos.bp)
    app.register_blueprint(historial.bp)
    app.register_blueprint(mantenimiento.bp)
    app.register_blueprint(conteo.bp)
    app.register_blueprint(pendientes.bp)
    app.register_blueprint(reinicio_dia.bp)

    @app.route("/")
    def index():
        return redirect(url_for("escaneo.salida"))

    @app.route("/salud")
    def salud():
        # Sin login: lo usa Railway para saber si la app arrancó bien.
        from .db import get_db

        get_db().execute("SELECT 1").fetchone()
        return "ok"

    # --- App instalable (PWA): el service worker debe servirse desde la raíz
    # para poder controlar todo el sitio.
    estaticos = Path(app.root_path) / "static"

    @app.route("/sw.js")
    def service_worker():
        r = send_from_directory(estaticos, "sw.js", mimetype="application/javascript")
        r.headers["Service-Worker-Allowed"] = "/"
        r.headers["Cache-Control"] = "no-cache"
        return r

    @app.route("/manifest.webmanifest")
    def manifiesto():
        return send_from_directory(estaticos, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.after_request
    def cabeceras_de_seguridad(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if produccion:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
        return resp

    return app
