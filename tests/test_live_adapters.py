"""Offline tests for the live adapters: URL/query building + parsing helpers.

These don't hit the network — they guard the verified request contracts
(field DSL, facet params, accent handling, date parsing) against regressions.
"""

from __future__ import annotations

from datetime import timezone

from app.config import get_settings
from app.ingest.ap import APAdapter, _safe_date
from app.ingest.getty import GettyAdapter, _find_comp, _strip_accents
from app.ingest.reuters import _parse_reuters_date


def _settings():
    return get_settings()


# --- AP -------------------------------------------------------------------
def test_ap_photographer_query_uses_field_dsl():
    ad = APAdapter(_settings(), _settings().credentials_for("ap"))
    assert ad.build_query("photographer", "Emilio Morenatti") == 'photographer.name:"Emilio Morenatti"'
    assert ad.build_query("text", "Madrid APTOPIX") == "Madrid APTOPIX"


def test_ap_detail_url_usa_query_no_st():
    """El enlace «Ver en la agencia» de cada foto de AP.

    `st` es el TIPO de búsqueda en su web, no el término: con `st` el enlace
    abría una página en blanco. Leído en el código de su propia web.
    """
    ad = APAdapter(_settings(), _settings().credentials_for("ap"))
    asset = ad._parse_item({"_source": {"itemid": "abc123"}}, "text", "x")
    assert asset is not None, "el objeto de prueba tiene que parsearse de verdad"
    assert asset.detail_url == (
        "https://newsroom.ap.org/editorial-photos-videos/search"
        "?query=abc123&mediaType=photo&st=keyword"
    )
    assert "?st=" not in asset.detail_url  # el id nunca va en `st`


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
# --- Getty: comp grande de la ficha ---------------------------------------
# El listado solo trae el JPEG de 612px y su firma va atada a ese tamaño; los
# comps de 1024/2048 van firmados aparte y solo salen en la ficha de la foto.
_FICHA = (
    '<meta content="https://media.gettyimages.com/id/2288794175/es/foto/x.jpg'
    "?s=1024x1024&amp;w=gi&amp;k=20&amp;c=MILUNO=\" property='og:image'/>"
    '{"url":"https://media.gettyimages.com/id/2288794175/es/foto/x.jpg'
    '?s=2048x2048\\u0026w=gi\\u0026k=20\\u0026c=DOSMIL="}'
    '<img src="https://media.gettyimages.com/id/9999999/es/foto/otra.jpg'
    '?s=2048x2048&amp;w=gi&amp;k=20&amp;c=RELACIONADA=">'
)


def test_getty_comp_prefiere_2048_y_desescapa_la_url():
    url = _find_comp(_FICHA, "2288794175")
    assert url.startswith("https://media.gettyimages.com/id/2288794175/")
    assert "s=2048x2048" in url and "&amp;" not in url and "\\u0026" not in url
    assert url.endswith("c=DOSMIL=")


def test_getty_comp_no_se_lleva_el_de_una_foto_relacionada():
    """La ficha enseña fotos parecidas: el id tiene que mandar."""
    assert "9999999" not in (_find_comp(_FICHA, "2288794175") or "")
    solo_relacionadas = _FICHA[_FICHA.index("<img"):]
    assert _find_comp(solo_relacionadas, "2288794175") is None


def test_getty_comp_cae_al_1024_si_no_hay_2048():
    sin_2048 = _FICHA[: _FICHA.index('{"url"')]
    assert "s=1024x1024" in _find_comp(sin_2048, "2288794175")


def test_getty_sin_comp_devuelve_none():
    assert _find_comp("<html>ni una foto</html>", "2288794175") is None


def test_reuters_date_is_day_first():
    dt = _parse_reuters_date("06/07/2026 17:23")
    assert dt is not None
    assert (dt.day, dt.month, dt.year) == (6, 7, 2026)
    assert dt.tzinfo == timezone.utc


def test_reuters_bad_date_returns_none():
    assert _parse_reuters_date("") is None
    assert _parse_reuters_date(None) is None
