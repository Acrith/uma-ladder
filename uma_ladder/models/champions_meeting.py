"""Champions Meeting — admin-managed upcoming-event entries.

CMs are JP-game ranked events that run for ~5 days every 2-3 weeks. The
track conditions almost always match a real G1 race we already have in
RacePreset, so each row carries `preset_id` as the load-bearing field.
The optional `override_*` columns are for the rare CM that uses
non-standard conditions — we leave them NULL when the preset matches.

Service-layer `effective_*` helpers do the override resolution so
templates / dashboard widgets read a single source of truth.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .presets import RacePreset
from .users import User


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ChampionsMeeting(db.Model):
    __tablename__ = "champions_meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Optional — CM windows are typically 5 days but the admin may not
    # know the exact end date when forecasting from uma.moe.
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    preset_id: Mapped[int] = mapped_column(
        ForeignKey("race_presets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Override columns — NULL means "inherit from preset". Filled means
    # this CM diverges from the preset for this field only. Surface
    # / direction / distance values are accepted as raw strings so
    # admins can punch through to non-canonical values without us
    # needing a migration to add an enum entry.
    override_venue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    override_surface: Mapped[str | None] = mapped_column(String(8), nullable=True)
    override_distance_meters: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    override_distance_category: Mapped[str | None] = mapped_column(
        String(8), nullable=True
    )
    override_direction: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    preset: Mapped[RacePreset] = relationship("RacePreset", lazy="joined")
    created_by: Mapped[User | None] = relationship("User", lazy="joined")

    # ---- override resolution helpers ----

    @property
    def effective_venue(self) -> str:
        return self.override_venue or self.preset.venue

    @property
    def effective_surface(self) -> str:
        return self.override_surface or self.preset.surface

    @property
    def effective_distance_meters(self) -> int:
        return self.override_distance_meters or self.preset.distance_meters

    @property
    def effective_distance_category(self) -> str:
        return self.override_distance_category or self.preset.distance_category

    @property
    def effective_direction(self) -> str:
        return self.override_direction or self.preset.direction

    @property
    def is_clockwise(self) -> bool:
        """Right-handed = clockwise; left-handed = counterclockwise.
        Straight / stretch tracks are neither — caller should display
        the direction string directly in those cases."""
        return self.effective_direction == "Right"

    def __repr__(self) -> str:
        return f"<ChampionsMeeting {self.name} starts_on={self.starts_on}>"
