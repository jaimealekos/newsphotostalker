"""Shared plumbing for HTTP-based live adapters (no browser needed).

Getty (and AFP through it) serve fully server-rendered search pages, so plain
HTTP requests are enough — much lighter and more reliable on a server than
driving Chromium. Fetching goes through :mod:`.fetcher` (system curl), which
is the one HTTP stack Getty's bot protection consistently allows.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import BaseAdapter, DownloadedFiles, RawAsset
from .fetcher import FetchError, fetch, fetch_text


class HttpAdapterError(RuntimeError):
    pass


class HttpAdapter(BaseAdapter):
    """Base class for adapters that scrape via plain HTTP requests."""

    #: seconds to sleep between consecutive page fetches (politeness)
    PAGE_DELAY = 1.5

    def open(self) -> None:  # nothing to acquire
        pass

    def close(self) -> None:
        pass

    def get_html(self, url: str) -> str:
        try:
            return fetch_text(url)
        except FetchError as exc:
            raise HttpAdapterError(f"{self.agency}: {exc}") from exc

    def polite_pause(self) -> None:
        time.sleep(self.PAGE_DELAY)

    # -- download ----------------------------------------------------------
    def download(self, asset: RawAsset, dest_dir) -> DownloadedFiles:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        preview_path = thumb_path = None
        total = 0

        preview_url = asset.preview_url
        thumb_url = asset.thumbnail_url or preview_url
        body = None
        if preview_url:
            body = self._fetch_bytes(preview_url)
            preview_path = dest / "preview.jpg"
            preview_path.write_bytes(body)
            total += len(body)
        if thumb_url:
            thumb_path = dest / "thumb.jpg"
            if thumb_url == preview_url and body is not None:
                # Same source file — reuse bytes instead of re-downloading.
                thumb_path.write_bytes(body)
                total += len(body)
            else:
                tbody = self._fetch_bytes(thumb_url)
                thumb_path.write_bytes(tbody)
                total += len(tbody)

        if total == 0:
            raise HttpAdapterError(f"{self.agency}: no downloadable image for {asset.external_id}")
        return DownloadedFiles(
            preview_path=str(preview_path) if preview_path else None,
            thumbnail_path=str(thumb_path) if thumb_path else None,
            file_bytes=total,
        )

    def _fetch_bytes(self, url: str) -> bytes:
        try:
            return fetch(url)
        except FetchError as exc:
            raise HttpAdapterError(f"{self.agency}: {exc}") from exc
