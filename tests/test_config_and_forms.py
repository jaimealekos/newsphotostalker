"""Config routing + form normalization (pure logic, no DB)."""

from __future__ import annotations

from app.config import get_settings
from app.services import _normalise_form


def test_afp_routes_to_getty_credentials():
    settings = get_settings()
    afp = settings.credentials_for("afp")
    getty = settings.credentials_for("getty")
    # AFP shares the Getty distribution credentials.
    assert afp is getty


def test_form_defaults_name_from_agency_and_query():
    data = _normalise_form({"agency": "ap", "kind": "text", "query": "Madrid APTOPIX"})
    assert data["name"] == "AP · Madrid APTOPIX"
    assert data["enabled"] is True


def test_form_time_retention_clears_nothing_but_parses_ints():
    data = _normalise_form(
        {
            "agency": "reuters",
            "kind": "photographer",
            "query": "Susana Vera",
            "retention_mode": "time",
            "retention_months": "3",
            "cadence_minutes": "120",
            "enabled": "on",
        }
    )
    assert data["retention_mode"] == "time"
    assert data["retention_months"] == 3
    assert data["cadence_minutes"] == 120


def test_form_checkbox_off_means_disabled():
    data = _normalise_form(
        {"agency": "getty", "kind": "text", "query": "Spain wildfire", "enabled": "off"}
    )
    assert data["enabled"] is False


def test_form_size_retention():
    data = _normalise_form(
        {
            "agency": "getty",
            "kind": "text",
            "query": "x",
            "retention_mode": "size",
            "retention_mb": "50",
        }
    )
    assert data["retention_mode"] == "size"
    assert data["retention_mb"] == 50
