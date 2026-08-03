"""Config routing + form normalization (pure logic, no DB)."""

from __future__ import annotations

import yaml

from app.config import FIRST_RUN_CONFIG, _read_config, get_settings
from app.services import _normalise_form


# --- codificación de la configuración --------------------------------------
# El .exe escribe su config en UTF-8 y luego la lee; con read_text() a secas,
# Python usa la codificación regional (cp1252 en un Windows español) y el primer
# arranque moría leyendo su propio fichero recién creado.
def test_config_utf8_se_lee_aunque_la_region_no_lo_sea(tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text(FIRST_RUN_CONFIG, encoding="utf-8")
    datos = yaml.safe_load(_read_config(ruta))
    assert datos["mode"] == "live"
    assert datos["playwright"]["headless"] is True


def test_config_guardada_en_ansi_tambien_se_lee(tmp_path):
    """Si alguien la reescribe con el Bloc de notas, no debe romperse."""
    ruta = tmp_path / "config.yaml"
    ruta.write_bytes("mode: live  # configuración en español\n".encode("cp1252"))
    assert yaml.safe_load(_read_config(ruta))["mode"] == "live"


def test_la_config_del_primer_arranque_es_yaml_valido_y_en_vivo():
    datos = yaml.safe_load(FIRST_RUN_CONFIG)
    assert datos["mode"] == "live"
    assert datos["data_dir"] == "./data"
    # Sin credenciales de Reuters: se entra a mano desde ajustes.
    assert datos["agencies"]["reuters"]["username"] is None


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
