"""Crea o actualiza una cuenta de usuario (nombre + contraseña + rol)
directo en la base de datos.

Se usa la primera vez para crear el Admin inicial, antes de que exista
ningún login en el sistema. Después de eso, los usuarios se administran
desde la propia app en Admin -> Usuarios; este script solo sirve como
respaldo si algún día hace falta resetear una cuenta desde la terminal.

Corre este script tú mismo (no se lo pidas a un asistente ni lo compartas)
para que tu contraseña real nunca quede escrita en ningún otro lado.

Uso:
    python scripts/crear_usuario.py
"""
from __future__ import annotations

import getpass
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "instance" / "activos.db"


def main() -> None:
    if not DB_PATH.exists():
        print("No existe instance/activos.db todavía. Corre primero importar_catalogo.py y migrar_login_roles.py.")
        return

    nombre = input("Nombre de usuario: ").strip()
    if not nombre:
        print("El nombre no puede estar vacío.")
        return

    rol = ""
    while rol not in ("admin", "operador"):
        rol = input("Rol (admin/operador): ").strip().lower()

    password = getpass.getpass("Contraseña: ")
    if len(password) < 4:
        print("Usa al menos 4 caracteres.")
        return
    confirmar = getpass.getpass("Confirma la contraseña: ")
    if password != confirmar:
        print("Las contraseñas no coinciden.")
        return

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        """INSERT INTO usuarios (nombre, password_hash, rol, activo) VALUES (?, ?, ?, 1)
           ON CONFLICT(nombre) DO UPDATE SET
               password_hash = excluded.password_hash,
               rol = excluded.rol,
               activo = 1""",
        (nombre, generate_password_hash(password), rol),
    )
    con.commit()
    con.close()
    print(f"Listo: '{nombre}' ({rol}) puede iniciar sesión.")


if __name__ == "__main__":
    main()
