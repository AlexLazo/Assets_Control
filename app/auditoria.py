"""Registro de auditoría: quién hizo qué y cuándo.

Se llama desde cada acción que cambia algo importante (usuarios, inventario,
cierres, mantenimiento, datos...). `registrar` NO hace commit: la fila viaja en
la misma transacción que la acción, así que si la acción se revierte, su
registro también -- nunca queda una huella de algo que no pasó.
"""
from __future__ import annotations

from flask_login import current_user

# código -> texto que ve el usuario (también alimenta el filtro de la bitácora)
ACCIONES = {
    "login": "Inicio de sesión",
    "login_fallido": "Intento de acceso fallido",
    "password_propia": "Cambió su propia contraseña",
    "usuario_crear": "Usuario creado",
    "usuario_editar": "Usuario editado",
    "usuario_activar": "Usuario activado",
    "usuario_desactivar": "Usuario desactivado",
    "usuario_rol": "Cambio de rol",
    "usuario_reset_password": "Contraseña reseteada",
    "usuario_eliminar": "Usuario eliminado",
    "equipo_crear": "Equipo creado",
    "equipo_editar": "Equipo editado",
    "equipo_eliminar": "Equipo eliminado",
    "ruta_crear": "Ruta creada",
    "ruta_editar": "Ruta editada",
    "ruta_estado": "Ruta activada/desactivada",
    "ruta_eliminar": "Ruta eliminada",
    "mantenimiento_enviar": "Enviado a mantenimiento",
    "mantenimiento_regreso": "Regresó de mantenimiento",
    "dia_cerrar": "Cierre de día",
    "dia_reiniciar": "Reinicio de día",
    "incidencia_resolver": "Incidencia resuelta a mano",
    "conteo_iniciar": "Conteo iniciado",
    "conteo_cerrar": "Conteo cerrado",
    "conteo_ajustar": "Ajuste por conteo",
    "datos_subir_excel": "Excel subido",
    "datos_catalogo": "Catálogo actualizado",
    "datos_respaldo": "Respaldo manual descargado",
    "datos_restaurar": "Base restaurada",
    "datos_reiniciar_bd": "Base reiniciada",
}

_MAX = 2000


def _texto(valor) -> str | None:
    if valor is None or valor == "":
        return None
    return str(valor)[:_MAX]


def registrar(db, accion: str, objetivo=None, detalle=None, *, usuario_id=None, usuario_nombre=None) -> None:
    """Anota una acción. Por defecto la persona es el usuario con sesión iniciada."""
    if usuario_id is None and usuario_nombre is None and current_user and current_user.is_authenticated:
        usuario_id, usuario_nombre = current_user.id, current_user.nombre
    db.execute(
        "INSERT INTO auditoria (usuario_id, usuario_nombre, accion, objetivo, detalle) VALUES (?, ?, ?, ?, ?)",
        (usuario_id, usuario_nombre, accion, _texto(objetivo), _texto(detalle)),
    )


def cambios(antes, despues, campos: dict[str, str]) -> str:
    """Texto 'Modelo: A → B; Notas: ... → ...' solo con lo que realmente cambió.
    `campos` mapea columna -> etiqueta."""
    partes = []
    for col, etiqueta in campos.items():
        a, d = antes[col], despues[col]
        if (a or None) != (d or None):
            partes.append(f"{etiqueta}: {a if a not in (None, '') else '(vacío)'} → {d if d not in (None, '') else '(vacío)'}")
    return "; ".join(partes)
