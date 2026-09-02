"""Reuters Connect adapter (browser-driven).

VERIFIED AGAINST THE LIVE SERVICE (2026-07) with a manually established session.
Reuters Connect requires a real login and is protected by a DataDome bot-wall,
so this adapter drives a headed, logged-in Chromium profile (see
:mod:`.live_base`). The verified contract:

* Login: NO se automatiza, a propósito. El perfil persistente guarda la sesión,
  que un humano establece a mano una vez (scripts/login_reuters.py abre un
  navegador normal para pasar DataDome). Automatizar el login —teclear la
  contraseña contra auth.thomsonreuters.com— hace que Reuters contabilice
  intentos fallidos y bloquee la cuenta por IP (pasó en 08-2026). Si no hay
  sesión viva, login() falla limpiamente y pide el login manual.
* Search URL: https://www.reutersconnect.com/all?search=all%3A<query>
  (the "all:" prefix searches everything; a photographer's name matches their
  credited images). ``media-types=picture`` restricts to stills.
* Result cards use stable data-qa-component hooks:
    [data-qa-component="item-overview"]      one result card
      [data-qa-id]                           newsml id  -> external_id
      a[href*='/item/']                      detail URL
      [data-qa-component="item-headline"]    title
      [data-qa-component="overview-date"]    "dd/mm/yyyy HH:MM"
      [data-qa-component="overview-source"]  distributor/source (e.g. ZUMA)
      [data-qa-component="overview-thumbnail"] CSS background-image -> preview
* Preview images live on cdn1.agency.thomsonreuters.com and require a
  reutersconnect.com Referer to download (handled by the base download()).

Resolución (verificado 08-2026): **640 es el máximo**, y no hay forma de subir.
La URL de la tarjeta lleva el tamaño en la RUTA
(``/preview/<newsml>/<binary>/640x640?...``) y la firma de CloudFront cubre ese
tramo, así que pedirle 1024 o 2048 devuelve 403. La ficha del ítem publica una
ruta ``/watermark/…/800x800``, pero va SIN firmar y responde 403 incluso desde
dentro de la propia página con la sesión iniciada; las de 800 firmadas no
existen, y la ficha misma solo llega a mostrar imágenes de 640. Por eso el
adaptador no abre la ficha: sería tiempo perdido.

Card overviews expose the *source agency*, not the individual byline, so for
photographer searches the photographer is taken to be the query itself.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote

from dateutil import parser as dateparser

from .base import RawAsset
from .live_base import LiveAdapter, LiveAdapterError, SinSesionError

SEARCH_URL = "https://www.reutersconnect.com/all?search=all%3A{q}&media-types=picture&sort=newest"

CARD = '[data-qa-component="item-overview"]'
# Reuters pagina con un botón "LOAD MORE" (no scroll infinito) que avanza una
# lista VIRTUALIZADA. Hook estable del botón (el texto va en un <span> y en
# mayúsculas por CSS, así que no vale un selector por texto).
LOAD_MORE = '[data-qa-component="load-more-button"]'
# Tope de pulsaciones de "LOAD MORE" (evita bucles en consultas muy prolíficas).
# ~12 resultados por tanda: 60 tandas ≈ 720 resultados.
MAX_LOAD_MORE_CLICKS = 60


class ReutersAdapter(LiveAdapter):
    agency = "reuters"
    requires_login = True

    def login(self) -> None:
        page = self.page
        page.goto("https://www.reutersconnect.com/all", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        # ¿Ya hay sesión en el perfil persistente? Es el caso normal.
        if self._looks_logged_in():
            return

        # A partir de aquí NO se automatiza el login, nunca. Reuters contabiliza
        # cada intento fallido de inicio de sesión y bloquea la cuenta por IP:
        # pasó en 08-2026 cuando el re-login automático —teclear email+contraseña
        # contra el muro DataDome— se repitió al caducar la sesión. La sesión se
        # establece SOLO a mano, una vez, desde «ajustes → iniciar sesión en
        # Reuters» (login_reuters.bat / python -m scripts.login_reuters), que abre
        # un navegador normal y deja que un humano resuelva el acceso. Sin sesión
        # viva, la búsqueda falla limpiamente y se pide el login manual.
        raise SinSesionError(
            f"no hay sesión de Reuters viva{self._coletilla_duracion()}. "
            "Entra a mano una vez desde «ajustes → iniciar sesión en Reuters» "
            "(login_reuters.bat / python -m scripts.login_reuters): se abre una "
            "ventana del navegador, resuelves el acceso y la sesión queda guardada "
            "en el perfil. El login no se automatiza: Reuters bloquea la cuenta si "
            "se intenta."
        )

    def _coletilla_duracion(self) -> str:
        """« (la sesión aguantó 8.7 días)», o nada si no se sabe.

        Cuánto vive una sesión de Reuters no es público, y solo se aprende
        midiéndolo: cada aviso de sesión muerta que lleva el dato acerca el día
        en que podamos avisar ANTES de que caduque. El camino del keep-alive ya
        lo decía; este —el login y la búsqueda, por donde sale el aviso que
        acaba leyendo un humano— salía sin él, justo el dato que su propia
        postdata pide.
        """
        from .keepalive import dias_desde_login_humano  # aquí dentro: evita el ciclo

        dias = dias_desde_login_humano(self.settings)
        return f" (la sesión aguantó {dias:.1f} días)" if dias is not None else ""

    def _looks_logged_in(self) -> bool:
        return "/login" not in self.page.url and "auth.thomsonreuters.com" not in self.page.url

    def _datadome_challenge(self) -> bool:
        """True si la página actual lleva el CAPTCHA de DataDome.

        El marcador es el iframe del captcha (geo.captcha-delivery.com), que
        una página normal no incrusta jamás. No vale buscar «captcha-delivery»
        a secas: el tag de vigilancia de DataDome puede venir del mismo dominio
        en páginas perfectamente sanas, y daría challenge donde no lo hay.
        """
        try:
            return "geo.captcha-delivery.com" in (self.page.content() or "").lower()
        except Exception:  # noqa: BLE001
            return False

    def estado_sesion(self) -> str:
        """Clasifica la página actual: "viva", "challenge" o "caida".

        El orden lo dictaron dos sorpresas comprobadas en vivo:

        * Estar en el login MANDA. La propia página de login puede venir con el
          captcha de DataDome encima (visto en 08-2026: /login con el iframe de
          geo.captcha-delivery), y eso no la convierte en un muro transitorio:
          si Reuters te ha mandado al login, la sesión no está, y hay que
          decirlo — «challenge» aquí taparía la única avería que exige humano.
        * Con URL de logueado, el contenido decide. DataDome sirve su
          interstitial EN LA MISMA URL (un 401 deja page.url intacta), así que
          fiarse de la URL tomaría el muro por una sesión sana y anotaría
          éxitos falsos.
        """
        if not self._looks_logged_in():
            return "caida"
        if self._datadome_challenge():
            return "challenge"
        return "viva"

    def search(self, *, kind, query, since, limit=100):
        url = SEARCH_URL.format(q=quote(query))
        self.page.goto(url, wait_until="domcontentloaded")
        try:
            self.page.wait_for_selector(CARD, timeout=self.settings.playwright.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise self._sin_tarjetas(url, exc) from exc
        self.page.wait_for_timeout(1200)

        # La lista de resultados está VIRTUALIZADA: el DOM solo mantiene ~12
        # tarjetas a la vez y "LOAD MORE" avanza la ventana en lugar de acumular.
        # Por eso cosechamos por external_id tras cada tanda (no una sola vez al
        # final). Orden newest-first: paramos al rebasar el cursor (since) o al
        # reunir 'limit' resultados nuevos.
        since_aware = _aware(since) if since is not None else None
        collected: dict[str, RawAsset] = {}
        dry = 0
        for step in range(MAX_LOAD_MORE_CLICKS + 1):
            added = self._harvest_visible(collected, kind, query)

            if since_aware is not None and self._reaches_cursor(collected, since_aware):
                break
            if len(self._newer_than(collected, since_aware)) >= limit:
                break
            if step > 0 and added == 0:
                dry += 1
                if dry >= 2:  # dos tandas sin novedades: fin de resultados
                    break
            else:
                dry = 0

            try:
                btn = self.page.wait_for_selector(LOAD_MORE, timeout=6000)
            except Exception:  # noqa: BLE001 - no hay más botón: fin de resultados
                break
            try:
                btn.scroll_into_view_if_needed()
                btn.click()
            except Exception:  # noqa: BLE001
                break
            self.page.wait_for_timeout(1800)

        assets = self._newer_than(collected, since_aware)
        assets.sort(
            key=lambda a: a.captured_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return assets[:limit]

    def _sin_tarjetas(self, url: str, exc: Exception) -> LiveAdapterError:
        """Construye el error de «no hay tarjetas» DICIENDO cuál de los tres es.

        Los tres avisos idénticos del 27-08-2026 («no result cards at … Timeout
        45000ms») no permitían distinguir la única avería que exige un humano —la
        sesión caducada— del muro de DataDome servido en la propia URL de
        búsqueda, ni de una página logueada que sencillamente no pinta nada. Aquí
        se clasifica ANTES de reventar: cada caso sale con su nombre, con su
        reintentable y, cuando toca, con los días que aguantó la sesión.
        """
        try:
            estado = self.estado_sesion()
        except Exception:  # noqa: BLE001 - el clasificador nunca manda el desenlace
            estado = "viva"  # sin diagnóstico fiable, el caso genérico

        if estado == "caida":
            # Lo mismo que ve login() al abrir, pero visto desde la búsqueda:
            # Reuters redirige EN SERVIDOR al login. No se reintenta (SinSesionError
            # ya lo dice), porque la sesión no va a aparecer sola.
            return SinSesionError(
                "Reuters mandó la búsqueda al login: la sesión ha caducado"
                f"{self._coletilla_duracion()}. Entra a mano desde «ajustes → "
                "iniciar sesión en Reuters» (login_reuters.bat / python -m "
                "scripts.login_reuters). El login no se automatiza: Reuters "
                "bloquea la cuenta si se intenta."
            )

        if estado == "challenge":
            # Muro transitorio: la sesión está, pero DataDome se ha puesto delante
            # de esta URL. Reintentable a propósito — los 15 s del runner suelen
            # bastar para pasarlo.
            return LiveAdapterError(
                "Reuters: muro/challenge de DataDome servido en la URL de búsqueda "
                f"({url}); no hay resultados que leer. El reintento puede pasarlo."
            )

        # Sesión viva y aun así ni una tarjeta. Cuando se escribió esto no
        # sabíamos cuál de tres cosas era; el diagnóstico que se lleva puesto lo
        # contó, y son dos episodios con la MISMA huella: 27-08-2026 (~4 h) y
        # 31-08-2026 (~8 h, 09:01→17:05). En los dos, la maqueta entra entera
        # —cabecera, filtros, el avatar de tu cuenta: 23 nodos data-qa-component—
        # y la rejilla de resultados se queda vacía en TODAS las búsquedas a la
        # vez, con el keep-alive viendo la sesión viva cada hora; luego se
        # arregla solo. Eso es un corte del lado de Reuters, no una avería de
        # aquí, y el mensaje lo dice para que nadie salga corriendo a rehacer una
        # sesión que está perfectamente. El diagnóstico sigue detrás a propósito:
        # es lo que permitirá DESMENTIR esta lectura si algún día la huella
        # cambia —una maqueta nueva daría otro recuento de nodos, y cero
        # resultados de verdad traería su texto de «sin resultados»—.
        return LiveAdapterError(
            f"Reuters: la página carga con la sesión viva, pero no pinta ni una "
            f"tarjeta en {url}. Suele ser un corte de Reuters (visto el 27 y el "
            f"31-08-2026, horas seguidas y en todas las búsquedas a la vez) que "
            f"se recupera solo: la sesión no se toca. ({exc}); "
            f"{self._diagnostico_pagina()}"
        )

    def _diagnostico_pagina(self) -> str:
        """Una línea con lo que se ve en la página, para que el aviso se explique solo.

        Cada sonda va por separado y a prueba de fallos: una página a medio morir
        —que es justo la que provoca este camino— no debe convertir el
        diagnóstico en una segunda excepción que tape la primera. Y las que
        admiten plazo lo llevan CORTO: esto corre con el lock del runner tomado y
        después de haber quemado los 45 s del wait_for_selector, así que heredar
        ese mismo plazo por defecto sería pagar otro minuto de espera —dos, con el
        reintento— por una línea informativa. El try/except protege de la
        excepción; del plantón, solo el plazo.

        La línea sale ENTERA con las marcas del runner citadas (_cita_marcas):
        el <title> y la URL también los escribe la página, y la red por cadenas
        del runner coteja el mensaje completo — neutralizar solo el texto del
        body dejaba al título el poder de vetar un reintento en silencio.
        """
        sondas = (
            ("url", lambda: self.page.url),
            ("título", lambda: self.page.title()),
            ("nodos data-qa-component", lambda: len(self.page.query_selector_all("[data-qa-component]"))),
            ("texto", lambda: _recorte(self.page.inner_text("body", timeout=2000))),
        )
        partes = []
        for etiqueta, sonda in sondas:
            try:
                partes.append(f"{etiqueta}={sonda()!r}")
            except Exception:  # noqa: BLE001
                partes.append(f"{etiqueta}=?")
        return _cita_marcas("diagnóstico: " + ", ".join(partes))

    def _harvest_visible(self, collected: dict, kind: str, query: str) -> int:
        """Parsea las tarjetas ahora visibles y añade las nuevas. Devuelve
        cuántas se añadieron (0 = la ventana no trajo nada nuevo)."""
        added = 0
        for card in self.page.query_selector_all(CARD):
            asset = self._parse_card(card, kind, query)
            if asset and asset.external_id not in collected:
                collected[asset.external_id] = asset
                added += 1
        return added

    def _newer_than(self, collected: dict, since_aware: datetime | None) -> list[RawAsset]:
        assets = list(collected.values())
        if since_aware is None:
            return assets
        return [
            a for a in assets
            if a.captured_at is None or _aware(a.captured_at) > since_aware
        ]

    def _reaches_cursor(self, collected: dict, since_aware: datetime) -> bool:
        """True si ya hemos cosechado alguna foto anterior o igual al cursor:
        con orden newest-first, significa que ya tenemos todas las más nuevas."""
        return any(
            a.captured_at is not None and _aware(a.captured_at) <= since_aware
            for a in collected.values()
        )

    def _parse_card(self, card, kind: str, query: str) -> RawAsset | None:
        def comp_text(name):
            el = card.query_selector(f'[data-qa-component="{name}"]')
            return el.inner_text().strip() if el else None

        # The newsml id lives in data-qa-id on the card element itself (a nested
        # element carries an unrelated "videos" qa-id, so read the card's own).
        external_id = card.get_attribute("data-qa-id")
        # Cards without a newsml data-qa-id are cross-sell/promo tiles — skip.
        if not external_id or not external_id.startswith("tag:reuters"):
            return None
        link_el = card.query_selector("a[href*='/item/']")
        href = link_el.get_attribute("href") if link_el else None

        thumb_el = card.query_selector('[data-qa-component="overview-thumbnail"]')
        preview_url = None
        if thumb_el:
            preview_url = self._bg_of(thumb_el)

        title = comp_text("item-headline")
        source = comp_text("overview-source")
        date_txt = comp_text("overview-date")

        return RawAsset(
            external_id=external_id,
            agency=self.agency,
            title=title,
            caption=comp_text("item-overview") or title,
            photographer=(query if kind == "photographer" else None),
            credit=source,
            captured_at=_parse_reuters_date(date_txt),
            keywords=[query] + ([source] if source else []),
            detail_url=_absolute(href),
            thumbnail_url=preview_url,
            preview_url=preview_url,
            raw_metadata={
                "source": "reutersconnect.com",
                "distributor": source,
                "search_kind": kind,
                "search_query": query,
                "date_text": date_txt,
            },
        )

    def _bg_of(self, handle) -> str | None:
        css = handle.evaluate("el => getComputedStyle(el).backgroundImage")
        m = re.search(r'url\(["\']?([^"\')]+)', css or "")
        return m.group(1) if m else None



def _recorte(texto: str | None, limite: int = 150) -> str:
    """Texto visible con los espacios colapsados y recortado: lo justo para
    reconocer de un vistazo qué página era, sin inundar el correo del aviso."""
    return re.sub(r"\s+", " ", texto or "").strip()[:limite]


def _cita_marcas(texto: str) -> str:
    """Neutraliza en ``texto`` las marcas de NO_REINTENTABLES, citándolas.

    El runner decide si reintentar preguntando primero a la excepción y, como
    red, cotejando el mensaje ENTERO con NO_REINTENTABLES; desde que el
    diagnóstico incrusta lo que se lee en la página —body, <title> y URL, que
    los tres los escribe la página— bastaría con que Reuters pusiera «no hay
    sesión» en cualquiera de ellos —el navegador va en es-ES— para que un fallo
    perfectamente reintentable dejara de reintentarse en silencio. Lo que dice
    la página es una prueba, no una orden: las marcas se neutralizan al entrar,
    y sobre la línea completa del diagnóstico, no sonda a sonda — citar solo el
    body dejaba al título ese poder de veto (lo cazó la revisión de 08-2026).
    """
    from .runner import NO_REINTENTABLES  # aquí dentro: evita el ciclo de imports

    def _cita(m: re.Match) -> str:
        # Punto medio en vez del espacio ("no·hay·sesión"): se lee igual y ya no
        # casa. Si algún día la marca fuera de una sola palabra, se parte igual.
        trozo = m.group(0)
        return trozo.replace(" ", "·") if " " in trozo else trozo[:1] + "·" + trozo[1:]

    marcas = "|".join(re.escape(marca) for marca in NO_REINTENTABLES)
    return re.sub(marcas, _cita, texto, flags=re.IGNORECASE) if marcas else texto


def _parse_reuters_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        # Reuters shows dd/mm/yyyy HH:MM
        return dateparser.parse(text, dayfirst=True).replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


def _absolute(href: str | None) -> str | None:
    if not href:
        return None
    return href if href.startswith("http") else f"https://www.reutersconnect.com{href}"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
