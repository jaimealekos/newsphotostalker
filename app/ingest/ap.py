"""Associated Press adapter (AP Newsroom).

VERIFIED AGAINST THE LIVE SERVICE (2026-07): AP Newsroom exposes an anonymous
search API that needs no authentication, so this adapter uses plain HTTP —
no browser required. The verified contract:

* Search:
    POST https://api.newsroom.ap.org/v1/nrsearch/anonymous/search
    JSON body: {"Query": ..., "MediaTypes": ["photo"], "PageNumber": N,
                "PageSize": 50, "Sort": ["firstcreated:desc"], ...}
  The Query string supports an Elasticsearch-style field DSL; photographer
  searches use  photographer.name:"Emilio Morenatti"  (checked: returns only
  that photographer's images, newest first). Free text goes in as-is.
* Results: Elasticsearch envelope; each Items[i]._source carries itemid,
  title (slug), caption.nitf (HTML), photographer{name}, firstcreated (ISO),
  renditions, etc.
* Images (watermarked previews, hotlinkable without auth):
    https://mapi.associatedpress.com/v2/items/{itemid}/preview/AP.jpg?wm=api    (~1024px)
    https://mapi.associatedpress.com/v2/items/{itemid}/thumbnail/AP.jpg?wm=api  (~125px)
  The preview keeps the full EXIF/IPTC block (caption + credit).

Data quality note: a small number of index entries carry corrupt future
``firstcreated`` dates (e.g. year 2060). Those would poison the novelty
cursor (max captured_at), so dates further than a day in the future fall back
to ``arrivaldatetime`` / ``itemstartdatetime`` or None.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from dateutil import parser as dateparser

from .base import RawAsset
from .http_base import HttpAdapter, HttpAdapterError
from .fetcher import FetchError, fetch

SEARCH_URL = "https://api.newsroom.ap.org/v1/nrsearch/anonymous/search"
IMG_BASE = "https://mapi.associatedpress.com/v2/items"

_TAG_RE = re.compile(r"<[^>]+>")


class APAdapter(HttpAdapter):
    agency = "ap"
    requires_login = False

    PAGE_SIZE = 50
    MAX_PAGES = 3

    def build_query(self, kind: str, query: str) -> str:
        if kind == "photographer":
            return f'photographer.name:"{query}"'
        return query

    def search(self, *, kind, query, since, limit=100):
        assets: list[RawAsset] = []
        seen: set[str] = set()

        for page in range(1, self.MAX_PAGES + 1):
            payload = {
                "Query": self.build_query(kind, query),
                "MediaTypes": ["photo"],
                "PageNumber": page,
                "MixedMediaPageNumber": page,
                "PageSize": self.PAGE_SIZE,
                "Sort": ["firstcreated:desc"],
                "Date": "Anytime",
                "DateLabel": "Anytime",
            }
            data = self._post_json(payload)
            items = data.get("Items") or []
            if not items:
                break

            page_assets = []
            for item in items:
                asset = self._parse_item(item, kind, query)
                if asset and asset.external_id not in seen:
                    seen.add(asset.external_id)
                    page_assets.append(asset)
            assets.extend(page_assets)

            # newest-first: stop paging once we're past the cursor
            oldest = min((a.captured_at for a in page_assets if a.captured_at), default=None)
            if since is not None and oldest is not None and oldest <= _aware(since):
                break
            if len(assets) >= limit or page >= data.get("TotalPages", 1):
                break
            self.polite_pause()

        if since is not None:
            assets = [a for a in assets if a.captured_at is None or a.captured_at > _aware(since)]
        return assets[:limit]

    def _post_json(self, payload: dict) -> dict:
        try:
            body = fetch(
                SEARCH_URL,
                method="POST",
                data=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "Referer": "https://newsroom.ap.org/",
                    "Origin": "https://newsroom.ap.org",
                },
            )
        except FetchError as exc:
            raise HttpAdapterError(f"AP search failed: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise HttpAdapterError(f"AP search returned non-JSON ({body[:120]!r})") from exc

    def _parse_item(self, item: dict, kind: str, query: str) -> RawAsset | None:
        src = item.get("_source") or {}
        itemid = src.get("itemid") or item.get("_id")
        if not itemid:
            return None

        caption_html = (src.get("caption") or {}).get("nitf") or ""
        caption = _TAG_RE.sub(" ", caption_html).strip() or None
        photographer = (src.get("photographer") or {}).get("name")
        title = src.get("title") or src.get("headline")

        return RawAsset(
            external_id=itemid,
            agency=self.agency,
            title=title,
            caption=caption,
            photographer=photographer,
            credit=f"{photographer}/AP" if photographer else "AP",
            captured_at=_safe_date(src),
            keywords=[query] + ([title] if title else []),
            # Los tres parámetros hacen falta, y ninguno es evidente (el porqué,
            # con las líneas de su código, en app/enlaces.py):
            #   query      el término. `st` NO lo es: es el tipo de búsqueda.
            #   mediaType  sin él la página no pinta nada aunque encuentre.
            #   st=keyword lo que pone su propia interfaz.
            detail_url=(
                "https://newsroom.ap.org/editorial-photos-videos/search"
                f"?query={itemid}&mediaType=photo&st=keyword"
            ),
            # La rendition /thumbnail/ de AP es ~125px y se ve pixelada; como
            # en el resto de agencias, la miniatura reutiliza la preview.
            thumbnail_url=None,
            preview_url=f"{IMG_BASE}/{itemid}/preview/AP.jpg?wm=api",
            raw_metadata={
                "source": "newsroom.ap.org",
                "itemid": itemid,
                "search_kind": kind,
                "search_query": query,
                "firstcreated": src.get("firstcreated"),
                "provider": (src.get("provider") or {}).get("name"),
                "signals": src.get("signals"),
                "dateline": src.get("dateline"),
            },
        )


def _safe_date(src: dict) -> datetime | None:
    """Parse the capture date, guarding against corrupt future dates."""
    horizon = datetime.now(timezone.utc) + timedelta(days=1)
    for field in ("firstcreated", "arrivaldatetime", "itemstartdatetime"):
        raw = src.get(field)
        if not raw:
            continue
        try:
            dt = dateparser.parse(raw)
        except (ValueError, OverflowError, TypeError):
            continue
        if dt is None:
            continue
        dt = _aware(dt)
        if dt <= horizon:
            return dt
    return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
