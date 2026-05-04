from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .enums import DraftMatchStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DraftMatch(db.Model):
    __tablename__ = "draft_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DraftMatchStatus.WAITING_FOR_OPPONENT, index=True
    )
    host_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opponent_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    join_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    umas_per_player: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    preset_pool: Mapped[str] = mapped_column(String(16), nullable=False, default="custom")
    selected_preset_id: Mapped[int | None] = mapped_column(
        ForeignKey("race_presets.id", ondelete="SET NULL"), nullable=True
    )
    host_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opponent_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    room_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    room_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    winner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    loser_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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

    host = relationship("User", lazy="joined", foreign_keys=[host_user_id])
    opponent = relationship("User", lazy="joined", foreign_keys=[opponent_user_id])
    selected_preset = relationship("RacePreset", lazy="joined")
    season = relationship("Season", lazy="joined")

    def __repr__(self) -> str:
        return f"<DraftMatch {self.id} status={self.status}>"


class DraftMatchBan(db.Model):
    __tablename__ = "draft_match_bans"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_match_id: Mapped[int] = mapped_column(
        ForeignKey("draft_matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ban_type: Mapped[str] = mapped_column(String(32), nullable=False)
    uma_character_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_characters.id", ondelete="SET NULL"), nullable=True
    )
    uma_outfit_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_outfits.id", ondelete="SET NULL"), nullable=True
    )
    condition_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    uma_character = relationship("UmaCharacter", lazy="joined")
    uma_outfit = relationship("UmaOutfit", lazy="joined")


class DraftRaceResult(db.Model):
    __tablename__ = "draft_race_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_match_id: Mapped[int] = mapped_column(
        ForeignKey("draft_matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uma_character_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_characters.id", ondelete="SET NULL"), nullable=True
    )
    uma_outfit_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_outfits.id", ondelete="SET NULL"), nullable=True
    )
    custom_uma_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    placement: Mapped[int] = mapped_column(Integer, nullable=False)
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

    user = relationship("User", lazy="joined", foreign_keys=[user_id])
    uma_character = relationship("UmaCharacter", lazy="joined")
    uma_outfit = relationship("UmaOutfit", lazy="joined")


class DraftEloChange(db.Model):
    __tablename__ = "draft_elo_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_match_id: Mapped[int] = mapped_column(
        ForeignKey("draft_matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opponent_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating_before: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_after: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
