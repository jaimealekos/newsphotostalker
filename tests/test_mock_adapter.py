"""Tests for the mock adapter: determinism, since-filtering, downloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PIL import Image

from app.config import get_settings
from app.ingest.mock import MockAdapter


def _adapter(agency="ap"):
    settings = get_settings()
    return MockAdapter(settings, settings.credentials_for(agency), agency=agency)


def test_search_is_deterministic():
    a1 = _adapter().search(kind="text", query="Real Madrid", since=None)
    a2 = _adapter().search(kind="text", query="Real Madrid", since=None)
    assert [x.external_id for x in a1] == [x.external_id for x in a2]
    assert len(a1) == MockAdapter.POOL_SIZE


def test_photographer_kind_sets_photographer_to_query():
    assets = _adapter().search(kind="photographer", query="Emilio Morenatti", since=None)
    assert all(a.photographer == "Emilio Morenatti" for a in assets)


def test_since_filters_older_assets():
    all_assets = _adapter().search(kind="text", query="Real Madrid", since=None)
    cutoff = datetime.now(timezone.utc) - timedelta(days=20)
    newer = _adapter().search(kind="text", query="Real Madrid", since=cutoff)
    assert len(newer) < len(all_assets)
    assert all(a.captured_at > cutoff for a in newer)


def test_results_sorted_newest_first():
    assets = _adapter().search(kind="text", query="Spain wildfire", since=None)
    dates = [a.captured_at for a in assets]
    assert dates == sorted(dates, reverse=True)


def test_download_writes_valid_jpegs(tmp_path):
    adapter = _adapter("reuters")
    asset = adapter.search(kind="photographer", query="Susana Vera", since=None)[0]
    files = adapter.download(asset, tmp_path)
    assert files.file_bytes > 0
    for p in (files.preview_path, files.thumbnail_path):
        img = Image.open(p)
        assert img.format == "JPEG"
        assert img.size[0] > 0
