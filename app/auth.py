import re
import time
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import auditoria
from .db import get_db

bp = Blueprint("auth", __name__)

# Reglas únicas de contraseña y de usuario de login: las usan Mi cuenta,
# Usuarios (crear/resetear) y el script de línea de comandos.
MIN_PASSWORD = 8
USUARIO_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
USUARIO_AYUDA = "3 a 30 caracteres: letras sin tilde, números, punto, guion o guion bajo (sin espacios)."


def error_password(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres."
    return None

ROLES = {
    "supervisor": "Supervisor",
    "facturador": "Facturador",
    "admin": "Admin",
    "super_admin": "Super Admin",
}
TODOS = set(ROLES)
BASICOS = {"supervisor", "facturador"}
ADMINS = {"admin", "super_admin"}

# Qué rol puede qué. Para cambiar quién ve o hace algo, se edita SOLO aquí.
PERMISOS = {
    # Operación diaria: todos los roles
    "escanear": TODOS,                  # Salida y Retorno
    "dashboard": TODOS,
    "pendientes": TODOS,
    "mantenimiento": TODOS,
    "conteo_escanear": TODOS,
    "inventario_ver": TODOS,            # solo lectura
    # Gestión: Admin y Super Admin
    "inventario_editar": ADMINS,
    "bitacora": ADMINS,                 # incluye exportar a Excel
    "historial": ADMINS,
    "etiquetas": ADMINS,
    "conteo_gestionar": ADMINS,         # iniciar, cerrar y ajustar un conteo
    "cerrar_dia": ADMINS,
    "usuarios": ADMINS,                 # un Admin solo gestiona Supervisores y Facturadores
    # Solo Super Admin
    "datos": {"super_admin"},           # respaldos, restaurar, reiniciar, importar Excel
}


class Usuario(UserMixin):
    def __init__(self, id_: int, nombre: str, rol: str, usuario: str | None = None, debe_cambiar: bool = False):
        self.id = id_
        self.nombre = nombre      # nombre para mostrar (bitácora, reportes)
        self.rol = rol
        self.usuario = usuario    # nombre corto de login
        self.debe_cambiar = debe_cambiar

    def get_id(self) -> str:
        return str(self.id)


def cargar_usuario(usuario_id: str):
    fila = get_db().execute(
        "SELECT id, nombre, rol, usuario, debe_cambiar_password FROM usuarios WHERE id = ? AND activo = 1", (usuario_id,)
    ).fetchone()
    if not fila:
        return None
    return Usuario(fila["id"], fila["nombre"], fila["rol"], fila["usuario"], bool(fila["debe_cambiar_password"]))


def puede(permiso: str) -> bool:
    return current_user.is_authenticated and current_user.rol in PERMISOS[permiso]


def requiere_permiso(permiso: str):
    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(*args, **kwargs):
            if current_user.rol not in PERMISOS[permiso]:
                abort(403)
            return vista(*args, **kwargs)

        return envoltura

    return decorador


# Compatibilidad: "admin" a secas = Admin o Super Admin.
requiere_admin = requiere_permiso("inventario_editar")
requiere_super_admin = requiere_permiso("datos")


# --------------------------------------------------------- límite de intentos
# En memoria (la app corre en un solo proceso): tras MAX_FALLOS contraseñas
# incorrectas seguidas desde una misma IP o para un mismo usuario, se bloquea
# ese intento durante BLOQUEO_SEGUNDOS. Frena adivinar contraseñas sin
# necesidad de más infraestructura.
MAX_FALLOS = 5          # por usuario
MAX_FALLOS_IP = 30      # por IP: alto a propósito, toda una bodega suele salir con la misma IP
VENTANA_SEGUNDOS = 15 * 60
BLOQUEO_SEGUNDOS = 10 * 60
_fallos: dict[str, list[float]] = {}
_bloqueados: dict[str, float] = {}


def _claves(nombre: str) -> list[str]:
    return [f"ip:{request.remote_addr}", f"usuario:{nombre.lower()}"]


def _segundos_bloqueado(nombre: str) -> int:
    ahora = time.time()
    restante = 0
    for k in _claves(nombre):
        hasta = _bloqueados.get(k, 0)
        if hasta > ahora:
            restante = max(restante, int(hasta - ahora) + 1)
        elif k in _bloqueados:
            del _bloqueados[k]
    return restante


def _registrar_fallo(nombre: str) -> None:
    ahora = time.time()
    for k in _claves(nombre):
        recientes = [t for t in _fallos.get(k, []) if ahora - t < VENTANA_SEGUNDOS] + [ahora]
        _fallos[k] = recientes
        limite = MAX_FALLOS_IP if k.startswith("ip:") else MAX_FALLOS
        if len(recientes) >= limite:
            _bloqueados[k] = ahora + BLOQUEO_SEGUNDOS
            _fallos[k] = []


def _limpiar_fallos(nombre: str) -> None:
    for k in _claves(nombre):
        _fallos.pop(k, None)


def _destino_seguro(destino: str | None) -> str:
    """Solo se acepta una ruta interna: evita que un enlace con ?next= mande
    al usuario a otro sitio después de iniciar sesión."""
    if destino and destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return destino
    return url_for("escaneo.salida")


def _buscar_para_login(db, texto: str):
    """Acepta el usuario corto (AlexLz) sin importar mayúsculas. Por
    compatibilidad también acepta el nombre completo con el que la gente ya
    entraba; el usuario corto siempre tiene prioridad."""
    if not texto:
        return None
    columnas = "id, nombre, usuario, rol, activo, password_hash, debe_cambiar_password"
    fila = db.execute(f"SELECT {columnas} FROM usuarios WHERE usuario = ? COLLATE NOCASE", (texto,)).fetchone()
    if fila is None:
        fila = db.execute(f"SELECT {columnas} FROM usuarios WHERE nombre = ? COLLATE NOCASE ORDER BY id LIMIT 1", (texto,)).fetchone()
    return fila


@bp.before_app_request
def forzar_cambio_de_password():
    """Quien tiene una contraseña temporal (cuenta nueva o reseteada por un
    Admin) solo puede ir a 'Mi cuenta' o salir, hasta que elija la suya."""
    if request.endpoint in (None, "static", "auth.cuenta", "auth.logout", "auth.login", "service_worker", "manifiesto", "salud"):
        return None
    if current_user.is_authenticated and getattr(current_user, "debe_cambiar", False):
        return redirect(url_for("auth.cuenta"))
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        password = request.form.get("password", "")

        espera = _segundos_bloqueado(nombre)
        if espera:
            flash(f"Demasiados intentos fallidos. Espera {-(-espera // 60)} minuto(s) e inténtalo de nuevo.", "error")
            return redirect(url_for("auth.login"))

        db = get_db()
        fila = _buscar_para_login(db, nombre)

        if fila is None or not check_password_hash(fila["password_hash"], password):
            _registrar_fallo(nombre)
            if fila is not None:
                # Solo se anota el intento cuando el usuario existe: así nunca
                # se guarda lo que alguien escribió por error en ese campo.
                auditoria.registrar(
                    db, "login_fallido", fila["usuario"] or fila["nombre"], "Contraseña incorrecta",
                    usuario_id=fila["id"], usuario_nombre=fila["nombre"],
                )
                db.commit()
            flash("Usuario o contraseña incorrectos.", "error")
            return redirect(url_for("auth.login"))

        if not fila["activo"]:
            flash("Tu cuenta está desactivada. Contacta al administrador.", "error")
            return redirect(url_for("auth.login"))

        _limpiar_fallos(nombre)
        login_user(
            Usuario(fila["id"], fila["nombre"], fila["rol"], fila["usuario"], bool(fila["debe_cambiar_password"])),
            remember=True,
        )
        auditoria.registrar(db, "login", fila["usuario"] or fila["nombre"], usuario_id=fila["id"], usuario_nombre=fila["nombre"])
        db.commit()
        if fila["debe_cambiar_password"]:
            flash("Por seguridad, elige tu propia contraseña antes de continuar.")
            return redirect(url_for("auth.cuenta"))
        flash(f"Sesión iniciada como {fila['nombre']}.")
        return redirect(_destino_seguro(request.args.get("next")))

    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.")
    return redirect(url_for("auth.login"))


@bp.route("/cuenta", methods=["GET", "POST"])
@login_required
def cuenta():
    """Cada usuario cambia su propia contraseña."""
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        confirmar = request.form.get("confirmar", "")
        db = get_db()
        fila = db.execute("SELECT password_hash FROM usuarios WHERE id = ?", (current_user.id,)).fetchone()

        if not check_password_hash(fila["password_hash"], actual):
            flash("La contraseña actual no es correcta.", "error")
        elif error_password(nueva):
            flash(error_password(nueva), "error")
        elif nueva != confirmar:
            flash("La contraseña nueva y su confirmación no coinciden.", "error")
        elif nueva == actual:
            flash("La contraseña nueva debe ser distinta de la actual.", "error")
        else:
            db.execute(
                "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 0 WHERE id = ?",
                (generate_password_hash(nueva), current_user.id),
            )
            auditoria.registrar(db, "password_propia", current_user.usuario or current_user.nombre)
            db.commit()
            flash("Contraseña actualizada.")
            return redirect(url_for("escaneo.salida") if current_user.debe_cambiar else url_for("auth.cuenta"))
    return render_template("auth/cuenta.html")
