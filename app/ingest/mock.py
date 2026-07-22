"""Mock adapter.

Generates realistic synthetic assets (with placeholder JPEGs drawn by Pillow)
so the *entire* system — scheduler, storage, retention, control panel and
gallery — can be exercised end-to-end without touching the real agency
services. Selected when ``mode: mock`` in the config.

The generated pool is deterministic per (agency, query) so repeated runs do not
grow without bound; de-duplication by ``external_id`` keeps runs idempotent.
The pool intentionally spans ~100 days so that, with a 3-month retention
window, the oldest few assets get purged — demonstrating the purge logic.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .base import BaseAdapter, DownloadedFiles, RawAsset

AGENCY_COLORS = {
    "ap": (196, 30, 58),        # AP red
    "reuters": (255, 130, 0),   # Reuters orange
    "afp": (0, 45, 114),        # AFP blue
    "getty": (20, 20, 20),      # Getty near-black
}

AGENCY_LABEL = {
    "ap": "ASSOCIATED PRESS",
    "reuters": "REUTERS",
    "afp": "AFP",
    "getty": "GETTY IMAGES",
}

# Pools of plausible caption fragments per topic keyword; falls back to generic.
CAPTION_BITS = [
    "General view during",
    "A person walks past",
    "People gather at",
    "Detail of",
    "Aftermath of",
    "Supporters celebrate at",
    "Emergency services attend",
    "A demonstrator holds a sign during",
    "Local residents react to",
    "Workers prepare for",
]

LOCATIONS = ["Madrid", "Barcelona", "Sevilla", "Toledo", "Valencia", "Zaragoza"]

# Photographers used to populate text searches (photographer searches use the
# query itself as the name).
TEXT_PHOTOGRAPHERS = {
    "ap": ["Manu Fernandez", "Bernat Armangue", "Paul White", "Andrea Comas"],
    "reuters": ["Juan Medina", "Vincent West", "Nacho Doce", "Violeta Santos Moura"],
    "afp": ["Pierre-Philippe Marcou", "Gabriel Bouys", "Josep Lago", "Cesar Manso"],
    "getty": ["David Ramos", "Denis Doyle", "Pablo Blazquez Dominguez", "Diego Radames"],
}


def _seed_int(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:12], 16)


def _rng_sequence(seed: int, n: int) -> list[int]:
    """Deterministic pseudo-random ints derived from a seed (no global state)."""
    out = []
    x = seed
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append(x)
    return out


class MockAdapter(BaseAdapter):
    agency = "mock"
    requires_login = False

    #: how many synthetic assets to generate per search
    POOL_SIZE = 24
    #: days the pool spans (some fall outside a 3-month window on purpose)
    POOL_SPAN_DAYS = 100

    def __init__(self, settings, credentials, agency: str):
        super().__init__(settings, credentials)
        self.agency = agency

    # -- generation --------------------------------------------------------
    def _generate_pool(self, kind: str, query: str) -> list[RawAsset]:
        now = datetime.now(timezone.utc)
        seed = _seed_int(self.agency, kind, query)
        rnd = _rng_sequence(seed, self.POOL_SIZE * 5)
        assets: list[RawAsset] = []

        for i in range(self.POOL_SIZE):
            base = i * 5
            days_ago = (rnd[base] % (self.POOL_SPAN_DAYS * 24)) / 24.0
            captured = now - timedelta(days=days_ago)

            if kind == "photographer":
                photographer = query
            else:
                pool = TEXT_PHOTOGRAPHERS.get(self.agency, ["Staff Photographer"])
                photographer = pool[rnd[base + 1] % len(pool)]

            location = LOCATIONS[rnd[base + 2] % len(LOCATIONS)]
            bit = CAPTION_BITS[rnd[base + 3] % len(CAPTION_BITS)]
            topic = query if kind == "text" else "daily life"
            caption = f"{bit} {topic} in {location}, Spain."
            title = f"{topic.title()} — {location}"

            ext_id = f"{self.agency.upper()}-{seed:012x}-{i:03d}"
            keywords = [location, "Spain", topic]
            if kind == "text":
                keywords += query.split()

            assets.append(
                RawAsset(
                    external_id=ext_id,
                    agency=self.agency,
                    title=title,
                    caption=caption,
                    photographer=photographer,
                    credit=f"{photographer}/{AGENCY_LABEL.get(self.agency, self.agency.upper())}",
                    captured_at=captured,
                    keywords=keywords,
                    detail_url=f"https://example.invalid/{self.agency}/{ext_id}",
                    thumbnail_url=None,
                    preview_url=None,
                    raw_metadata={
                        "mock": True,
                        "agency": self.agency,
                        "search_kind": kind,
                        "search_query": query,
                        "distributor": "AFP" if self.agency == "afp" else self.agency.upper(),
                        "orientation": "landscape",
                    },
                )
            )
        return assets

    def search(self, *, kind, query, since, limit=100):
        pool = self._generate_pool(kind, query)
        if since is not None:
            since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            pool = [a for a in pool if a.captured_at and a.captured_at > since_utc]
        pool.sort(key=lambda a: a.captured_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return pool[:limit]

    # -- download ----------------------------------------------------------
    def download(self, asset: RawAsset, dest_dir) -> DownloadedFiles:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        preview = dest / "preview.jpg"
        thumb = dest / "thumb.jpg"

        color = AGENCY_COLORS.get(asset.agency, (60, 60, 60))
        self._render(asset, preview, size=(1024, 683), bg=color)
        self._render(asset, thumb, size=(400, 267), bg=color, small=True)

        total = preview.stat().st_size + thumb.stat().st_size
        return DownloadedFiles(
            preview_path=str(preview),
            thumbnail_path=str(thumb),
            file_bytes=total,
        )

    def _render(self, asset: RawAsset, path: Path, size, bg, small=False):
        img = Image.new("RGB", size, bg)
        draw = ImageDraw.Draw(img)
        try:
            font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 28 if small else 46)
            font_sm = ImageFont.truetype("DejaVuSans.ttf", 14 if small else 22)
        except OSError:
            font_big = ImageFont.load_default()
            font_sm = ImageFont.load_default()

        label = AGENCY_LABEL.get(asset.agency, asset.agency.upper())
        draw.rectangle([0, 0, size[0], 48 if not small else 30], fill=(0, 0, 0))
        draw.text((16, 10 if not small else 6), label, fill=(255, 255, 255), font=font_big if not small else font_sm)

        y = size[1] - (120 if not small else 78)
        draw.rectangle([0, y - 12, size[0], size[1]], fill=(0, 0, 0, 180))
        wrapped = _wrap((asset.caption or asset.title or ""), 46 if not small else 40)
        draw.text((16, y), wrapped, fill=(240, 240, 240), font=font_sm)
        cap_date = asset.captured_at.strftime("%Y-%m-%d") if asset.captured_at else ""
        draw.text(
            (16, size[1] - (28 if not small else 18)),
            f"{asset.photographer or ''}  •  {cap_date}",
            fill=(200, 200, 200),
            font=font_sm,
        )
        img.save(path, "JPEG", quality=82)


def _wrap(text: str, width: int) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:4])
