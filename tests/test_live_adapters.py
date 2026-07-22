"""Offline tests for the live adapters: URL/query building + parsing helpers.

These don't hit the network — they guard the verified request contracts
(field DSL, facet params, accent handling, date parsing) against regressions.
"""

from __future__ import annotations

from datetime import timezone

from app.config import get_settings
from app.ingest.ap import APAdapter, _safe_date
from app.ingest.getty import GettyAdapter, _strip_accents
from app.ingest.reuters import _parse_reuters_date


def _settings():
    return get_settings()


# --- AP -------------------------------------------------------------------
def test_ap_photographer_query_uses_field_dsl():
    ad = APAdapter(_settings(), _settings().credentials_for("ap"))
    assert ad.build_query("photographer", "Emilio Morenatti") == 'photographer.name:"Emilio Morenatti"'
    assert ad.build_query("text", "Madrid APTOPIX") == "Madrid APTOPIX"


def test_ap_rejects_corrupt_future_dates():
    # A year-2060 firstcreated must be ignored (falls back / None).
    assert _safe_date({"firstcreated": "2060-04-06T10:10:32Z"}) is None
    good = _safe_date({"firstcreated": "2026-07-13T04:49:01Z"})
    assert good is not None and good.year == 2026
    # falls back to arrivaldatetime when firstcreated is corrupt
    fb = _safe_date({"firstcreated": "2099-01-01T00:00:00Z", "arrivaldatetime": "2026-06-01T00:00:00Z"})
    assert fb is not None and fb.year == 2026


# --- Getty / AFP ----------------------------------------------------------
def test_getty_text_search_url():
    ad = GettyAdapter(_settings(), _settings().credentials_for("getty"), agency="getty")
    url = ad.build_search_url("text", "Spain wildfire")
    assert "phrase=Spain+wildfire" in url
    assert "collections=afp" not in url
    assert "family=editorial" in url


def test_getty_photographer_uses_artistexact():
    ad = GettyAdapter(_settings(), _settings().credentials_for("getty"), agency="getty")
    url = ad.build_search_url("photographer", "Pablo Blazquez Dominguez")
    assert "artistexact=Pablo+Blazquez+Dominguez" in url


def test_afp_restricts_to_afp_collection():
    ad = GettyAdapter(_settings(), _settings().credentials_for("afp"), agency="afp")
    url = ad.build_search_url("text", "Real Madrid")
    assert "collections=afp" in url


def test_strip_accents():
    assert _strip_accents("Óscar del Pozo") == "Oscar del Pozo"
    assert _strip_accents("Emilio Morenatti") == "Emilio Morenatti"


# --- Reuters --------------------------------------------------------------
def test_reuters_date_is_day_first():
    dt = _parse_reuters_date("06/07/2026 17:23")
    assert dt is not None
    assert (dt.day, dt.month, dt.year) == (6, 7, 2026)
    assert dt.tzinfo == timezone.utc


def test_reuters_bad_date_returns_none():
    assert _parse_reuters_date("") is None
    assert _parse_reuters_date(None) is None
