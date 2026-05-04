from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .enums import PresetSource


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RacePreset(db.Model):
    __tablename__ = "race_presets"
    __table_args__ = (
        UniqueConstraint(
            "venue",
            "surface",
            "distance_meters",
            "direction",
            "course_variant",
            name="uq_race_presets_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PresetSource.CUSTOM_BUILTIN, index=True
    )
    external_source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    venue: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    surface: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    distance_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_category: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    course_variant: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_runners: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<RacePreset {self.name} {self.distance_meters}m>"
