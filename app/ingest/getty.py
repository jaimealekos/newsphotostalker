"""Getty distribution adapter — serves BOTH Getty Images and AFP.

AFP content is distributed through Getty, so a single adapter handles both:
when ``agency == "afp"`` the search is restricted to the AFP collection
(``collections=afp``); for plain Getty it is not.

VERIFIED AGAINST THE LIVE SERVICE (2026-07): gettyimages.com serves fully
server-rendered search pages with schema.org microdata per result card, so
this adapter uses plain HTTP — no browser required. The verified contract:

* Text search:
    https://www.gettyimages.com/search/2/image?phrase=<q>&family=editorial&sort=newest
* Photographer search (exact artist match; discovered via the redirect from
  /search/photographer):
    https://www.gettyimages.com/search/2/image?artistexact=<name>&assettype=image&family=editorial&sort=newest
* AFP restriction: append ``&collections=afp`` (checked: creators become AFP
  staff and EXIF copyright reads "AFP or licensors").
* Pagination: ``&page=N`` (60 unique results per page, no overlap).
* Each card carries data-asset-id + itemProp microdata: caption, creator,
  creditText, contentUrl/thumbnailUrl (612px unwatermarked JPEG on
  media.gettyimages.com) and uploadDate (full ISO timestamp — used as the
  novelty cursor).
* Resolución (verificado 08-2026): el ``contentUrl`` del listado es de 612px y
  su firma ``c=`` va **atada a ese tamaño** — cambiar ``s=`` a mano devuelve
  400. Los comps grandes (1024 y 2048, con marca de agua) van firmados aparte y
  solo aparecen en la ficha ``/detail/<id>``, así que la foto grande cuesta una
  petición extra por foto NUEVA (ver :meth:`GettyAdapter.download`).
"""

from __future__ import annotations

import html as html_lib
import re
import time
import unicodedata
from dataclasses import replace
from datetime import datetime
from urllib.parse import quote_plus

from dateutil import parser as dateparser

from .base import RawAsset
from .http_base import HttpAdapter, HttpAdapterError

SEARCH_BASE = "https://www.gettyimages.com/search/2/image"
DETAIL_BASE = "https://www.gettyimages.com/detail"

# Tamaños de comp que publica la ficha, de mayor a menor preferencia.
COMP_SIZES = ("2048x2048", "1024x1024")

# Result cards: anchor by the asset id + testid marker, slice card-to-card.
CARD_ANCHOR = re.compile(r'data-asset-id="(\d+)"\s+data-testid="galleryMosaicAsset"')

_FIELDS = {
    "caption": re.compile(r'itemProp="caption" content="([^"]*)"'),
    "creator": re.compile(r'itemProp="creator"[^>]*><meta itemProp="name" content="([^"]*)"'),
    "credit": re.compile(r'itemProp="creditText" content="([^"]*)"'),
    "content_url": re.compile(r'itemProp="contentUrl" content="([^"]*)"'),
    "thumb_url": re.compile(r'itemProp="thumbnailUrl" content="([^"]*)"'),
    "upload_date": re.compile(r'itemProp="uploadDate" content="([^"]*)"'),
    "detail": re.compile(r'itemProp="acquireLicensePage" content="([^"]*)"'),
    "name": re.compile(r'itemProp="name" content="([^"]*)"'),
}


class GettyAdapter(HttpAdapter):
    #: pages fetched per run at most (60 results per page)
    MAX_PAGES = 3
    #: pausa entre fichas al bajar los comps grandes (una por foto nueva)
    DETAIL_DELAY = 0.5

    def __init__(self, settings, credentials, agency: str = "getty"):
        super().__init__(settings, credentials)
        self.agency = agency

    # -- url building ------------------------------------------------------
    def build_search_url(self, kind: str, query: str, page: int = 1) -> str:
        params = ["family=editorial", "sort=newest", "assettype=image"]
        if kind == "photographer":
            params.append(f"artistexact={quote_plus(query)}")
        else:
            params.append(f"phrase={quote_plus(query)}")
        if self.agency == "afp":
            params.append("collections=afp")
        if page > 1:
            params.append(f"page={page}")
        return f"{SEARCH_BASE}?{'&'.join(params)}"

    # -- search ------------------------------------------------------------
    def search(self, *, kind, query, since, limit=100):
        # Getty's artistexact facet is accent-sensitive ("Óscar" != "Oscar").
        # Try the query as typed, then fall back to an accent-stripped form so
        # the panel is forgiving of accents in photographer names.
        effective = query
        if kind == "photographer":
            stripped = _strip_accents(query)
            if stripped != query and not self._has_results(kind, query):
                effective = stripped
        return self._search(kind=kind, query=effective, since=since, limit=limit)

    def _has_results(self, kind: str, query: str) -> bool:
        try:
            return bool(self._parse_page(self.get_html(self.build_search_url(kind, query, 1)), kind, query))
        except HttpAdapterError:
            return False

    def _search(self, *, kind, query, since, limit):
        assets: list[RawAsset] = []
        seen: set[str] = set()
        for page in range(1, self.MAX_PAGES + 1):
            url = self.build_search_url(kind, query, page)
            page_html = self.get_html(url)
            page_assets = self._parse_page(page_html, kind, query)
            if not page_assets:
                if page == 1:
                    raise HttpAdapterError(
                        f"Getty/{self.agency}: 0 result cards at {url} — page layout may have changed"
                    )
                break

            fresh = [a for a in page_assets if a.external_id not in seen]
            seen.update(a.external_id for a in fresh)
            assets.extend(fresh)

            # Results are sorted newest-first: once a page's oldest item is
            # older than the cursor, later pages can't contain novelties.
            oldest = min(
                (a.captured_at for a in page_assets if a.captured_at), default=None
            )
            if since is not None and oldest is not None and oldest <= _aware(since):
                break
            if len(assets) >= limit:
                break
            self.polite_pause()

        if since is not None:
            assets = [a for a in assets if a.captured_at is None or a.captured_at > _aware(since)]
        return assets[:limit]

    def _parse_page(self, page_html: str, kind: str, query: str) -> list[RawAsset]:
        anchors = list(CARD_ANCHOR.finditer(page_html))
        assets: list[RawAsset] = []
        for i, m in enumerate(anchors):
            start = m.start()
            end = anchors[i + 1].start() if i + 1 < len(anchors) else min(len(page_html), start + 12000)
            segment = page_html[start:end]
            asset = self._parse_card(m.group(1), segment, kind, query)
            if asset:
                assets.append(asset)
        return assets

    # -- download ----------------------------------------------------------
    def download(self, asset: RawAsset, dest_dir):
        """Guarda el comp grande como preview y el de 612 como miniatura.

        El listado solo trae el JPEG de 612px; el grande hay que pedírselo a la
        ficha de la foto. Esa petición extra solo se paga por foto NUEVA (el
        runner ya ha descartado las que están guardadas), y si falla se baja el
        de 612 de siempre en vez de perder la foto.
        """
        big = self._comp_url(asset)
        if big:
            asset = replace(asset, preview_url=big, thumbnail_url=asset.thumbnail_url or asset.preview_url)
        return super().download(asset, dest_dir)

    def _comp_url(self, asset: RawAsset) -> str | None:
        url = asset.detail_url or f"{DETAIL_BASE}/{asset.external_id}"
        try:
            page_html = self.get_html(url)
        except HttpAdapterError:
            return None
        finally:
            time.sleep(self.DETAIL_DELAY)
        return _find_comp(page_html, asset.external_id)

    def _parse_card(self, asset_id: str, segment: str, kind: str, query: str) -> RawAsset | None:
        def field(key):
            m = _FIELDS[key].search(segment)
            return html_lib.unescape(m.group(1)) if m else None

        content_url = field("content_url")
        if not content_url:
            return None
        caption = field("caption")
        creator = field("creator")
        detail = field("detail")

        return RawAsset(
            external_id=asset_id,
            agency=self.agency,
            title=(caption or "")[:140] or field("name"),
            caption=caption,
            photographer=creator,
            credit=field("credit") or creator,
            captured_at=_parse_date(field("upload_date")),
            keywords=[query],
            detail_url=detail or f"https://www.gettyimages.com/detail/{asset_id}",
            thumbnail_url=field("thumb_url") or content_url,
            preview_url=content_url,
            raw_metadata={
                "source": "gettyimages.com",
                "asset_id": asset_id,
                "distributor": "AFP" if self.agency == "afp" else "Getty",
                "search_kind": kind,
                "search_query": query,
                "upload_date": field("upload_date"),
            },
        )


def _find_comp(page_html: str, asset_id: str) -> str | None:
    """URL firmada del comp más grande de ESA foto dentro de su ficha.

    La ficha también enseña fotos relacionadas, así que el id va dentro del
    patrón: si no, se colaría el comp de otra imagen.
    """
    # La ficha escapa las URLs de dos maneras: HTML (&amp;) en las etiquetas
    # meta y JSON (&) en el estado embebido.
    text = page_html.replace("&amp;", "&").replace("\\u0026", "&")
    for size in COMP_SIZES:
        match = re.search(
            r'https://media\.gettyimages\.com/id/'
            + re.escape(asset_id)
            + r'/[^"\'\\\s]+?\.jpg\?s='
            + size
            + r'&[^"\'\\\s]*c=[^"\'\\\s&]+',
            text,
        )
        if match:
            return match.group(0)
    return None


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return dateparser.parse(value)
    except (ValueError, OverflowError):
        return None


def _aware(dt: datetime) -> datetime:
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
