"""Business-logic helpers used by the web layer.

Keeps the FastAPI route handlers thin: search CRUD (with scheduler
side-effects), dashboard statistics, and the activity feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import scheduler, storage
from .auth import hash_password
from .models import (
    KIND_PHOTOGRAPHER,
    KIND_TEXT,
    RETENTION_SIZE,
    RETENTION_TIME,
    AppSettings,
    Asset,
    RunLog,
    Search,
    User,
)

AGENCY_CHOICES = [
    ("ap", "Associated Press"),
    ("reuters", "Reuters"),
    ("afp", "AFP (vía Getty)"),
    ("getty", "Getty Images"),
]
KIND_CHOICES = [(KIND_PHOTOGRAPHER, "Fotógrafo"), (KIND_TEXT, "Búsqueda de texto")]
RETENTION_CHOICES = [(RETENTION_TIME, "Por tiempo (meses)"), (RETENTION_SIZE, "Por espacio (MB)")]


# --- create / update / delete --------------------------------------------
# Nota: el refresco es GLOBAL (un job que corre todas las búsquedas juntas,
# ver scheduler.py). Crear/editar/activar una búsqueda ya NO programa un job
# propio; enabled solo decide si el refresco global la incluye.
def create_search(session: Session, form: dict, user_id: int) -> Search:
    search = Search(user_id=user_id, **_normalise_form(form))
    session.add(search)
    session.commit()
    return search


def update_search(session: Session, search: Search, form: dict) -> Search:
    for key, value in _normalise_form(form).items():
        setattr(search, key, value)
    session.commit()
    return search


def delete_search(session: Session, search: Search) -> None:
    # Remove media files for every asset before the cascade delete.
    for asset in search.assets:
        storage.remove_asset_files(asset.preview_path, asset.thumbnail_path)
    session.delete(search)
    session.commit()


def toggle_search(session: Session, search: Search) -> Search:
    search.enabled = not search.enabled
    session.commit()
    return search


# --- ajustes globales ------------------------------------------------------
def get_app_settings(session: Session) -> AppSettings:
    cfg = session.get(AppSettings, 1)
    if cfg is None:  # por si la BD es anterior a esta tabla
        cfg = AppSettings(id=1)
        session.add(cfg)
        session.commit()
    return cfg


def update_app_settings(session: Session, form: dict) -> AppSettings:
    cfg = get_app_settings(session)
    cfg.photos_per_page = max(1, min(500, _to_int(form.get("photos_per_page"), cfg.photos_per_page)))
    cfg.refresh_every = max(1, _to_int(form.get("refresh_every"), cfg.refresh_every))
    unit = str(form.get("refresh_unit") or cfg.refresh_unit)
    cfg.refresh_unit = unit if unit in ("hours", "days") else "hours"
    cfg.refresh_start_hour = max(0, min(23, _to_int(form.get("refresh_start_hour"), cfg.refresh_start_hour)))
    cfg.refresh_start_minute = max(0, min(59, _to_int(form.get("refresh_start_minute"), cfg.refresh_start_minute)))
    session.commit()
    # Aplica el nuevo horario al job global de refresco.
    scheduler.reschedule()
    return cfg


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalise_form(form: dict) -> dict:
    mode = form.get("retention_mode", RETENTION_TIME)
    months = form.get("retention_months")
    mb = form.get("retention_mb")
    data = {
        "name": (form.get("name") or "").strip(),
        "agency": form["agency"],
        "kind": form["kind"],
        "query": (form.get("query") or "").strip(),
        "cadence_minutes": int(form.get("cadence_minutes") or 360),
        "retention_mode": mode,
        "retention_months": int(months) if (mode == RETENTION_TIME and months) else (int(months) if months else None),
        "retention_mb": int(mb) if (mode == RETENTION_SIZE and mb) else (int(mb) if mb else None),
        "enabled": _as_bool(form.get("enabled", True)),
    }
    if not data["name"]:
        data["name"] = f"{data['agency'].upper()} · {data['query']}"
    return data


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "on", "yes")


# --- statistics -----------------------------------------------------------
@dataclass
class SearchStats:
    search: Search
    asset_count: int
    total_bytes: int
    newest: datetime | None
    oldest: datetime | None
    # Cuándo se guardó la última foto; ``has_new`` lo rellena la ruta del
    # dashboard comparándolo con la última visita del navegador (cookie).
    last_added: datetime | None = None
    has_new: bool = False

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / (1024 * 1024), 1)


def search_stats(session: Session, search: Search) -> SearchStats:
    row = session.execute(
        select(
            func.count(Asset.id),
            func.coalesce(func.sum(Asset.file_bytes), 0),
            func.max(Asset.captured_at),
            func.min(Asset.captured_at),
            func.max(Asset.downloaded_at),
        ).where(Asset.search_id == search.id)
    ).one()
    return SearchStats(search, row[0], row[1], row[2], row[3], row[4])


def all_search_stats(session: Session, user_id: int) -> list[SearchStats]:
    searches = list(
        session.scalars(
            select(Search).where(Search.user_id == user_id).order_by(Search.agency, Search.name)
        )
    )
    return [search_stats(session, s) for s in searches]


@dataclass
class GlobalStats:
    searches: int
    enabled: int
    assets: int
    total_mb: float
    agencies: dict[str, int]


def global_stats(session: Session, user_id: int) -> GlobalStats:
    """Estadísticas del usuario (cada uno ve solo lo suyo)."""
    mine = Search.user_id == user_id
    total_searches = session.scalar(select(func.count(Search.id)).where(mine)) or 0
    enabled = (
        session.scalar(select(func.count(Search.id)).where(mine, Search.enabled.is_(True))) or 0
    )
    asset_join = select(Asset).join(Search, Asset.search_id == Search.id).where(mine)
    total_assets = session.scalar(
        select(func.count()).select_from(asset_join.subquery())
    ) or 0
    total_bytes = session.scalar(
        select(func.coalesce(func.sum(Asset.file_bytes), 0))
        .select_from(Asset)
        .join(Search, Asset.search_id == Search.id)
        .where(mine)
    ) or 0
    by_agency = dict(
        session.execute(
            select(Asset.agency, func.count(Asset.id))
            .join(Search, Asset.search_id == Search.id)
            .where(mine)
            .group_by(Asset.agency)
        ).all()
    )
    return GlobalStats(
        searches=total_searches,
        enabled=enabled,
        assets=total_assets,
        total_mb=round(total_bytes / (1024 * 1024), 1),
        agencies=by_agency,
    )


# --- per-search photo view -------------------------------------------------
def _chrono_order():
    """Orden de la vista de búsqueda: más nueva primero (fallback: descarga)."""
    return (func.coalesce(Asset.captured_at, Asset.downloaded_at).desc(), Asset.id.desc())


def search_assets(
    session: Session, search_id: int, page: int = 1, per_page: int = 60
) -> tuple[list[Asset], int]:
    total = session.scalar(select(func.count(Asset.id)).where(Asset.search_id == search_id)) or 0
    stmt = (
        select(Asset)
        .where(Asset.search_id == search_id)
        .order_by(*_chrono_order())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    return list(session.scalars(stmt)), total


def adjacent_asset_ids(session: Session, asset: Asset) -> tuple[int | None, int | None]:
    """IDs (anterior, siguiente) respecto al orden cronológico de su búsqueda."""
    ids = list(
        session.scalars(
            select(Asset.id).where(Asset.search_id == asset.search_id).order_by(*_chrono_order())
        )
    )
    i = ids.index(asset.id)
    prev_id = ids[i - 1] if i > 0 else None
    next_id = ids[i + 1] if i + 1 < len(ids) else None
    return prev_id, next_id


# --- activity feed --------------------------------------------------------
def recent_runs(session: Session, user_id: int, limit: int = 20) -> list[RunLog]:
    """Ejecuciones de las búsquedas del usuario (las huérfanas no se listan)."""
    mine = select(Search.id).where(Search.user_id == user_id)
    return list(
        session.scalars(
            select(RunLog)
            .where(RunLog.search_id.in_(mine))
            .order_by(RunLog.started_at.desc())
            .limit(limit)
        )
    )


# --- usuarios (solo los maneja el admin) -----------------------------------
def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.is_admin.desc(), User.username)))


def create_user(session: Session, username: str, password: str) -> User | None:
    username = (username or "").strip()
    if not username or not password:
        return None
    if session.scalar(select(User).where(User.username == username)):
        return None  # nombre ya en uso
    user = User(username=username, password_hash=hash_password(password), is_admin=False)
    session.add(user)
    session.commit()
    return user


def update_user(session: Session, user: User, username: str, password: str) -> User:
    username = (username or "").strip()
    if username and username != user.username:
        if not session.scalar(select(User).where(User.username == username)):
            user.username = username
    if password:
        user.password_hash = hash_password(password)
    session.commit()
    return user


def delete_user(session: Session, user: User) -> None:
    """Borra un usuario con todas sus búsquedas y fotos (ficheros incluidos)."""
    for search in list(user.searches):
        delete_search(session, search)
    session.delete(user)
    session.commit()
