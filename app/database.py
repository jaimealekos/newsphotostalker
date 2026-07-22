"""SQLAlchemy engine + session setup."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import REPO_ROOT, get_settings

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
            abs_path = (REPO_ROOT / path).resolve()
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

    - Añade ``searches.user_id`` si la base de datos es anterior al
      multiusuario (v1.0).
    - Garantiza que existe un admin (primera vez: ``admin`` / ``admin``;
      cámbiala desde Ajustes).
    - Asigna al admin las búsquedas sin dueño.
    """
    from .auth import hash_password
    from .models import AppSettings, User

    with session_scope() as session:
        cols = [row[1] for row in session.execute(text("PRAGMA table_info(searches)"))]
        if "user_id" not in cols:
            session.execute(text("ALTER TABLE searches ADD COLUMN user_id INTEGER"))
            log.warning("Migración: añadida la columna searches.user_id")

        # Fila única de ajustes globales (refresco + fotos por página).
        if session.get(AppSettings, 1) is None:
            session.add(AppSettings(id=1))
            log.warning("Creada la fila de ajustes globales (id=1)")

        admin = session.query(User).filter(User.is_admin.is_(True)).first()
        if admin is None:
            admin = User(username="admin", password_hash=hash_password("admin"), is_admin=True)
            session.add(admin)
            session.flush()
            log.warning("Creado el usuario inicial admin/admin — cambia la contraseña desde Ajustes")

        session.execute(
            text("UPDATE searches SET user_id = :uid WHERE user_id IS NULL"), {"uid": admin.id}
        )


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
