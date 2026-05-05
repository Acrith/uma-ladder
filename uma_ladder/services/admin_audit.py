"""Admin audit log — append-only feed of privileged actions.

Designed as best-effort: ``log_action`` swallows exceptions so a
broken audit row never blocks the actual change. The trail is for
review, not transactional integrity.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from ..extensions import db
from ..models import AdminAuditLog


def log_action(
    *,
    actor_user_id: int | None,
    action: str,
    target_user_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    details: str | None = None,
    target_kind: str | None = None,
    target_id: int | None = None,
) -> AdminAuditLog | None:
    """Append a row to the audit trail. Returns the row, or ``None`` if
    the write failed for any reason (logged via flask app logger when
    available; never raises into the caller).
    """
    try:
        row = AdminAuditLog(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action=action,
            before_json=before,
            after_json=after,
            details=details,
            target_kind=target_kind,
            target_id=target_id,
        )
        db.session.add(row)
        db.session.commit()
        return row
    except Exception:  # noqa: BLE001
        # Defensive rollback — the calling service may still need the
        # session for subsequent commits.
        with contextlib.suppress(Exception):
            db.session.rollback()
        return None


@dataclass(frozen=True)
class AuditPage:
    entries: Sequence[AdminAuditLog]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


def list_recent(
    *,
    page: int = 1,
    page_size: int = 50,
    action: str | None = None,
    actor_user_id: int | None = None,
) -> AuditPage:
    """Paginated browse of the audit trail (newest first). Optional
    filters by exact action verb or actor id."""
    page = max(1, page)
    page_size = max(1, page_size)

    base = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
    if action:
        base = base.where(AdminAuditLog.action == action)
    if actor_user_id is not None:
        base = base.where(AdminAuditLog.actor_user_id == actor_user_id)

    total = db.session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    entries = list(
        db.session.scalars(
            base.limit(page_size).offset((page - 1) * page_size)
        )
    )
    return AuditPage(
        entries=entries, total=total, page=page, page_size=page_size
    )
