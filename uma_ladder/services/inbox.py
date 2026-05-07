"""User-facing notification inbox (PR-J12).

The single point that domain code (services/draft.py etc.) calls
to put a notification in front of a user. Discord-webhook fan-out
in `notifications/services.py` is a separate, parallel concern —
inbox is in-app, that one is out-of-app.

Designed event-agnostic: future event kinds add a constant here +
a render branch in the inbox template + a fan-out call from the
domain layer. The model has no enum gate so add-a-kind doesn't
need a migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from ..extensions import db
from ..models import DraftMatchInvite, UserNotification

# Event kind constants — keep these in sync with the inbox
# template's render branches. New kinds: append here, add a
# `{% elif n.kind == ... %}` block, and call `create(...)` from
# the domain service.
KIND_DRAFT_INVITE = "draft_invite"


def create(
    *,
    user_id: int,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> UserNotification:
    """Drop a notification into a user's inbox. The payload shape
    is per-kind — see the template for what each render branch
    expects."""
    notif = UserNotification(
        user_id=user_id,
        kind=kind,
        payload_json=payload or {},
    )
    db.session.add(notif)
    db.session.commit()
    return notif


def list_for_user(user_id: int, *, limit: int = 50) -> Sequence[UserNotification]:
    """Newest-first slice of the user's inbox. 50 is plenty for
    the MVP — paginate later if anyone hits the cap."""
    return list(
        db.session.scalars(
            select(UserNotification)
            .where(UserNotification.user_id == user_id)
            .order_by(UserNotification.created_at.desc())
            .limit(limit)
        )
    )


def unread_count_for_user(user_id: int) -> int:
    """Drives the navbar badge. Cheap query covered by the
    user_id+read_at composite index."""
    return int(
        db.session.scalar(
            select(func.count(UserNotification.id))
            .where(UserNotification.user_id == user_id)
            .where(UserNotification.read_at.is_(None))
        )
        or 0
    )


def mark_all_read_for_user(user_id: int) -> int:
    """Visiting /inbox marks the whole inbox read in one query.
    Returns count touched so callers can flash "N notifications
    cleared" if they want."""
    now = datetime.now(UTC)
    result = db.session.execute(
        update(UserNotification)
        .where(UserNotification.user_id == user_id)
        .where(UserNotification.read_at.is_(None))
        .values(read_at=now)
    )
    db.session.commit()
    return int(result.rowcount or 0)


# ---------- Draft-invite plumbing ----------


def create_for_draft_invite(invite: DraftMatchInvite) -> UserNotification:
    """Fan-out from `services/draft.invite_to_match`. Keeps the
    payload tight — inviter username + match id are enough for the
    template; everything else is recoverable from the link."""
    inviter = invite.inviter
    return create(
        user_id=invite.invitee_user_id,
        kind=KIND_DRAFT_INVITE,
        payload={
            "match_id": invite.draft_match_id,
            "inviter_username": inviter.username if inviter else "?",
            "invite_id": invite.id,
        },
    )


def delete_for_draft_invite(invite_id: int) -> int:
    """When an invite is accepted / declined / cancelled, the
    notification stops being actionable and would clutter the
    inbox. We load + filter in Python rather than using JSON-path
    SQL — the predicate isn't portable across SQLite/Postgres,
    and the candidate set (kind = draft_invite) is bounded and
    small in practice."""
    candidates = db.session.scalars(
        select(UserNotification).where(
            UserNotification.kind == KIND_DRAFT_INVITE
        )
    ).all()
    deleted = 0
    for n in candidates:
        if (n.payload_json or {}).get("invite_id") == invite_id:
            db.session.delete(n)
            deleted += 1
    if deleted:
        db.session.commit()
    return deleted
