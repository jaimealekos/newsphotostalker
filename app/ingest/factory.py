"""Adapter factory: pick the right adapter for an agency + mode."""

from __future__ import annotations

from .base import BaseAdapter
from .mock import MockAdapter


def get_adapter(agency: str, settings) -> BaseAdapter:
    """Return an adapter instance for ``agency``.

    In ``mock`` mode every agency uses :class:`MockAdapter`. In ``live`` mode
    each agency uses its real scraper; AFP and Getty both use the Getty
    distribution adapter (with the concrete agency passed through).
    """
    creds = settings.credentials_for(agency)

    if not settings.is_live:
        return MockAdapter(settings, creds, agency=agency)

    if agency == "ap":
        from .ap import APAdapter

        return APAdapter(settings, creds)
    if agency == "reuters":
        from .reuters import ReutersAdapter

        return ReutersAdapter(settings, creds)
    if agency in ("getty", "afp"):
        from .getty import GettyAdapter

        return GettyAdapter(settings, creds, agency=agency)

    raise ValueError(f"Unknown agency: {agency}")
