"""User-facing inbox (PR-J12).

Distinct from `notifications.py` — that table is the audit log for
outbound *Discord* webhook calls, admin-gated. This one is the
per-user inbox for in-app notifications: draft invites, race
reminders, eventually chat alerts. Designed event-agnostic so future
fan-out only registers a new `kind`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UserNotification(db.Model):
    __tablename__ = "user_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Free-form event tag — UPPERCASE-ish convention recommended
    # for new kinds so they're easy to grep. Today: `draft_invite`.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # Whatever the rendering template needs. For draft_invite:
    # {"inviter_username": "...", "match_id": 42}.
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user = relationship("User", lazy="joined")

    __table_args__ = (
        # Hot-path query is "unread for this user" — index covers
        # both the user filter and the read_at IS NULL predicate.
        Index(
            "ix_user_notifications_user_unread",
            "user_id",
            "read_at",
        ),
    )
