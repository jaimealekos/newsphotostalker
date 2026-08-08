"""El mecanismo que sostiene las fotos destacadas: la cookie de sesión.

La regla dice que las novedades se desmarcan al cerrar el navegador. Eso no se
puede detectar desde el servidor, así que se apoya en una cookie SIN caducidad,
que es la que el navegador borra al cerrarse. Si alguien le pusiera un
``max_age`` "para que no se pierda", la regla se rompería en silencio: las
novedades sobrevivirían al cierre. De ahí que se compruebe aquí.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from starlette.responses import Response

from app.main import COOKIE_FRONTERAS, MAX_FRONTERAS, _frontera_de_sesion, _guarda_fronteras

AHORA = datetime.now(timezone.utc)


def _peticion(cookie: str | None = None):
    return SimpleNamespace(cookies={COOKIE_FRONTERAS: cookie} if cookie else {})


def _busqueda(id_, seen_at):
    return SimpleNamespace(id=id_, seen_at=seen_at)


def test_la_cookie_no_lleva_caducidad_para_morir_con_el_navegador():
    respuesta = Response()
    _guarda_fronteras(respuesta, {"1": AHORA.isoformat()})
    cabecera = respuesta.headers["set-cookie"].lower()

    assert COOKIE_FRONTERAS in cabecera
    assert "max-age" not in cabecera, "con max-age la marca sobreviviría al cierre"
    assert "expires" not in cabecera, "con expires la marca sobreviviría al cierre"
    assert "httponly" in cabecera


def test_primera_visita_congela_el_visto_anterior():
    visto = AHORA - timedelta(hours=5)
    frontera, a_guardar = _frontera_de_sesion(_peticion(), _busqueda(7, visto))

    assert frontera == visto              # la frontera es DONDE SE QUEDÓ la visita anterior
    assert a_guardar == {"7": visto.isoformat()}


def test_al_recargar_la_frontera_no_se_toca():
    """Esto es lo que hace que las fotos sigan destacadas tras recargar."""
    congelada = (AHORA - timedelta(hours=5)).isoformat()
    cookie = json.dumps({"7": congelada})
    # El `seen_at` de la búsqueda YA lo movió la visita anterior; da igual.
    frontera, a_guardar = _frontera_de_sesion(_peticion(cookie), _busqueda(7, AHORA))

    assert frontera == datetime.fromisoformat(congelada)
    assert a_guardar is None               # no se reescribe la cookie


def test_otra_busqueda_se_anota_sin_pisar_la_anterior():
    previa = (AHORA - timedelta(hours=9)).isoformat()
    cookie = json.dumps({"7": previa})
    visto8 = AHORA - timedelta(hours=2)
    _, a_guardar = _frontera_de_sesion(_peticion(cookie), _busqueda(8, visto8))

    assert a_guardar == {"7": previa, "8": visto8.isoformat()}


def test_una_busqueda_nunca_visitada_no_destaca_nada():
    frontera, a_guardar = _frontera_de_sesion(_peticion(), _busqueda(3, None))
    assert frontera is None
    assert a_guardar == {"3": ""}


def test_una_cookie_manipulada_no_tumba_la_pagina():
    for basura in ("no es json", "[1,2,3]", json.dumps({"7": "fecha inventada"})):
        frontera, a_guardar = _frontera_de_sesion(_peticion(basura), _busqueda(7, AHORA))
        assert frontera == AHORA           # se rehace desde el visto de la búsqueda
        assert a_guardar is not None


def test_la_cookie_no_crece_sin_fin():
    muchas = {str(i): AHORA.isoformat() for i in range(MAX_FRONTERAS + 10)}
    _, a_guardar = _frontera_de_sesion(_peticion(json.dumps(muchas)), _busqueda(9999, AHORA))
    assert len(a_guardar) == MAX_FRONTERAS
    assert "9999" in a_guardar              # la de ahora nunca es la descartada
