"""Adapter interface shared by every agency scraper.

An adapter knows how to:
  * ``search(...)`` a service for assets matching a query, optionally only
    those newer than a cursor (the "novedades" / new-since-last-run logic).
  * ``download(...)`` the preview image + thumbnail for one asset.

Each adapter returns lightweight :class:`RawAsset` objects; persistence,
de-duplication and retention are handled by the runner, not the adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawAsset:
    """A single search result, before it is stored."""

    external_id: str
    agency: str
    title: str | None = None
    caption: str | None = None
    photographer: str | None = None
    credit: str | None = None
    captured_at: datetime | None = None
    keywords: list[str] = field(default_factory=list)
    detail_url: str | None = None
    thumbnail_url: str | None = None
    preview_url: str | None = None
    raw_metadata: dict = field(default_factory=dict)


@dataclass
class DownloadedFiles:
    preview_path: str | None
    thumbnail_path: str | None
    file_bytes: int


class BaseAdapter(ABC):
    """Common surface for all agency adapters."""

    agency: str = "base"
    #: whether this adapter requires a login before searching
    requires_login: bool = False

    def __init__(self, settings, credentials):
        self.settings = settings
        self.credentials = credentials

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "BaseAdapter":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        """Acquire any resources (browser, session, login). Override as needed."""

    def close(self) -> None:
        """Release resources. Override as needed."""

    # -- required ----------------------------------------------------------
    @abstractmethod
    def search(
        self,
        *,
        kind: str,
        query: str,
        since: datetime | None,
        limit: int = 100,
    ) -> list[RawAsset]:
        """Return assets matching ``query``.

        ``kind`` is ``"photographer"`` or ``"text"``. When ``since`` is given,
        only assets newer than that timestamp should be returned.
        """

    @abstractmethod
    def download(self, asset: RawAsset, dest_dir) -> DownloadedFiles:
        """Download the preview + thumbnail for ``asset`` into ``dest_dir``."""
