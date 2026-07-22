"""Filesystem storage for downloaded media.

Layout::

    data/media/<agency>/<search_id>/<safe_external_id>/preview.jpg
                                                       /thumb.jpg
                                                       /metadata.json

Paths stored in the DB are relative to ``settings.media_dir`` so the gallery
can serve them from a single mounted static route.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_id(external_id: str) -> str:
    return _SAFE.sub("_", external_id)[:120]


def asset_dir(agency: str, search_id: int, external_id: str) -> Path:
    settings = get_settings()
    d = settings.media_dir / agency / str(search_id) / safe_id(external_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def to_relative(path: str | None) -> str | None:
    """Store paths relative to media_dir for stable serving."""
    if not path:
        return None
    settings = get_settings()
    try:
        return str(Path(path).resolve().relative_to(settings.media_dir.resolve()))
    except ValueError:
        return path


def to_absolute(rel_path: str | None) -> Path | None:
    if not rel_path:
        return None
    return get_settings().media_dir / rel_path


def write_metadata(directory: Path, metadata: dict) -> None:
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))


def remove_asset_files(*rel_paths: str | None) -> None:
    """Delete an asset's files (and its now-empty directory)."""
    dirs = set()
    for rel in rel_paths:
        abs_path = to_absolute(rel)
        if abs_path and abs_path.exists():
            dirs.add(abs_path.parent)
            abs_path.unlink(missing_ok=True)
    for d in dirs:
        meta = d / "metadata.json"
        meta.unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            pass  # not empty; leave it
