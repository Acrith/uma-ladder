from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .enums import PresetSource


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RacePreset(db.Model):
    """One row per *race*, not per track configuration.

    The earlier design unique-keyed on (venue, surface, distance_meters,
    direction, course_variant) — fine until you noticed Tokyo Yushun +
    Japanese Oaks + Japan Cup all run on Tokyo 2400m turf left. They
    are three different G1s sharing one physical configuration. The
    constraint silently dropped two of them at fetch / seed time.

    PR-G2 relaxed the constraint. Random-preset selection (draft) now
    picks across all rows; CM moderators can pick the *race* they
    actually mean instead of just the track. Custom presets keep their
    own dedupe semantics in services/seed_presets.py — those are
    track configs by design and don't carry distinct race names.
    """

    __tablename__ = "race_presets"
    __table_args__ = (
        # Non-unique composite index: lets the random-preset filter
        # (services/draft.filter_presets) prune candidate rows by track
        # condition without a sequential scan. No uniqueness — multiple
        # rows can share a physical configuration.
        Index(
            "ix_race_presets_track_config",
            "venue",
            "surface",
            "distance_meters",
            "direction",
            "course_variant",
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
