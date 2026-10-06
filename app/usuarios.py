import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.security import generate_password_hash

from .auth import requiere_admin
from .db import get_db

bp = Blueprint("usuarios", __name__, url_prefix="/admin/usuarios")


@bp.route("/")
@requiere_admin
def listado():
    db = get_db()
    lista = db.execute("SELECT * FROM usuarios ORDER BY activo DESC, nombre").fetchall()
    return render_template("usuarios/listado.html", usuarios=lista)


@bp.route("/nuevo", methods=["GET", "POST"])
@requiere_admin
def nuevo():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        rol = request.form.get("rol")
        password = request.form.get("password", "")
        confirmar = request.form.get("confirmar", "")

        db = get_db()
        error = None
        if not nombre:
            error = "El nombre no puede estar vacío."
        elif rol not in ("admin", "operador"):
            error = "Selecciona un rol válido."
        elif len(password) < 4:
            error = "La contraseña debe tener al menos 4 caracteres."
        elif password != confirmar:
            error = "Las contraseñas no coinciden."
        elif db.execute("SELECT id FROM usuarios WHERE nombre = ?", (nombre,)).fetchone():
            error = f"Ya existe un usuario llamado '{nombre}'."

        if error:
            flash(error, "error")
            return render_template("usuarios/nuevo.html", nombre=nombre, rol=rol)

        db.execute(
            "INSERT INTO usuarios (nombre, password_hash, rol) VALUES (?, ?, ?)",
            (nombre, generate_password_hash(password), rol),
        )
        db.commit()
        flash(f"Usuario '{nombre}' creado.")
        return redirect(url_for("usuarios.listado"))

    return render_template("usuarios/nuevo.html", nombre="", rol="operador")


@bp.route("/<int:usuario_id>/desactivar", methods=["POST"])
@requiere_admin
def desactivar(usuario_id):
    nuevo_activo = request.form.get("nuevo_activo", type=int)
    if usuario_id == current_user.id and not nuevo_activo:
        flash("No puedes desactivar tu propia cuenta mientras tienes la sesión iniciada.", "error")
        return redirect(url_for("usuarios.listado"))
    db = get_db()
    db.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_activo, usuario_id))
    db.commit()
    return redirect(url_for("usuarios.listado"))


@bp.route("/<int:usuario_id>/cambiar-rol", methods=["POST"])
@requiere_admin
def cambiar_rol(usuario_id):
    nuevo_rol = request.form.get("nuevo_rol")
    if nuevo_rol not in ("admin", "operador"):
        flash("Rol no válido.", "error")
        return redirect(url_for("usuarios.listado"))
    if usuario_id == current_user.id and nuevo_rol != "admin":
        flash("No puedes quitarte el rol de Admin a ti mismo mientras tienes la sesión iniciada.", "error")
        return redirect(url_for("usuarios.listado"))
    db = get_db()
    db.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (nuevo_rol, usuario_id))
    db.commit()
    flash("Rol actualizado.")
    return redirect(url_for("usuarios.listado"))


@bp.route("/<int:usuario_id>/eliminar", methods=["POST"])
@requiere_admin
def eliminar(usuario_id):
    if usuario_id == current_user.id:
        flash("No puedes eliminar tu propia cuenta mientras tienes la sesión iniciada.", "error")
        return redirect(url_for("usuarios.listado"))
    db = get_db()
    usuario = db.execute("SELECT nombre FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    if usuario is None:
        flash("Ese usuario no existe.", "error")
        return redirect(url_for("usuarios.listado"))
    try:
        db.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
        db.commit()
        flash(f"Usuario '{usuario['nombre']}' eliminado.")
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"No se puede eliminar '{usuario['nombre']}': tiene movimientos o cierres de día registrados. Desactívalo en su lugar.", "error")
    return redirect(url_for("usuarios.listado"))


@bp.route("/<int:usuario_id>/resetear-password", methods=["GET", "POST"])
@requiere_admin
def resetear_password(usuario_id):
    db = get_db()
    usuario = db.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    if usuario is None:
        flash("Ese usuario no existe.", "error")
        return redirect(url_for("usuarios.listado"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirmar = request.form.get("confirmar", "")
        if len(password) < 4:
            flash("La contraseña debe tener al menos 4 caracteres.", "error")
        elif password != confirmar:
            flash("Las contraseñas no coinciden.", "error")
        else:
            db.execute(
                "UPDATE usuarios SET password_hash = ? WHERE id = ?",
                (generate_password_hash(password), usuario_id),
            )
            db.commit()
            flash(f"Contraseña de '{usuario['nombre']}' actualizada.")
            return redirect(url_for("usuarios.listado"))

    return render_template("usuarios/resetear_password.html", usuario=usuario)
