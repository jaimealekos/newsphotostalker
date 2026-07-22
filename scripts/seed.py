"""Siembra las búsquedas de ejemplo (una por agencia con navegador/HTTP).

Idempotente: no duplica una búsqueda con el mismo (agencia, tipo, consulta).

Uso:  python -m scripts.seed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import init_db, session_scope  # noqa: E402
from app.models import RETENTION_TIME, Search, User  # noqa: E402

# Tres ejemplos que muestran los tres caminos de ingesta:
#   * Reuters  — navegador con sesión (fotógrafo)
#   * Getty    — HTML por HTTP (fotógrafo; nombre de artista exacto)
#   * AP       — API JSON anónima (búsqueda de texto; "APTOPIX" = mejores fotos)
SEEDS = [
    ("Reuters · Alejandro Martínez Vélez", "reuters", "photographer", "Alejandro Martínez Vélez"),
    ("Getty · Pablo Blázquez Domínguez", "getty", "photographer", "Pablo Blázquez Domínguez"),
    ("AP · APTOPIX", "ap", "text", "APTOPIX"),
]


def main() -> None:
    init_db()
    created = 0
    with session_scope() as session:
        admin = session.scalar(select(User).where(User.is_admin.is_(True)))
        for name, agency, kind, query in SEEDS:
            exists = session.scalar(
                select(Search).where(
                    Search.agency == agency, Search.kind == kind, Search.query == query
                )
            )
            if exists:
                print(f"= ya existe: {name}")
                continue
            session.add(
                Search(
                    user_id=admin.id if admin else None,
                    name=name,
                    agency=agency,
                    kind=kind,
                    query=query,
                    cadence_minutes=360,
                    retention_mode=RETENTION_TIME,
                    retention_months=3,
                    retention_mb=None,
                    enabled=True,
                )
            )
            created += 1
            print(f"+ creada: {name}")
    print(f"\nListo. {created} búsqueda(s) nueva(s).")


if __name__ == "__main__":
    main()
