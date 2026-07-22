"""Retention / purge logic.

Each search declares a retention policy:

  * ``time``  — keep only assets captured within the last N months; older ones
                are purged.
  * ``size``  — keep the search's total media under N megabytes; while over the
                limit, purge the oldest assets until back under.

Purging removes both the DB rows and the files on disk. Called at the end of
every run so limits are enforced continuously.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import storage
from .models import RETENTION_SIZE, RETENTION_TIME, Asset, Search


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sort_key(a: Asset) -> datetime:
    ref = _as_aware(a.captured_at) or _as_aware(a.downloaded_at)
    return ref or datetime.min.replace(tzinfo=timezone.utc)


def purge_search(session: Session, search: Search) -> int:
    """Enforce the retention policy for one search. Returns count purged."""
    assets = list(session.scalars(select(Asset).where(Asset.search_id == search.id)))
    if not assets:
        return 0

    if search.retention_mode == RETENTION_TIME and search.retention_months:
        return _purge_by_time(session, assets, search.retention_months)
    if search.retention_mode == RETENTION_SIZE and search.retention_mb:
        return _purge_by_size(session, assets, search.retention_mb)
    return 0


def _purge_by_time(session: Session, assets: list[Asset], months: int) -> int:
    # Approximate a month as 30 days for the cutoff window.
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
    purged = 0
    for a in assets:
        if _sort_key(a) < cutoff:
            _delete_asset(session, a)
            purged += 1
    return purged


def _purge_by_size(session: Session, assets: list[Asset], limit_mb: int) -> int:
    limit_bytes = limit_mb * 1024 * 1024
    total = sum(a.file_bytes or 0 for a in assets)
    if total <= limit_bytes:
        return 0
    # Oldest first, delete until under the limit.
    oldest_first = sorted(assets, key=_sort_key)
    purged = 0
    for a in oldest_first:
        if total <= limit_bytes:
            break
        total -= a.file_bytes or 0
        _delete_asset(session, a)
        purged += 1
    return purged


def _delete_asset(session: Session, asset: Asset) -> None:
    storage.remove_asset_files(asset.preview_path, asset.thumbnail_path)
    session.delete(asset)
