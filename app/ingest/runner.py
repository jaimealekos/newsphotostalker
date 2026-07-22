"""Ingest runner — orchestrates one run of a search.

Steps per run:
  1. open the agency adapter,
  2. search for assets newer than the search cursor (the "novedades" logic),
  3. skip assets already stored (de-dup by external_id),
  4. download preview + thumbnail and persist the asset + metadata,
  5. advance the cursor, record status,
  6. apply the retention policy (purge oldest / over-limit),
  7. write a RunLog row.

A module-level lock serialises runs so concurrent scheduler jobs don't fight
over the SQLite database.
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from .. import alerts, storage
from ..config import get_settings
from ..database import session_scope
from ..models import RETENTION_TIME, Asset, RunLog, Search, utcnow
from ..retention import purge_search
from .factory import get_adapter

_RUN_LOCK = threading.Lock()

# Centinela: "usa el cursor de novedades de la búsqueda" (por defecto). El
# backfill pasa un `since` explícito (la fecha límite de retención, o None).
_USE_CURSOR = object()

# Backfill (relleno bajo demanda): tope de páginas por adaptador y de fotos
# totales, para que baje hasta el límite de retención sin desbocarse.
BACKFILL_PAGE_CAP = 60
BACKFILL_MAX = 3000


@dataclass
class RunResult:
    search_id: int
    status: str
    new_assets: int = 0
    purged_assets: int = 0
    found: int = 0
    message: str = ""
    errors: list[str] = field(default_factory=list)


def run_search(search_id: int, limit: int = 100, *, since=_USE_CURSOR, page_cap=None) -> RunResult:
    """Run a single search end-to-end. Thread-safe (serialised).

    Por defecto usa el cursor de novedades (solo trae lo más nuevo). El backfill
    pasa ``since`` explícito (fecha límite de retención o None) y un ``page_cap``
    alto para bajar hacia atrás y rellenar toda la ventana.
    """
    with _RUN_LOCK:
        return _run_search_locked(search_id, limit, since, page_cap)


def backfill_search(search_id: int) -> RunResult:
    """Rellena el histórico de una búsqueda hasta su límite de retención.

    Ignora el cursor de novedades y baja hasta la fecha de retención (o, con
    retención por espacio, hasta ``BACKFILL_MAX``); el de-dup por external_id
    hace que solo se descargue lo que aún no está guardado.
    """
    with session_scope() as session:
        search = session.get(Search, search_id)
        floor = _retention_floor(search) if search is not None else None
    return run_search(search_id, limit=BACKFILL_MAX, since=floor, page_cap=BACKFILL_PAGE_CAP)


def _retention_floor(search: Search) -> datetime | None:
    """Fecha más antigua que la retención conservaría (None si es por espacio)."""
    if search.retention_mode == RETENTION_TIME and search.retention_months:
        return datetime.now(timezone.utc) - relativedelta(months=search.retention_months)
    return None


def _run_search_locked(search_id: int, limit: int, since, page_cap) -> RunResult:
    settings = get_settings()

    with session_scope() as session:
        search = session.get(Search, search_id)
        if search is None:
            return RunResult(search_id, "error", message="search not found")
        # snapshot the fields we need after the session closes
        agency = search.agency
        kind = search.kind
        query = search.query
        cursor = search.cursor
        existing_ids = set(
            session.scalars(select(Asset.external_id).where(Asset.search_id == search_id))
        )
        run = RunLog(search_id=search_id, status="running", started_at=utcnow())
        session.add(run)
        session.flush()
        run_id = run.id

    since_for_search = cursor if since is _USE_CURSOR else since
    result = RunResult(search_id=search_id, status="ok")
    new_cursor = cursor

    try:
        adapter = get_adapter(agency, settings)
        with adapter:
            # Backfill: sube el tope de páginas del adaptador (AP/Getty) para
            # poder bajar hasta el límite de retención en una sola pasada.
            if page_cap is not None:
                try:
                    adapter.MAX_PAGES = page_cap
                except Exception:  # noqa: BLE001
                    pass
            found = adapter.search(kind=kind, query=query, since=since_for_search, limit=limit)
            result.found = len(found)

            for raw in found:
                if raw.external_id in existing_ids:
                    continue
                try:
                    _store_asset(adapter, search_id, agency, raw)
                    existing_ids.add(raw.external_id)
                    result.new_assets += 1
                    cap = _as_aware(raw.captured_at)
                    if cap and (new_cursor is None or cap > _as_aware(new_cursor)):
                        new_cursor = cap
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"{raw.external_id}: {exc}")
    except Exception as exc:  # noqa: BLE001
        result.status = "error"
        result.message = f"{type(exc).__name__}: {exc}"
        result.errors.append(traceback.format_exc(limit=3))

    # Persist run outcome + retention purge.
    with session_scope() as session:
        search = session.get(Search, search_id)
        if search is not None:
            search.last_run_at = utcnow()
            search.last_status = result.status
            search.last_error = result.message or None
            if new_cursor is not None:
                search.cursor = _as_aware(new_cursor)
            try:
                result.purged_assets = purge_search(session, search)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"purge: {exc}")

        run = session.get(RunLog, run_id)
        if run is not None:
            run.finished_at = utcnow()
            run.status = result.status
            run.new_assets = result.new_assets
            run.purged_assets = result.purged_assets
            run.message = _summarise(result)

    # Aviso por flanco (solo agencias vigiladas; ver app/alerts.py). Nunca
    # debe tumbar el run: el aviso es un extra, el run ya está registrado.
    try:
        alerts.record_run(settings, agency, result.status != "error", result.message or None)
    except Exception:  # noqa: BLE001
        pass

    result.message = _summarise(result)
    return result


def _store_asset(adapter, search_id: int, agency: str, raw) -> None:
    directory = storage.asset_dir(agency, search_id, raw.external_id)
    # Reuse the already-open adapter (live adapters keep a single browser open
    # for the whole run) so downloads share the authenticated session.
    files = adapter.download(raw, directory)

    metadata = {
        "external_id": raw.external_id,
        "agency": raw.agency,
        "title": raw.title,
        "caption": raw.caption,
        "photographer": raw.photographer,
        "credit": raw.credit,
        "captured_at": raw.captured_at.isoformat() if raw.captured_at else None,
        "keywords": raw.keywords,
        "detail_url": raw.detail_url,
        "raw_metadata": raw.raw_metadata,
    }
    storage.write_metadata(directory, metadata)

    with session_scope() as session:
        asset = Asset(
            search_id=search_id,
            agency=agency,
            external_id=raw.external_id,
            title=raw.title,
            caption=raw.caption,
            photographer=raw.photographer,
            credit=raw.credit,
            captured_at=_as_aware(raw.captured_at),
            keywords=raw.keywords,
            raw_metadata=raw.raw_metadata,
            detail_url=raw.detail_url,
            thumbnail_path=storage.to_relative(files.thumbnail_path),
            preview_path=storage.to_relative(files.preview_path),
            file_bytes=files.file_bytes,
            downloaded_at=utcnow(),
        )
        session.add(asset)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _summarise(result: RunResult) -> str:
    if result.status == "error":
        return result.message or "error"
    parts = [f"{result.new_assets} nuevas", f"{result.found} encontradas"]
    if result.purged_assets:
        parts.append(f"{result.purged_assets} purgadas")
    if result.errors:
        parts.append(f"{len(result.errors)} errores de descarga")
    return ", ".join(parts)


def run_all_enabled(limit: int = 100) -> list[RunResult]:
    """Run every enabled search once (used by the scheduler and CLI)."""
    with session_scope() as session:
        ids = list(session.scalars(select(Search.id).where(Search.enabled.is_(True))))
    return [run_search(sid, limit=limit) for sid in ids]
