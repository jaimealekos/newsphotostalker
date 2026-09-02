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
        _migrate_assets(session)

        # Al arrancar no hay ninguna ejecución en curso, así que cualquier RunLog
        # que quedara en "running" es de un cierre a medias: se marca terminado
        # para que el panel no muestre un indicador de trabajo eterno.
        session.execute(
            text(
                "UPDATE run_logs SET status='error', finished_at=:now, "
                "message=COALESCE(message,'')||' (interrumpido)' "
                "WHERE status='running' AND finished_at IS NULL"
            ),
            {"now": datetime.now(timezone.utc)},
        )

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
        _migrate_grupos(session, user.id)


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


def _migrate_grupos(session: Session, user_id: int) -> None:
    """Convierte los separadores (1.1–1.2) en grupos, y no deja búsquedas sueltas.

    El separador era una raya suelta: el bloque que «abría» era todo lo que
    venía detrás en la lista ordenada, hasta el siguiente separador. Esa lectura
    —la que ya se veía en pantalla— es la que se conserva aquí, para que al
    actualizar el panel salga exactamente igual que estaba: cada separador se
    convierte en el grupo de las búsquedas que le seguían, y las que iban ANTES
    del primero se quedan en «Sin grupo».

    Un separador sin rótulo (los que solo metían aire) pasa a llamarse «Sin
    título»: como grupo hay que poder pulsarlo para abrir su feed, y una etiqueta
    en blanco no se puede pulsar. Se renombra en dos segundos desde «ordenar».

    Idempotente y sin pérdida: solo añade la columna si falta, solo lee la tabla
    vieja si existe, y al terminar la deja sin borrar hasta haberla vaciado.
    """
    from .models import Search, SearchGroup

    cols = [row[1] for row in session.execute(text("PRAGMA table_info(searches)"))]
    if "group_id" not in cols:
        session.execute(text("ALTER TABLE searches ADD COLUMN group_id INTEGER"))
        log.warning("Migración: añadida la columna searches.group_id")

    tablas = {
        row[0]
        for row in session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='separators'")
        )
    }
    if tablas:
        # Se mezclan por posición con el MISMO desempate que usaba el panel (a
        # igualdad, el separador va delante), o el primer bloque se comería la
        # raya que lo abría.
        seps = list(
            session.execute(
                text("SELECT id, label, position FROM separators WHERE user_id = :u"),
                {"u": user_id},
            )
        )
        if seps:
            busquedas = list(
                session.scalars(select(Search).where(Search.user_id == user_id))
            )
            filas = [(int(p), 0, str(lab or ""), None) for _id, lab, p in seps]
            filas += [(int(s.position), 1, "", s) for s in busquedas]
            filas.sort(key=lambda f: (f[0], f[1]))

            grupo = None
            posicion = 0
            dentro = 0
            for _pos, es_busqueda, etiqueta, search in filas:
                if not es_busqueda:
                    grupo = SearchGroup(
                        user_id=user_id, name=etiqueta.strip() or "Sin título", position=posicion
                    )
                    session.add(grupo)
                    session.flush()
                    posicion += 1
                    dentro = 0
                    continue
                if search.group_id is not None:
                    continue
                if grupo is None:
                    grupo = _grupo_por_defecto(session, user_id, posicion)
                    posicion += 1
                search.group_id = grupo.id
                search.position = dentro
                dentro += 1
            session.flush()
            log.warning("Migración: %s separadores convertidos en grupos", len(seps))

        session.execute(text("DELETE FROM separators WHERE user_id = :u"), {"u": user_id})
        sobran = session.scalar(text("SELECT COUNT(*) FROM separators")) or 0
        if not sobran:
            session.execute(text("DROP TABLE separators"))
            log.warning("Migración: la tabla separators ya no hace falta")

    # Nadie se queda fuera: lo pide el propio panel (toda búsqueda vive dentro de
    # un grupo) y es también la red que recoge lo que llegue por otra vía —una
    # base tocada a mano, un grupo borrado a pelo—.
    huerfanas = list(
        session.scalars(
            select(Search)
            .where(Search.user_id == user_id, Search.group_id.is_(None))
            .order_by(Search.position, Search.id)
        )
    )
    if huerfanas:
        grupo = _grupo_por_defecto(session, user_id)
        hueco = (
            session.scalar(
                select(func.max(Search.position)).where(Search.group_id == grupo.id)
            )
            or -1
        ) + 1
        for search in huerfanas:
            search.group_id = grupo.id
            search.position = hueco
            hueco += 1
        log.warning("Migración: %s búsquedas adoptadas por «%s»", len(huerfanas), grupo.name)


def _grupo_por_defecto(session: Session, user_id: int, posicion: int | None = None):
    """El grupo «Sin grupo», creándolo si aún no existe."""
    from .models import GRUPO_POR_DEFECTO, SearchGroup

    grupo = session.scalars(
        select(SearchGroup).where(
            SearchGroup.user_id == user_id, SearchGroup.name == GRUPO_POR_DEFECTO
        )
    ).first()
    if grupo is None:
        if posicion is None:
            posicion = (
                session.scalar(
                    select(func.max(SearchGroup.position)).where(SearchGroup.user_id == user_id)
                )
                or -1
            ) + 1
        grupo = SearchGroup(user_id=user_id, name=GRUPO_POR_DEFECTO, position=posicion)
        session.add(grupo)
        session.flush()
    return grupo


def _migrate_assets(session: Session) -> None:
    """Añade a ``assets`` las columnas que falten en bases de datos antiguas.

    Solo añade columnas: nunca borra ni reescribe filas. Las fotos que ya
    estaban quedan con ``seen_at`` a NULL, o sea "sin abrir", que es lo cierto;
    no salen destacadas igualmente porque son anteriores a tu última visita.
    """
    cols = [row[1] for row in session.execute(text("PRAGMA table_info(assets)"))]
    if "seen_at" not in cols:
        session.execute(text("ALTER TABLE assets ADD COLUMN seen_at DATETIME"))
        log.warning("Migración: añadida la columna assets.seen_at")

    # Los enlaces «Ver en la agencia» de AP se guardaron con `st=`, que en su web
    # es el TIPO de búsqueda y no el término: abrían una página vacía. Se
    # reescribe el parámetro en las fotos ya guardadas. Solo cambia esa palabra
    # dentro de la URL; no toca ninguna otra columna ni borra ninguna fila.
    arreglados = session.execute(
        text(
            "UPDATE assets SET detail_url = REPLACE(detail_url, "
            "'/editorial-photos-videos/search?st=', '/editorial-photos-videos/search?query=') "
            "WHERE agency = 'ap' AND detail_url LIKE '%/editorial-photos-videos/search?st=%'"
        )
    ).rowcount
    if arreglados:
        log.warning("Migración: corregidos %s enlaces a AP (st= -> query=)", arreglados)

    # Y les falta el tipo de medio: sin `mediaType` la web de AP encuentra los
    # resultados pero no pinta ninguna foto. Se añade a las que no lo llevan.
    # Idempotente: la condición excluye las que ya lo tienen.
    completados = session.execute(
        text(
            "UPDATE assets SET detail_url = detail_url || '&mediaType=photo&st=keyword' "
            "WHERE agency = 'ap' "
            "  AND detail_url LIKE '%/editorial-photos-videos/search?query=%' "
            "  AND detail_url NOT LIKE '%mediaType=%'"
        )
    ).rowcount
    if completados:
        log.warning("Migración: completados %s enlaces a AP (+mediaType)", completados)


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
    from .models import Search, SearchGroup

    ordered = session.scalar(
        select(func.count(Search.id)).where(Search.user_id == user_id, Search.position != 0)
    ) or 0
    if ordered or session.scalar(select(func.count(SearchGroup.id))):
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
