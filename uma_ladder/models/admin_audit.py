from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AdminAuditLog(db.Model):
    """One row per privileged action.

    Designed as a read-mostly compliance trail — never updated after
    insert. ``before_json`` / ``after_json`` are free-form snapshots
    of the relevant slice of state (e.g. ``{"role": "user"}`` →
    ``{"role": "organizer"}`` for a role change). ``details`` is
    free-form text context (e.g. forfeit reason).

    FKs are SET NULL so deleting a user keeps the audit trail intact
    with the now-anonymous reference visible.
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Short machine-friendly verb. Examples: "role_change",
    # "official_race_cancel", "draft_match_cancel", "draft_match_forfeit".
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional FK-light reference to the entity touched (e.g. race id,
    # match id) for filterability. We don't FK it because the entity
    # may live in any of several tables.
    target_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    actor = relationship("User", lazy="joined", foreign_keys=[actor_user_id])
    target_user = relationship(
        "User", lazy="joined", foreign_keys=[target_user_id]
    )

    def __repr__(self) -> str:
        return (
            f"<AdminAuditLog {self.id} {self.action} "
            f"actor={self.actor_user_id} target={self.target_user_id}>"
        )
