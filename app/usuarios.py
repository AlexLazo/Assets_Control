import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.security import generate_password_hash

from . import auditoria
from .auth import BASICOS, ROLES, USUARIO_AYUDA, USUARIO_RE, error_password, requiere_permiso
from .db import get_db

bp = Blueprint("usuarios", __name__, url_prefix="/admin/usuarios")


def _roles_asignables() -> list[str]:
    """Super Admin crea cualquier rol; un Admin solo Supervisor y Facturador."""
    return list(ROLES) if current_user.rol == "super_admin" else sorted(BASICOS)


def _puede_gestionar(rol_objetivo: str) -> bool:
    return current_user.rol == "super_admin" or rol_objetivo in BASICOS


def _es_ultimo_super(db, usuario_id: int) -> bool:
    fila = db.execute("SELECT rol, activo FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    if not fila or fila["rol"] != "super_admin" or not fila["activo"]:
        return False
    otros = db.execute(
        "SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'super_admin' AND activo = 1 AND id != ?", (usuario_id,)
    ).fetchone()["n"]
    return otros == 0


def _error_usuario(db, usuario: str, nombre: str, excepto_id: int | None = None) -> str | None:
    """Valida el par (usuario de login, nombre para mostrar). None = todo bien."""
    if not nombre:
        return "El nombre para mostrar no puede estar vacío."
    if not USUARIO_RE.match(usuario):
        return f"El usuario no es válido: {USUARIO_AYUDA}"
    otro = db.execute(
        "SELECT nombre FROM usuarios WHERE usuario = ? COLLATE NOCASE AND id IS NOT ?", (usuario, excepto_id)
    ).fetchone()
    if otro:
        return f"El usuario '{usuario}' ya lo usa {otro['nombre']}."
    if db.execute("SELECT 1 FROM usuarios WHERE nombre = ? AND id IS NOT ?", (nombre, excepto_id)).fetchone():
        return f"Ya existe una persona llamada '{nombre}'."
    return None


def _cargar_objetivo(db, usuario_id: int):
    u = db.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    if u is None:
        flash("Ese usuario no existe.", "error")
        return None
    if not _puede_gestionar(u["rol"]):
        abort(403)
    return u


@bp.route("/")
@requiere_permiso("usuarios")
def listado():
    db = get_db()
    filas = db.execute("SELECT * FROM usuarios ORDER BY activo DESC, nombre COLLATE NOCASE").fetchall()
    usuarios = [dict(f, gestionable=_puede_gestionar(f["rol"])) for f in filas]
    return render_template("usuarios/listado.html", usuarios=usuarios, roles_asignables=_roles_asignables())


@bp.route("/nuevo", methods=["GET", "POST"])
@requiere_permiso("usuarios")
def nuevo():
    asignables = _roles_asignables()
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        nombre = request.form.get("nombre", "").strip()
        rol = request.form.get("rol")
        password = request.form.get("password", "")
        confirmar = request.form.get("confirmar", "")

        db = get_db()
        error = _error_usuario(db, usuario, nombre)
        if not error and rol not in asignables:
            error = "Selecciona un rol válido (tu rol no puede crear ese tipo de usuario)."
        elif not error and error_password(password):
            error = error_password(password)
        elif not error and password != confirmar:
            error = "Las contraseñas no coinciden."

        if error:
            flash(error, "error")
            return render_template("usuarios/nuevo.html", usuario=usuario, nombre=nombre, rol=rol, roles_asignables=asignables)

        # Contraseña temporal: la persona debe elegir la suya al primer ingreso.
        db.execute(
            "INSERT INTO usuarios (nombre, usuario, password_hash, rol, debe_cambiar_password) VALUES (?, ?, ?, ?, 1)",
            (nombre, usuario, generate_password_hash(password), rol),
        )
        auditoria.registrar(db, "usuario_crear", f"{usuario} ({nombre})", f"Rol: {ROLES[rol]}")
        db.commit()
        flash(f"Usuario '{usuario}' creado como {ROLES[rol]}. Al entrar por primera vez deberá cambiar su contraseña.")
        return redirect(url_for("usuarios.listado"))

    return render_template("usuarios/nuevo.html", usuario="", nombre="", rol="supervisor", roles_asignables=asignables)


@bp.route("/<int:usuario_id>/editar", methods=["GET", "POST"])
@requiere_permiso("usuarios")
def editar(usuario_id):
    db = get_db()
    u = _cargar_objetivo(db, usuario_id)
    if u is None:
        return redirect(url_for("usuarios.listado"))

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        nombre = request.form.get("nombre", "").strip()
        error = _error_usuario(db, usuario, nombre, excepto_id=usuario_id)
        if error:
            flash(error, "error")
            return render_template("usuarios/editar.html", u=u, usuario=usuario, nombre=nombre)
        antes = {"usuario": u["usuario"], "nombre": u["nombre"]}
        despues = {"usuario": usuario, "nombre": nombre}
        cambio = auditoria.cambios(antes, despues, {"usuario": "Usuario", "nombre": "Nombre"})
        if cambio:
            db.execute("UPDATE usuarios SET usuario = ?, nombre = ? WHERE id = ?", (usuario, nombre, usuario_id))
            auditoria.registrar(db, "usuario_editar", f"{usuario} ({nombre})", cambio)
            db.commit()
            flash("Usuario actualizado. Los movimientos anteriores muestran ahora el nuevo nombre.")
        return redirect(url_for("usuarios.listado"))

    return render_template("usuarios/editar.html", u=u, usuario=u["usuario"] or "", nombre=u["nombre"])


@bp.route("/<int:usuario_id>/desactivar", methods=["POST"])
@requiere_permiso("usuarios")
def desactivar(usuario_id):
    db = get_db()
    u = _cargar_objetivo(db, usuario_id)
    if u is None:
        return redirect(url_for("usuarios.listado"))
    nuevo_activo = request.form.get("nuevo_activo", type=int)
    if not nuevo_activo:
        if usuario_id == current_user.id:
            flash("No puedes desactivar tu propia cuenta mientras tienes la sesión iniciada.", "error")
            return redirect(url_for("usuarios.listado"))
        if _es_ultimo_super(db, usuario_id):
            flash("No se puede desactivar al último Super Admin activo.", "error")
            return redirect(url_for("usuarios.listado"))
    db.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_activo, usuario_id))
    auditoria.registrar(db, "usuario_activar" if nuevo_activo else "usuario_desactivar", f"{u['usuario'] or u['nombre']} ({u['nombre']})")
    db.commit()
    return redirect(url_for("usuarios.listado"))


@bp.route("/<int:usuario_id>/cambiar-rol", methods=["POST"])
@requiere_permiso("usuarios")
def cambiar_rol(usuario_id):
    db = get_db()
    u = _cargar_objetivo(db, usuario_id)
    if u is None:
        return redirect(url_for("usuarios.listado"))
    nuevo_rol = request.form.get("nuevo_rol")
    if nuevo_rol not in _roles_asignables():
        flash("Rol no válido para tu nivel de acceso.", "error")
        return redirect(url_for("usuarios.listado"))
    if usuario_id == current_user.id:
        flash("No puedes cambiarte el rol a ti mismo.", "error")
        return redirect(url_for("usuarios.listado"))
    if _es_ultimo_super(db, usuario_id) and nuevo_rol != "super_admin":
        flash("No se puede quitar el rol al último Super Admin activo.", "error")
        return redirect(url_for("usuarios.listado"))
    db.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (nuevo_rol, usuario_id))
    auditoria.registrar(db, "usuario_rol", f"{u['usuario'] or u['nombre']} ({u['nombre']})", f"{ROLES[u['rol']]} → {ROLES[nuevo_rol]}")
    db.commit()
    flash("Rol actualizado.")
    return redirect(url_for("usuarios.listado"))


@bp.route("/<int:usuario_id>/eliminar", methods=["POST"])
@requiere_permiso("usuarios")
def eliminar(usuario_id):
    db = get_db()
    u = _cargar_objetivo(db, usuario_id)
    if u is None:
        return redirect(url_for("usuarios.listado"))
    if usuario_id == current_user.id:
        flash("No puedes eliminar tu propia cuenta mientras tienes la sesión iniciada.", "error")
        return redirect(url_for("usuarios.listado"))
    if _es_ultimo_super(db, usuario_id):
        flash("No se puede eliminar al último Super Admin activo.", "error")
        return redirect(url_for("usuarios.listado"))
    try:
        db.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
        auditoria.registrar(db, "usuario_eliminar", f"{u['usuario'] or u['nombre']} ({u['nombre']})", f"Rol: {ROLES[u['rol']]}")
        db.commit()
        flash(f"Usuario '{u['nombre']}' eliminado.")
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"No se puede eliminar '{u['nombre']}': tiene movimientos o cierres de día registrados. Desactívalo en su lugar.", "error")
    return redirect(url_for("usuarios.listado"))


@bp.route("/<int:usuario_id>/resetear-password", methods=["GET", "POST"])
@requiere_permiso("usuarios")
def resetear_password(usuario_id):
    db = get_db()
    usuario = _cargar_objetivo(db, usuario_id)
    if usuario is None:
        return redirect(url_for("usuarios.listado"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirmar = request.form.get("confirmar", "")
        if error_password(password):
            flash(error_password(password), "error")
        elif password != confirmar:
            flash("Las contraseñas no coinciden.", "error")
        else:
            # Temporal: la persona elige la suya al entrar (salvo que sea la propia).
            temporal = 0 if usuario_id == current_user.id else 1
            db.execute(
                "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = ? WHERE id = ?",
                (generate_password_hash(password), temporal, usuario_id),
            )
            auditoria.registrar(db, "usuario_reset_password", f"{usuario['usuario'] or usuario['nombre']} ({usuario['nombre']})")
            db.commit()
            flash(f"Contraseña de '{usuario['nombre']}' actualizada." + (" Deberá cambiarla al entrar." if temporal else ""))
            return redirect(url_for("usuarios.listado"))

    return render_template("usuarios/resetear_password.html", usuario=usuario)
