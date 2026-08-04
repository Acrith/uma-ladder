"""Race captures — structured race data pulled straight from the game
client, replacing the screenshot→OCR→confirm pipeline.

A capture is raw evidence, not a result. It lands here as the tool's
untouched payload, gets normalized into result lines on read, and only
reaches the ladder once a human confirms it — the same gate the OCR
path has always had (PROJECT_INTENTIONS §13).

Schema notes in docs/room-match-extraction.md. The model stays
deliberately permissive (`payload_json`) because the payload shape is
owned by the extractor repo and will churn faster than this schema.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .enums import RaceCaptureSource, RaceCaptureStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RaceCapture(db.Model):
    __tablename__ = "race_captures"

    id: Mapped[int] = mapped_column(primary_key=True)

    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RaceCaptureSource.PACKET_CAPTURE
    )
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The extractor's payload, untouched. Everything below is denormalized
    # out of this for indexing/dedupe — the blob stays the source of truth
    # so a schema change upstream never loses data we already accepted.
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---- Game-side identity (from RoomMatchSavedRoomInfo) ----
    # saved_room_id is the natural idempotency key: in an 11-player room
    # every participant can run the extractor, so the same result gets
    # uploaded several times. Unique (NULLs stay distinct in both SQLite
    # and Postgres, so non-room captures are unaffected).
    saved_room_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, unique=True, index=True
    )
    race_instance_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    room_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    participant_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---- Ladder linkage — set when a human confirms it against a race ----
    official_race_id: Mapped[int | None] = mapped_column(
        ForeignKey("official_races.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    draft_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("draft_matches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=RaceCaptureStatus.PENDING,
        index=True,
    )
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    submitted_by = relationship(
        "User", lazy="joined", foreign_keys=[submitted_by_user_id]
    )
    confirmed_by = relationship(
        "User", lazy="joined", foreign_keys=[confirmed_by_user_id]
    )
    official_race = relationship("OfficialRace", lazy="joined")
    draft_match = relationship("DraftMatch", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<RaceCapture id={self.id} source={self.source} "
            f"room={self.saved_room_id} status={self.status}>"
        )
