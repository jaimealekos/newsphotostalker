"""Database models: User, Search, Asset, RunLog."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Retention modes
RETENTION_TIME = "time"  # keep only the last N months
RETENTION_SIZE = "size"  # keep total under N megabytes

# Search kinds
KIND_PHOTOGRAPHER = "photographer"
KIND_TEXT = "text"


class User(Base):
    """El usuario del panel. Desde la 1.1 hay exactamente uno: el login sigue
    existiendo, pero ya no hay administradores ni cuentas "hijas"."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    searches: Mapped[list["Search"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    separators: Mapped[list["Separator"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable por la migración desde bases de datos anteriores al login; el
    # arranque adopta las búsquedas huérfanas.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    agency: Mapped[str] = mapped_column(String(20), index=True)  # ap|reuters|afp|getty
    kind: Mapped[str] = mapped_column(String(20))  # photographer|text
    query: Mapped[str] = mapped_column(String(400))

    cadence_minutes: Mapped[int] = mapped_column(Integer, default=360)

    # Retention policy
    retention_mode: Mapped[str] = mapped_column(String(10), default=RETENTION_TIME)
    retention_months: Mapped[int | None] = mapped_column(Integer, nullable=True, default=3)
    retention_mb: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    enabled: Mapped[bool] = mapped_column(default=True)

    # Sitio que ocupa en el panel. El orden lo fija el usuario desde el modo
    # edición y se comparte con los separadores (una sola lista ordenada).
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # Runtime state
    cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Última vez que se abrió ESTA búsqueda para ver sus novedades. La luz del
    # panel se enciende cuando ha entrado alguna foto después de esta marca, así
    # que entrar en una búsqueda solo apaga su propia luz.
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="searches")
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="search", cascade="all, delete-orphan"
    )

    def retention_summary(self) -> str:
        if self.retention_mode == RETENTION_TIME:
            return f"{self.retention_months} meses"
        return f"{self.retention_mb} MB"


class Separator(Base):
    """Línea de separación con título dentro del panel.

    Comparte la escala de ``position`` con las búsquedas: el panel mezcla ambas
    en una sola lista ordenada, y el modo edición reparte las posiciones."""

    __tablename__ = "separators"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    label: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)

    user: Mapped["User"] = relationship(back_populates="separators")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("search_id", "external_id", name="uq_search_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id", ondelete="CASCADE"), index=True)
    agency: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(200), index=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    photographer: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    credit: Mapped[str | None] = mapped_column(String(200), nullable=True)

    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    raw_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    detail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    file_bytes: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    search: Mapped["Search"] = relationship(back_populates="assets")


class AppSettings(Base):
    """Ajustes globales del panel (fila única, id=1)."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    # Cuántas fotos por página en la vista de una búsqueda.
    photos_per_page: Mapped[int] = mapped_column(Integer, default=60)
    # Refresco GLOBAL: todas las búsquedas se ejecutan juntas cada
    # ``refresh_every`` (horas o días) empezando a las HH:MM.
    refresh_every: Mapped[int] = mapped_column(Integer, default=12)
    refresh_unit: Mapped[str] = mapped_column(String(10), default="hours")  # hours|days
    refresh_start_hour: Mapped[int] = mapped_column(Integer, default=6)
    refresh_start_minute: Mapped[int] = mapped_column(Integer, default=0)

    def refresh_summary(self) -> str:
        unit = "hora(s)" if self.refresh_unit == "hours" else "día(s)"
        return (
            f"cada {self.refresh_every} {unit}, "
            f"desde las {self.refresh_start_hour:02d}:{self.refresh_start_minute:02d}"
        )


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|error
    new_assets: Mapped[int] = mapped_column(Integer, default=0)
    purged_assets: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
