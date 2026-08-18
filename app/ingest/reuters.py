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
            "no hay sesión de Reuters viva. Entra a mano una vez desde "
            "«ajustes → iniciar sesión en Reuters» (login_reuters.bat / "
            "python -m scripts.login_reuters): se abre una ventana del navegador, "
            "resuelves el acceso y la sesión queda guardada en el perfil. El login "
            "no se automatiza: Reuters bloquea la cuenta si se intenta."
        )

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
            raise LiveAdapterError(f"Reuters: no result cards at {url} ({exc})") from exc
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
