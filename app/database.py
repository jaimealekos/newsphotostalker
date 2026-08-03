"""SQLAlchemy engine + session setup."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import BASE_DIR, get_settings

log = logging.getLogger("database")


class Base(DeclarativeBase):
    pass


def _resolved_db_url() -> str:
    """Turn a relative sqlite path into an absolute one so the DB location is
    stable regardless of the process working directory."""
    url = get_settings().db_url
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if not path.startswith("/"):
            abs_path = (BASE_DIR / path).resolve()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"{prefix}{abs_path}"
    return url


engine = create_engine(
    _resolved_db_url(),
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """WAL + busy timeout so the background scheduler and web reads don't
    collide on 'database is locked'."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    # Import models so they register on the metadata before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _bootstrap()


def _bootstrap() -> None:
    """Migración ligera + estado mínimo.

    - Añade las columnas que falten en ``searches``: ``user_id`` (v1.0),
      ``position`` y ``seen_at`` (1.1).
    - Colapsa la base a **un solo usuario** (1.1): sobrevive el antiguo admin y
      las demás cuentas se borran con sus búsquedas y sus fotos.
    - Adopta las búsquedas sin dueño y les da un orden inicial.
    """
    from .auth import hash_password
    from .models import AppSettings, User

    with session_scope() as session:
        _migrate_searches(session)

        # Fila única de ajustes globales (refresco + fotos por página).
        if session.get(AppSettings, 1) is None:
            session.add(AppSettings(id=1))
            log.warning("Creada la fila de ajustes globales (id=1)")

        user = _collapse_to_single_user(session)
        if user is None:
            user = User(username="admin", password_hash=hash_password("admin"))
            session.add(user)
            session.flush()
            log.warning("Creado el usuario inicial admin/admin — cámbialo desde Ajustes")

        session.execute(
            text("UPDATE searches SET user_id = :uid WHERE user_id IS NULL"), {"uid": user.id}
        )
        _seed_positions(session, user.id)


def _migrate_searches(session: Session) -> None:
    """Añade a ``searches`` las columnas que falten en bases de datos antiguas."""
    cols = [row[1] for row in session.execute(text("PRAGMA table_info(searches)"))]
    for name, ddl in (
        ("user_id", "INTEGER"),
        ("position", "INTEGER NOT NULL DEFAULT 0"),
        ("seen_at", "DATETIME"),
    ):
        if name not in cols:
            session.execute(text(f"ALTER TABLE searches ADD COLUMN {name} {ddl}"))
            log.warning("Migración: añadida la columna searches.%s", name)
    # Al venir de la cookie global de "visto", se da todo por visto ahora mismo:
    # así ninguna luz se enciende de golpe en el primer arranque tras migrar.
    if "seen_at" not in cols:
        session.execute(
            text("UPDATE searches SET seen_at = :now"), {"now": datetime.now(timezone.utc)}
        )


def _collapse_to_single_user(session: Session):
    """Deja una sola cuenta y la devuelve (None si la base está vacía).

    Sobrevive el admin de la v1.0 (o, sin esa columna, la cuenta más antigua);
    las demás se borran con sus búsquedas y con sus fotos en disco.
    """
    from . import storage
    from .models import Asset, Search, User

    users = list(session.scalars(select(User).order_by(User.id)))
    if not users:
        return None

    cols = [row[1] for row in session.execute(text("PRAGMA table_info(users)"))]
    keep = users[0]
    if "is_admin" in cols:
        admin_id = session.scalar(
            text("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1")
        )
        keep = next((u for u in users if u.id == admin_id), keep)

    for user in users:
        if user.id == keep.id:
            continue
        paths = session.execute(
            select(Asset.preview_path, Asset.thumbnail_path)
            .join(Search, Asset.search_id == Search.id)
            .where(Search.user_id == user.id)
        ).all()
        for preview, thumb in paths:
            storage.remove_asset_files(preview, thumb)
        log.warning(
            "Migración a usuario único: borrada la cuenta %r y sus %s fotos",
            user.username,
            len(paths),
        )
        session.delete(user)  # arrastra búsquedas, fotos y separadores
    session.flush()

    # La columna is_admin ya no la mira nadie; fuera si el SQLite lo permite.
    if "is_admin" in cols:
        try:
            session.execute(text("ALTER TABLE users DROP COLUMN is_admin"))
        except Exception:  # noqa: BLE001 - SQLite < 3.35: se queda inerte
            log.info("No se pudo eliminar users.is_admin; se queda sin usar")
    return keep


def _seed_positions(session: Session, user_id: int) -> None:
    """Da orden inicial al panel si aún no lo tiene.

    Se respeta el orden que mostraba antes del modo edición (agencia y luego
    nombre), para que la primera vista sea idéntica a la de siempre.
    """
    from .models import Search, Separator

    ordered = session.scalar(
        select(func.count(Search.id)).where(Search.user_id == user_id, Search.position != 0)
    ) or 0
    if ordered or session.scalar(select(func.count(Separator.id))):
        return
    searches = session.scalars(
        select(Search).where(Search.user_id == user_id).order_by(Search.agency, Search.name)
    )
    for index, search in enumerate(searches):
        search.position = index


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
