from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .enums import OfficialRaceStatus, RegistrationStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OfficialRace(db.Model):
    __tablename__ = "official_races"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    preset_id: Mapped[int | None] = mapped_column(
        ForeignKey("race_presets.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OfficialRaceStatus.DRAFT, index=True
    )
    organizer_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_opens_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    room_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    room_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    max_players: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    preset = relationship("RacePreset", lazy="joined")
    season = relationship("Season", lazy="joined")
    organizer = relationship("User", lazy="joined", foreign_keys=[organizer_user_id])

    def __repr__(self) -> str:
        return f"<OfficialRace {self.id} {self.name!r} status={self.status}>"


class OfficialRaceRegistration(db.Model):
    __tablename__ = "official_race_registrations"
    __table_args__ = (
        UniqueConstraint(
            "official_race_id", "user_id", name="uq_official_race_registrations_race_user"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    official_race_id: Mapped[int] = mapped_column(
        ForeignKey("official_races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RegistrationStatus.REGISTERED
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user = relationship("User", lazy="joined")


class OfficialRaceResult(db.Model):
    __tablename__ = "official_race_results"
    __table_args__ = (
        UniqueConstraint(
            "official_race_id", "user_id", name="uq_official_race_results_race_user"
        ),
        UniqueConstraint(
            "official_race_id", "placement", name="uq_official_race_results_race_placement"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    official_race_id: Mapped[int] = mapped_column(
        ForeignKey("official_races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uma_character_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_characters.id", ondelete="SET NULL"), nullable=True
    )
    uma_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    placement: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    speed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stamina: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wisdom: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user = relationship("User", lazy="joined", foreign_keys=[user_id])
    uma_character = relationship("UmaCharacter", lazy="joined")
