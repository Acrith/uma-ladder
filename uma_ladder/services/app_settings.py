"""PR-Q4 — admin-toggleable runtime flags.

Thin wrapper on top of the ``app_settings`` table so feature gates
can be flipped from the admin UI without a deploy. Values are stored
as strings; callers translate to whatever type they want. The
service collapses every truthy spelling we might find ("true", "1",
"yes", "on") down to a single bool — never assume the on-disk value
is exactly "true".

Today only ``invite_only_enabled`` lives here. Future flags follow
the same pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..extensions import db
from ..models import AppSetting

INVITE_ONLY_ENABLED = "invite_only_enabled"

_TRUTHY = {"true", "1", "yes", "on"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_bool(key: str, *, default: bool = False) -> bool:
    """Return a boolean view of ``key``. Missing → ``default``."""
    row = db.session.get(AppSetting, key)
    if row is None:
        return default
    return row.value.strip().lower() in _TRUTHY


def set_bool(
    key: str,
    flag: bool,
    *,
    actor_user_id: int | None = None,
) -> None:
    """Upsert a boolean setting. ``actor_user_id`` is stored as the
    last-updater so the admin audit can answer 'who flipped it'.
    Caller is responsible for audit-logging the action via
    ``admin_audit.log_action`` — this just persists the value."""
    value = "true" if flag else "false"
    row = db.session.get(AppSetting, key)
    if row is None:
        row = AppSetting(
            key=key,
            value=value,
            updated_at=_utcnow(),
            updated_by_user_id=actor_user_id,
        )
        db.session.add(row)
    else:
        row.value = value
        row.updated_at = _utcnow()
        row.updated_by_user_id = actor_user_id
    db.session.commit()


# ─── Convenience wrappers for the invite-only flag ───────────────


def is_invite_only_enabled() -> bool:
    return get_bool(INVITE_ONLY_ENABLED, default=False)


def set_invite_only_enabled(
    flag: bool, *, actor_user_id: int | None = None
) -> None:
    set_bool(INVITE_ONLY_ENABLED, flag, actor_user_id=actor_user_id)
