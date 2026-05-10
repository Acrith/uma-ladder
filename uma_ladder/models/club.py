from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Club(db.Model):
    """First-class uma.moe circle (club) cache (PR-M1).

    Promotes the bare ``circle_id`` integer that ``UserProfile.club_id``
    has been mirroring (PR-L1) into a real metadata cache. Unlocks
    per-club surfaces (member roster on ``/clubs/<id>``, future
    per-club ladder, multi-club race allowlist) without each render
    having to fish ``circle_name`` out of the ``uma_moe_cache`` JSON
    blob keyed by friend_code.

    The primary key IS uma.moe's ``circle_id`` — it's already
    globally unique, and using it directly avoids an extra synthetic
    ``id`` that would complicate JOINs from ``UserProfile.club_id``
    and the future race-allowlist FK.

    Note ``UserProfile.club_id`` deliberately stays as a bare
    integer (no FK constraint): it's a snapshot from the uma.moe
    trainer cache, the authoritative state lives upstream, and a
    user whose profile sync precedes the corresponding club sync
    still has a sensible mirror even if we haven't ingested the
    Club row yet. ``Club`` is a metadata cache; ``UserProfile.club_id``
    is "what uma.moe said about this user." Different concerns;
    keep them independent.
    """

    __tablename__ = "clubs"

    # uma.moe circle_id is the canonical identifier — no synthetic id.
    circle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # Optional capacity for future fields (member_count, total_fans,
    # etc.) without another migration. Stays nullable so a partial
    # sync from a TrainerSummary that doesn't carry it doesn't fail.
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<Club circle_id={self.circle_id} name={self.name!r}>"
