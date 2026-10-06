import secrets
import sys
from pathlib import Path

from flask import Flask, redirect, url_for
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


def _obtener_o_crear_secret_key(instance_path: Path) -> str:
    """Clave para firmar la cookie de sesión (identifica qué operador está
    activo en cada navegador). Se genera una sola vez y se guarda en disco
    para que no cambie entre reinicios del servidor -- si cambiara, todas
    las estaciones tendrían que volver a elegir operador cada vez que se
    reinicia la app."""
    archivo = instance_path / "secret_key.txt"
    if archivo.exists():
        return archivo.read_text(encoding="utf-8").strip()
    clave = secrets.token_hex(32)
    archivo.write_text(clave, encoding="utf-8")
    return clave


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=_obtener_o_crear_secret_key(instance_path),
        DATABASE=str(instance_path / "activos.db"),
    )

    db_module.init_app(app)
    login_manager.init_app(app)

    from . import auth
    login_manager.user_loader(auth.cargar_usuario)

    from . import escaneo, dashboard, bitacora, inventario, usuarios, etiquetas, datos, historial, mantenimiento, conteo, pendientes

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

    @app.route("/")
    def index():
        return redirect(url_for("escaneo.salida"))

    return app
