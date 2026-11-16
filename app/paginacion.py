"""Paginación sencilla para listados largos (Bitácora, Inventario)."""
from __future__ import annotations

from flask import request, url_for

POR_PAGINA = 100


def paginar(db, sql_total: str, sql_filas: str, parametros: list, por_pagina: int = POR_PAGINA):
    """Devuelve (filas, pag). `sql_filas` va sin LIMIT; la página sale de ?pagina=
    (un valor inválido o fuera de rango se corrige solo). Los enlaces conservan
    todos los demás filtros de la URL."""
    total = db.execute(sql_total, parametros).fetchone()[0]
    paginas = max(1, -(-total // por_pagina))
    pagina = min(max(request.args.get("pagina", 1, type=int), 1), paginas)
    filas = db.execute(f"{sql_filas} LIMIT ? OFFSET ?", [*parametros, por_pagina, (pagina - 1) * por_pagina]).fetchall()

    def enlace(n: int) -> str:
        return url_for(request.endpoint, **{**request.args.to_dict(), "pagina": n})

    return filas, {
        "total": total, "pagina": pagina, "paginas": paginas,
        "desde": (pagina - 1) * por_pagina + 1 if total else 0,
        "hasta": min(pagina * por_pagina, total),
        "anterior": enlace(pagina - 1) if pagina > 1 else None,
        "siguiente": enlace(pagina + 1) if pagina < paginas else None,
    }
