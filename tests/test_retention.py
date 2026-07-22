"""Retention/purge policy tests, against an isolated in-memory database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import RETENTION_SIZE, RETENTION_TIME, Asset, Search
from app.retention import purge_search


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_search(session, **kw):
    defaults = dict(name="s", agency="ap", kind="text", query="q")
    defaults.update(kw)
    search = Search(**defaults)
    session.add(search)
    session.flush()
    return search


def _add_asset(session, search, *, days_ago, mb=1):
    captured = datetime.now(timezone.utc) - timedelta(days=days_ago)
    a = Asset(
        search_id=search.id,
        agency=search.agency,
        external_id=f"id-{days_ago}-{mb}",
        captured_at=captured,
        file_bytes=mb * 1024 * 1024,
    )
    session.add(a)
    session.flush()
    return a


def test_time_retention_purges_older_than_window(session):
    search = _make_search(session, retention_mode=RETENTION_TIME, retention_months=3)
    _add_asset(session, search, days_ago=10)   # keep
    _add_asset(session, search, days_ago=80)   # keep (< 90d)
    _add_asset(session, search, days_ago=100)  # purge (> 90d)
    _add_asset(session, search, days_ago=200)  # purge

    purged = purge_search(session, search)
    assert purged == 2
    assert session.query(Asset).count() == 2


def test_size_retention_keeps_under_limit_newest_first(session):
    search = _make_search(session, retention_mode=RETENTION_SIZE, retention_mb=5, retention_months=None)
    # 8 MB total; limit 5 MB -> must purge oldest until <= 5 MB
    _add_asset(session, search, days_ago=1, mb=2)   # newest, keep
    _add_asset(session, search, days_ago=5, mb=2)   # keep
    _add_asset(session, search, days_ago=10, mb=2)  # borderline
    _add_asset(session, search, days_ago=30, mb=2)  # oldest, purge first

    purged = purge_search(session, search)
    remaining = session.query(Asset).all()
    total_mb = sum(a.file_bytes for a in remaining) / (1024 * 1024)
    assert total_mb <= 5
    assert purged >= 1
    # The oldest (30 days) must be gone.
    assert all(a.external_id != "id-30-2" for a in remaining)


def test_size_retention_noop_when_under_limit(session):
    search = _make_search(session, retention_mode=RETENTION_SIZE, retention_mb=100, retention_months=None)
    _add_asset(session, search, days_ago=1, mb=2)
    assert purge_search(session, search) == 0


def test_time_retention_uses_downloaded_when_no_capture(session):
    search = _make_search(session, retention_mode=RETENTION_TIME, retention_months=1)
    a = Asset(search_id=search.id, agency="ap", external_id="nocap", captured_at=None,
              downloaded_at=datetime.now(timezone.utc) - timedelta(days=40), file_bytes=1)
    session.add(a)
    session.flush()
    assert purge_search(session, search) == 1
