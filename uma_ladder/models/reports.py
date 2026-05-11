"""PR-Q3b — Community moderation reports.

A signed-in user files a report against another user. Reports queue
up at /admin/reports for review. Each report passes through one of
three resolutions: open (default), actioned, dismissed. The same
admin who resolves it logs an optional rationale in
`resolution_notes` so a follow-up admin can understand the
decision later.

False-reporter detection is derived (not stored): the queue UI
runs a count query for "how many of this reporter's prior reports
were dismissed?" and surfaces the number as a credibility hint
when the admin reviews a new report.

FKs:
- reporter / reported user_id: CASCADE so a hard-deleted user's
  reports go with them (the report is meaningless without context).
  Soft-deleted users keep their reports — the FK stays valid.
- resolved_by_user_id: SET NULL so deleting an admin doesn't lose
  the resolution row; the resolution stands, the resolver name
  just becomes `@?`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReportStatus:
    OPEN = "open"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


class Report(db.Model):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reported_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Free-form reason from the reporter, capped at 500 chars in
    # the service layer so the admin queue stays scannable.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # URL the reporter was on when they filed — usually a profile
    # page, race detail, or draft match card. Lets the admin jump
    # straight to the context.
    context: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReportStatus.OPEN, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    reporter = relationship(
        "User", lazy="joined", foreign_keys=[reporter_user_id]
    )
    reported_user = relationship(
        "User", lazy="joined", foreign_keys=[reported_user_id]
    )
    resolved_by = relationship(
        "User", lazy="joined", foreign_keys=[resolved_by_user_id]
    )

    def __repr__(self) -> str:
        return (
            f"<Report {self.id} {self.status} "
            f"reporter={self.reporter_user_id} target={self.reported_user_id}>"
        )
