"""PR-Q3b — community moderation reports service.

Three actions: file (any signed-in user), action / dismiss (admin
only, route-gated). A derived `dismissed_count_for_reporter` query
powers the admin queue's "N prior dismissed" credibility chip
without needing a per-user denormalised counter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from ..extensions import db
from ..models import Report, ReportStatus, User


class ReportError(Exception):
    pass


class SelfReportError(ReportError):
    """Refusing a user's attempt to report themselves."""


class TargetNotFoundError(ReportError):
    """The reported user doesn't exist (or is hard-deleted)."""


class ReportNotFoundError(ReportError):
    pass


class AlreadyResolvedError(ReportError):
    """A report that's already actioned or dismissed can't be
    transitioned again — keep the audit trail clean."""


MAX_REASON_CHARS = 500


def file_report(
    *,
    reporter: User,
    reported_user_id: int,
    reason: str,
    context: str | None = None,
) -> Report:
    """Create a new open report. Caller (route) handles rate
    limiting via flask-limiter; we just enforce the basic content
    rules here.

    Guards:
    - Cannot report yourself.
    - Target must exist. (Soft-deleted users are still reportable —
      a disabled user might still need an admin follow-up like
      "delete their data permanently" via the hard-delete path.)
    - Reason is required, capped to MAX_REASON_CHARS.
    """
    if reporter.id == reported_user_id:
        raise SelfReportError()

    target = db.session.get(User, reported_user_id)
    if target is None:
        raise TargetNotFoundError()

    cleaned = (reason or "").strip()
    if not cleaned:
        raise ReportError("Reason is required.")
    if len(cleaned) > MAX_REASON_CHARS:
        cleaned = cleaned[:MAX_REASON_CHARS]

    cleaned_context = (context or "").strip() or None
    if cleaned_context and len(cleaned_context) > 256:
        cleaned_context = cleaned_context[:256]

    row = Report(
        reporter_user_id=reporter.id,
        reported_user_id=target.id,
        reason=cleaned,
        context=cleaned_context,
        status=ReportStatus.OPEN,
    )
    db.session.add(row)
    db.session.commit()

    from . import admin_audit

    admin_audit.log_action(
        actor_user_id=reporter.id,
        action="report_filed",
        target_user_id=target.id,
        details=cleaned[:200],
        target_kind="report",
        target_id=row.id,
    )
    return row


@dataclass(frozen=True)
class ReportsPage:
    entries: Sequence[Report]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


def list_open(
    *, page: int = 1, page_size: int = 25
) -> ReportsPage:
    """Paginated open reports, newest first. Admin queue uses this
    as its primary feed."""
    page = max(1, page)
    page_size = max(1, page_size)
    base = (
        select(Report)
        .where(Report.status == ReportStatus.OPEN)
        .order_by(Report.created_at.desc())
    )
    total = db.session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    entries = list(
        db.session.scalars(
            base.limit(page_size).offset((page - 1) * page_size)
        )
    )
    return ReportsPage(
        entries=entries, total=total, page=page, page_size=page_size
    )


def _resolve(
    report_id: int,
    *,
    actor: User,
    new_status: str,
    notes: str | None,
) -> Report:
    row = db.session.get(Report, report_id)
    if row is None:
        raise ReportNotFoundError()
    if row.status != ReportStatus.OPEN:
        raise AlreadyResolvedError(f"already {row.status}")

    row.status = new_status
    row.resolved_by_user_id = actor.id
    row.resolved_at = datetime.now(UTC)
    row.resolution_notes = (notes or "").strip() or None
    db.session.commit()

    from . import admin_audit

    admin_audit.log_action(
        actor_user_id=actor.id,
        action=f"report_{new_status}",  # report_actioned / report_dismissed
        target_user_id=row.reported_user_id,
        details=row.resolution_notes,
        target_kind="report",
        target_id=row.id,
    )
    return row


def mark_actioned(
    report_id: int, *, actor: User, notes: str | None = None
) -> Report:
    """Admin confirms a report and took action (disable / ban / DM
    follow-up). The action itself isn't tied to this row — the
    admin runs the disable / hard-delete from the user detail page;
    this just records the closure."""
    return _resolve(
        report_id, actor=actor, new_status=ReportStatus.ACTIONED, notes=notes
    )


def dismiss(
    report_id: int, *, actor: User, notes: str | None = None
) -> Report:
    """Admin determined the report doesn't need action (false
    positive / misunderstanding / bad faith). The reporter's
    `dismissed_count_for_reporter` will increment by 1, surfacing
    on future reports they file as a credibility hint."""
    return _resolve(
        report_id, actor=actor, new_status=ReportStatus.DISMISSED, notes=notes
    )


def dismissed_count_for_reporter(reporter_user_id: int) -> int:
    """How many of this user's reports have been dismissed. Powers
    the credibility chip on the admin queue — admins see "(3 prior
    dismissed)" next to the reporter's name and can weigh the new
    report accordingly. Zero is the common case."""
    return (
        db.session.scalar(
            select(func.count(Report.id))
            .where(Report.reporter_user_id == reporter_user_id)
            .where(Report.status == ReportStatus.DISMISSED)
        )
        or 0
    )
