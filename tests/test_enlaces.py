"""Los enlaces del nombre del fotógrafo a la web de su agencia."""

from __future__ import annotations

import pytest

from app.enlaces import url_del_fotografo


def test_reuters_busca_por_nombre():
    url = url_del_fotografo("reuters", "Juan Medina")
    assert url.startswith("https://www.reutersconnect.com/all?search=all%3A")
    assert "Juan%20Medina" in url
    assert "media-types=picture" in url  # solo fotos, como en la app


def test_getty_busca_por_autoria_exacta_no_por_texto():
    """`artistexact` es la diferencia entre las fotos DE alguien y las fotos
    donde a alguien lo mencionan en el pie."""
    url = url_del_fotografo("getty", "Pablo Blázquez Domínguez")
    assert "artistexact=Pablo+Bl%C3%A1zquez+Dom%C3%ADnguez" in url
    assert "family=editorial" in url and "sort=newest" in url
    assert "collections=afp" not in url


def test_afp_va_por_getty_pero_acotado_a_su_coleccion():
    url = url_del_fotografo("afp", "Pierre Dupont")
    assert url.startswith("https://www.gettyimages.com/search/2/image?")
    assert "collections=afp" in url


def test_ap_manda_el_termino_en_query_y_no_en_st():
    """`st` es el TIPO de búsqueda en la web de AP, no el término.

    Se leyó en su propio código —`this.query=p.get("query")`,
    `this.searchType=p.get("st")`— después de que dos versiones del enlace
    dejaran la página en blanco. Si alguien vuelve a poner `st=`, este test cae.
    """
    url = url_del_fotografo("ap", "Emilio Morenatti")
    assert url == (
        "https://newsroom.ap.org/editorial-photos-videos/search?query=Emilio%20Morenatti"
    )
    assert "?st=" not in url and "&st=" not in url
    assert "photographer.name" not in url  # su API lo entiende; su web, no


@pytest.mark.parametrize("nombre", [None, "", "   "])
def test_sin_nombre_no_hay_enlace(nombre):
    """Muchas fotos llegan sin fotógrafo; el panel las pinta sin enlazar."""
    assert url_del_fotografo("ap", nombre) is None


def test_una_agencia_desconocida_no_inventa_enlace():
    assert url_del_fotografo("efe", "Alguien") is None
    assert url_del_fotografo("", "Alguien") is None


def test_los_nombres_con_comillas_o_ampersand_no_rompen_la_url():
    """Un nombre hostil no debe poder salirse de su parámetro."""
    url = url_del_fotografo("reuters", 'X" & Y=1')
    assert '"' not in url and "&" not in url.split("?", 1)[1].split("media-types")[0][:-1]
    assert url_del_fotografo("getty", "A&B")
    assert "&B" not in url_del_fotografo("getty", "A&B").split("artistexact=")[1]


def test_la_agencia_da_igual_en_mayusculas():
    assert url_del_fotografo("REUTERS", "X") == url_del_fotografo("reuters", "X")
