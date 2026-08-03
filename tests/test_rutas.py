"""Cómo están declaradas las rutas que conducen el navegador.

Playwright tiene dos API, una de bloqueo y otra asíncrona, y la de bloqueo
estalla si se la llama desde dentro del bucle de eventos. FastAPI ejecuta las
rutas ``def`` en un hilo aparte y las ``async def`` en el bucle, así que una
ruta que use Playwright TIENE que declararse síncrona.

Se coló una vez (importar sesión) y no se vio hasta ejercitarla contra el
programa empaquetado: leyendo el código no se nota nada raro.
"""

from __future__ import annotations

import inspect

import pytest

from app.main import app

#: Rutas que acaban conduciendo un navegador con la API de bloqueo.
RUTAS_CON_NAVEGADOR = [
    "/reuters/session/import",
    "/reuters/session/export",
]


def _endpoint(path: str):
    for ruta in app.routes:
        if getattr(ruta, "path", None) == path:
            return ruta.endpoint
    raise AssertionError(f"no existe la ruta {path}")


@pytest.mark.parametrize("path", RUTAS_CON_NAVEGADOR)
def test_las_rutas_con_navegador_no_son_asincronas(path):
    endpoint = _endpoint(path)
    assert not inspect.iscoroutinefunction(endpoint), (
        f"{path} está declarada async: Playwright fallaría con «Sync API inside "
        "the asyncio loop». Quítale el async y FastAPI la correrá en un hilo."
    )


def test_las_rutas_del_traspaso_de_sesion_existen():
    """Son la única vía para autenticar Reuters en un equipo sin pantalla."""
    caminos = {getattr(r, "path", "") for r in app.routes}
    assert {"/reuters/session/export", "/reuters/session/import"} <= caminos
    assert "/reuters/login" in caminos
