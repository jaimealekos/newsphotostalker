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
    Separator,
    User,
    utcnow,
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
    search = Search(
        user_id=user_id,
        position=_next_position(session, user_id),
        # Nace vista: sus primeras fotos no cuentan como "novedad desde tu
        # última visita" hasta que la búsqueda haya corrido estando tú fuera.
        seen_at=utcnow(),
        **_normalise_form(form),
    )
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


def mark_seen(session: Session, search: Search) -> Search:
    """Apaga la luz de novedades de UNA búsqueda (al abrirla)."""
    search.seen_at = utcnow()
    session.commit()
    return search


# --- orden del panel y separadores -----------------------------------------
# Búsquedas y separadores comparten la escala de ``position``, así que el panel
# es una sola lista ordenada que el usuario coloca a mano desde el modo edición.
def _next_position(session: Session, user_id: int) -> int:
    highest = max(
        session.scalar(select(func.max(Search.position)).where(Search.user_id == user_id)) or 0,
        session.scalar(select(func.max(Separator.position)).where(Separator.user_id == user_id)) or 0,
    )
    return highest + 1


def create_separator(session: Session, user_id: int, label: str = "") -> Separator:
    separator = Separator(
        user_id=user_id,
        label=(label or "").strip(),
        position=_next_position(session, user_id),
    )
    session.add(separator)
    session.commit()
    return separator


def update_separator(session: Session, separator: Separator, label: str) -> Separator:
    separator.label = (label or "").strip()
    session.commit()
    return separator


def delete_separator(session: Session, separator: Separator) -> None:
    session.delete(separator)
    session.commit()


def own_separator(session: Session, separator_id: int, user_id: int) -> Separator | None:
    separator = session.get(Separator, separator_id)
    return separator if separator and separator.user_id == user_id else None


def reorder_panel(session: Session, user_id: int, items: list[dict]) -> int:
    """Reescribe el orden del panel a partir de la lista del modo edición.

    ``items`` son ``{"type": "search"|"separator", "id": N}`` en el orden final;
    los separadores pueden traer además ``label``, así que arrastrar y renombrar
    se guardan de una vez. Solo se tocan las filas del usuario; lo que no venga
    en la lista (una búsqueda creada en otra pestaña mientras editabas) se queda
    detrás, con su orden intacto, en vez de saltar al principio.
    """
    mine = {
        "search": set(session.scalars(select(Search.id).where(Search.user_id == user_id))),
        "separator": set(
            session.scalars(select(Separator.id).where(Separator.user_id == user_id))
        ),
    }
    models = {"search": Search, "separator": Separator}

    position = 0
    moved = 0
    for item in items:
        kind = str(item.get("type"))
        if kind not in models:
            continue
        try:
            row_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if row_id not in mine[kind]:
            continue
        row = session.get(models[kind], row_id)
        if row is None:
            continue
        row.position = position
        if kind == "separator" and "label" in item:
            row.label = str(item.get("label") or "").strip()
        position += 1
        moved += 1

    # Las filas que no venían en la lista van al final, conservando su orden.
    leftovers = sorted(
        [
            row
            for kind, model in models.items()
            for row in session.scalars(select(model).where(model.user_id == user_id))
            if not any(
                str(i.get("type")) == kind and str(i.get("id")) == str(row.id) for i in items
            )
        ],
        key=lambda r: r.position,
    )
    for row in leftovers:
        row.position = position
        position += 1

    session.commit()
    return moved


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
    # Cuándo se guardó la última foto. Comparado con el ``seen_at`` de la propia
    # búsqueda da ``has_new``: la luz es de cada búsqueda, no del panel entero.
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
    stats = SearchStats(search, row[0], row[1], row[2], row[3], row[4])
    stats.has_new = _has_new(stats.last_added, search.seen_at)
    return stats


def _has_new(last_added: datetime | None, seen_at: datetime | None) -> bool:
    """¿Ha entrado alguna foto después de la última visita a esta búsqueda?"""
    if last_added is None:
        return False
    if seen_at is None:
        return True
    return _aware(last_added) > _aware(seen_at)


def _aware(value: datetime) -> datetime:
    """SQLite devuelve fechas sin zona; se leen siempre como UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def all_search_stats(session: Session, user_id: int) -> list[SearchStats]:
    searches = list(
        session.scalars(
            select(Search)
            .where(Search.user_id == user_id)
            .order_by(Search.position, Search.agency, Search.name)
        )
    )
    return [search_stats(session, s) for s in searches]


@dataclass
class PanelRow:
    """Una fila del panel: o una búsqueda con sus datos, o un separador."""

    kind: str  # "search" | "separator"
    position: int
    stats: SearchStats | None = None
    separator: Separator | None = None

    @property
    def row_id(self) -> int:
        return self.stats.search.id if self.kind == "search" else self.separator.id


def panel_rows(session: Session, user_id: int) -> list[PanelRow]:
    """Búsquedas y separadores mezclados en el orden que fijó el usuario."""
    rows = [
        PanelRow("search", st.search.position, stats=st)
        for st in all_search_stats(session, user_id)
    ]
    rows += [
        PanelRow("separator", sep.position, separator=sep)
        for sep in session.scalars(
            select(Separator).where(Separator.user_id == user_id).order_by(Separator.position)
        )
    ]
    # A igualdad de posición (solo posible antes del primer reordenado) el
    # separador encabeza el grupo que le sigue.
    rows.sort(key=lambda r: (r.position, r.kind == "search", r.row_id))
    return rows


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


# --- la cuenta (desde la 1.1 hay una sola) ---------------------------------
def update_user(session: Session, user: User, username: str, password: str) -> User:
    """Cambia el nombre y/o la contraseña de la única cuenta."""
    username = (username or "").strip()
    if username and username != user.username:
        if not session.scalar(select(User).where(User.username == username)):
            user.username = username
    if password:
        user.password_hash = hash_password(password)
    session.commit()
    return user
