"""La búsqueda de Reuters clasifica el fallo en vez de decir siempre lo mismo.

Producción emitió el 27-08-2026 tres avisos idénticos —«no result cards at …
(Timeout 45000ms)»— sin forma de saber cuál de las tres cosas había pasado: la
sesión caducada (única que exige un humano), el muro de DataDome servido en la
propia URL de búsqueda, o una página logueada que no pinta resultados. El
clasificador ya existía; lo que faltaba era llamarlo desde ``search()``.

Y el dato que la postdata del aviso pide —cuántos días aguantó la sesión— solo
salía por el camino del keep-alive. Ahora lo dicen también el login y la
búsqueda, que son por donde asoma el aviso que acaba leyendo un humano.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ingest import keepalive
from app.ingest.live_base import LiveAdapterError, SinSesionError

URL_BUSQUEDA = "https://www.reutersconnect.com/all?search=all%3Ax&media-types=picture"
URL_LOGIN = "https://www.reutersconnect.com/login?url64=aHR0cHM6Ly8"

INTERSTITIAL = (
    '<html><body><p id="cmsg">Please enable JS</p>'
    '<iframe src="https://geo.captcha-delivery.com/captcha/?x=1"></iframe>'
    "</body></html>"
)
LOGIN_CON_CAPTCHA = (
    "<html><body>Login | Reuters Connect"
    '<iframe src="https://geo.captcha-delivery.com/captcha/?y=2"></iframe>'
    "</body></html>"
)
PAGINA_SANA = '<html><body>Reuters Connect</body></html>'


class _PaginaFalsa:
    """Lo mínimo que toca el adaptador: ni navegador, ni red.

    ``wait_for_selector`` siempre revienta, que es el punto de partida de todos
    estos casos; lo que los distingue es la URL y el contenido.
    """

    def __init__(self, url, html, titulo, texto, url_revienta=False, nodos=0):
        self._url = url
        self._html = html
        self._titulo = titulo
        self._texto = texto
        self._url_revienta = url_revienta
        self._nodos = nodos

    @property
    def url(self):
        if self._url_revienta:
            raise RuntimeError("página a medio morir: ni la URL se puede leer")
        return self._url

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def wait_for_selector(self, *a, **k):
        raise TimeoutError("Page.wait_for_selector: Timeout 45000ms exceeded")

    def content(self):
        return self._html

    def title(self):
        return self._titulo

    def query_selector_all(self, selector):
        # Solo cuenta el recuento: el diagnóstico mide cuántos nodos de la
        # maqueta hay, no qué son.
        return [object()] * self._nodos

    def inner_text(self, selector, **kw):
        return self._texto


def _adaptador(
    tmp_path, *, url, html, titulo="Reuters Connect", texto="", url_revienta=False, nodos=0
):
    """ReutersAdapter sin abrir navegador (mismo truco que test_keepalive_renovacion)."""
    from app.ingest.reuters import ReutersAdapter

    ad = ReutersAdapter.__new__(ReutersAdapter)
    ad._page = _PaginaFalsa(url, html, titulo, texto, url_revienta, nodos)
    ad.settings = SimpleNamespace(
        playwright=SimpleNamespace(timeout_ms=1000),
        data_dir=tmp_path,
    )
    return ad


def _busca(ad):
    return ad.search(kind="text", query="x", since=None, limit=10)


# --- sesión caída ----------------------------------------------------------


def test_busqueda_con_sesion_caida_no_se_reintenta_y_dice_los_dias(tmp_path):
    """Reuters manda la búsqueda al login: eso es sesión muerta, no un tropiezo.

    Reintentar no puede arreglarlo (la sesión no aparece sola) y el aviso debe
    llevar lo que su postdata pide: los días que aguantó y qué hacer.
    """
    keepalive.marca_login_humano(SimpleNamespace(data_dir=tmp_path))
    ad = _adaptador(tmp_path, url=URL_LOGIN, html=LOGIN_CON_CAPTCHA)

    with pytest.raises(SinSesionError) as e:
        _busca(ad)

    mensaje = str(e.value)
    assert e.value.reintentable is False
    assert "aguantó" in mensaje and "días" in mensaje
    assert "iniciar sesión en Reuters" in mensaje


def test_sin_fichero_de_login_el_aviso_sale_igual_pero_sin_la_medida(tmp_path):
    """Nadie ha anotado nunca un login (instalación vieja): se omite la frase,
    pero el aviso —que es lo importante— sale igual."""
    ad = _adaptador(tmp_path, url=URL_LOGIN, html=LOGIN_CON_CAPTCHA)

    with pytest.raises(SinSesionError) as e:
        _busca(ad)

    assert "aguantó" not in str(e.value)
    assert "iniciar sesión en Reuters" in str(e.value)


# --- muro de DataDome ------------------------------------------------------


def test_busqueda_con_challenge_es_reintentable_y_nombra_a_datadome(tmp_path):
    """Con sesión buena, DataDome sirve su muro EN LA MISMA URL. Es transitorio:
    el reintento del runner (15 s) puede pasarlo, así que no vale SinSesionError."""
    ad = _adaptador(tmp_path, url=URL_BUSQUEDA, html=INTERSTITIAL)

    with pytest.raises(LiveAdapterError) as e:
        _busca(ad)

    assert not isinstance(e.value, SinSesionError)
    assert e.value.reintentable is True
    assert "DataDome" in str(e.value)


# --- sesión viva y ni una tarjeta ------------------------------------------


def test_sesion_viva_sin_tarjetas_se_lleva_el_diagnostico_puesto(tmp_path):
    """Ni sesión muerta ni muro: el aviso se lleva la foto de la página.

    Era «el caso que no sabíamos leer», y el diagnóstico es justo lo que lo
    contó (ver el test siguiente). Sigue viajando entero —título, URL, recuento
    de nodos y texto— porque es lo que permitirá DESMENTIR esa lectura el día
    que la huella cambie.
    """
    ad = _adaptador(
        tmp_path,
        url=URL_BUSQUEDA,
        html=PAGINA_SANA,
        titulo="Search | Reuters Connect",
        texto="  No results   found\n\n for this search  ",
    )

    with pytest.raises(LiveAdapterError) as e:
        _busca(ad)

    mensaje = str(e.value)
    assert e.value.reintentable is True
    assert "Search | Reuters Connect" in mensaje          # el título real
    assert "search=all%3Ax" in mensaje                    # la URL final
    assert "No results found for this search" in mensaje  # el texto, sin sopa de espacios
    assert "nodos data-qa-component=0" in mensaje         # ni un nodo de la maqueta


def test_la_huella_del_corte_de_reuters_sale_con_su_nombre(tmp_path):
    """La huella real de producción, y el aviso ya la nombra.

    Medida dos veces: 27-08-2026 (~4 h) y 31-08-2026 (~8 h, 09:01→17:05). En las
    dos, la misma foto exacta: sesión viva, título «Reuters Connect», 23 nodos
    data-qa-component —la maqueta entra ENTERA: cabecera, filtros, el avatar de
    la cuenta— y ni una tarjeta, en las tres búsquedas a la vez y con el
    keep-alive viendo la sesión viva cada hora. Luego se arregla solo.

    Eso es un corte del lado de Reuters, y el aviso tiene que decirlo: el correo
    del 31-08 mandaba «vuelve a iniciar sesión» para una sesión que llevaba 11
    días perfecta, que es exactamente el paseo que estos avisos vienen a evitar.
    """
    ad = _adaptador(
        tmp_path,
        url=URL_BUSQUEDA,
        html=PAGINA_SANA,
        titulo="Reuters Connect",
        texto="Skip to main content Feed 0 J A HIDE FILTER Feed U.S. Politics: Donald Trump",
        nodos=23,
    )

    with pytest.raises(LiveAdapterError) as e:
        _busca(ad)

    mensaje = str(e.value)
    assert e.value.reintentable is True          # transitorio: se recupera solo
    assert not isinstance(e.value, SinSesionError)
    assert "corte de Reuters" in mensaje         # lo NOMBRA
    assert "la sesión no se toca" in mensaje     # y desactiva el paseo inútil
    assert "nodos data-qa-component=23" in mensaje   # con la huella para desmentirlo


def test_lo_que_diga_la_pagina_no_manda_sobre_el_reintento(tmp_path):
    """El diagnóstico incrusta el TEXTO de la página, y el runner tiene una red
    por cadenas —«no hay sesión» entre ellas— para decidir qué no reintentar.

    Con el navegador en es-ES, a Reuters le basta con escribir esa frase para que
    un fallo reintentable dejara de reintentarse sin que nadie lo viera: la
    palabra que decide ya no la escribiríamos nosotros, la escribiría la página.
    La página aporta pruebas, no órdenes.
    """
    from app.ingest import runner

    ad = _adaptador(
        tmp_path,
        url=URL_BUSQUEDA,
        html=PAGINA_SANA,
        texto="Vaya: no hay sesión de búsqueda que mostrar",
    )

    with pytest.raises(LiveAdapterError) as e:
        _busca(ad)

    assert runner._merece_reintento(e.value) is True
    assert "sesión de búsqueda" in str(e.value)   # y se sigue leyendo lo que ponía


def test_ni_el_titulo_de_la_pagina_manda_sobre_el_reintento(tmp_path):
    """El <title> también lo escribe la página, y la red del runner coteja el
    mensaje ENTERO: citar solo el texto del body dejaba al título el poder de
    vetar un reintento en silencio (lo cazó la revisión de 08-2026)."""
    from app.ingest import runner

    ad = _adaptador(
        tmp_path,
        url=URL_BUSQUEDA,
        html=PAGINA_SANA,
        titulo="No hay sesión activa | Reuters Connect",
    )

    with pytest.raises(LiveAdapterError) as e:
        _busca(ad)

    assert runner._merece_reintento(e.value) is True
    assert "Reuters Connect" in str(e.value)      # el título se sigue leyendo


def test_si_el_clasificador_revienta_el_fallo_sigue_saliendo(tmp_path):
    """El clasificador nunca manda el desenlace: si él mismo se cae (página a
    medio morir), se cae al caso genérico en vez de tapar el error original con
    otro. Y el diagnóstico, sonda a sonda, tampoco revienta por eso."""
    ad = _adaptador(tmp_path, url=URL_BUSQUEDA, html=PAGINA_SANA, url_revienta=True)

    with pytest.raises(LiveAdapterError) as e:
        _busca(ad)

    assert e.value.reintentable is True
    assert "url=?" in str(e.value)          # la sonda falló, el diagnóstico no


# --- login -----------------------------------------------------------------


def test_login_sin_sesion_dice_cuanto_aguanto(tmp_path):
    """Es el sitio donde una sesión muerta del todo se detecta normalmente, y
    salía sin el dato que pide la postdata de su propio aviso."""
    keepalive.marca_login_humano(SimpleNamespace(data_dir=tmp_path))
    ad = _adaptador(tmp_path, url=URL_LOGIN, html=LOGIN_CON_CAPTCHA)

    with pytest.raises(SinSesionError) as e:
        ad.login()

    assert "aguantó" in str(e.value) and "días" in str(e.value)
    assert "no se automatiza" in str(e.value)
