"""Layer-A invite model — host sends a draft-match invite to a
specific user by username. Invitee sees a banner and accepts /
declines. No presence tracking, no Discord DM (Layers B / C are
deferred backlog).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .enums import DraftInviteStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DraftMatchInvite(db.Model):
    __tablename__ = "draft_match_invites"
    __table_args__ = (
        UniqueConstraint(
            "draft_match_id",
            "invitee_user_id",
            name="uq_draft_match_invites_match_invitee",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_match_id: Mapped[int] = mapped_column(
        ForeignKey("draft_matches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inviter_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invitee_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=DraftInviteStatus.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    match = relationship("DraftMatch", lazy="joined")
    inviter = relationship(
        "User", lazy="joined", foreign_keys=[inviter_user_id]
    )
    invitee = relationship(
        "User", lazy="joined", foreign_keys=[invitee_user_id]
    )

    def __repr__(self) -> str:
        return (
            f"<DraftMatchInvite id={self.id} "
            f"match={self.draft_match_id} status={self.status}>"
        )
