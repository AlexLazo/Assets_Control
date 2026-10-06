from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)


class Usuario(UserMixin):
    def __init__(self, id_: int, nombre: str, rol: str):
        self.id = id_
        self.nombre = nombre
        self.rol = rol

    def get_id(self) -> str:
        return str(self.id)


def cargar_usuario(usuario_id: str):
    fila = get_db().execute(
        "SELECT id, nombre, rol FROM usuarios WHERE id = ? AND activo = 1", (usuario_id,)
    ).fetchone()
    return Usuario(fila["id"], fila["nombre"], fila["rol"]) if fila else None


def requiere_admin(vista):
    @wraps(vista)
    @login_required
    def envoltura(*args, **kwargs):
        if current_user.rol != "admin":
            abort(403)
        return vista(*args, **kwargs)

    return envoltura


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        password = request.form.get("password", "")

        fila = get_db().execute(
            "SELECT id, nombre, rol, activo, password_hash FROM usuarios WHERE nombre = ?", (nombre,)
        ).fetchone()

        if fila is None or not check_password_hash(fila["password_hash"], password):
            flash("Usuario o contraseña incorrectos.", "error")
            return redirect(url_for("auth.login"))

        if not fila["activo"]:
            flash("Tu cuenta está desactivada. Contacta al administrador.", "error")
            return redirect(url_for("auth.login"))

        login_user(Usuario(fila["id"], fila["nombre"], fila["rol"]), remember=True)
        flash(f"Sesión iniciada como {fila['nombre']}.")
        destino = request.args.get("next") or url_for("escaneo.salida")
        return redirect(destino)

    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.")
    return redirect(url_for("auth.login"))
