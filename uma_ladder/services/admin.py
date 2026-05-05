"""Admin actions on users — kept separate from auth.py because these
operate on *other* users, not self-service. Self-protection rules:

- you cannot edit your own role (use SQL or another admin)
- only superadmins may edit anyone at admin+ rank
- the last superadmin cannot be demoted
"""

from __future__ import annotations

from sqlalchemy import func, select

from ..extensions import db
from ..models import User
from ..models.users import Role, role_rank


class AdminError(Exception):
    pass


class CannotEditSelfError(AdminError):
    pass


class InsufficientRankError(AdminError):
    """Actor's role is too low to edit the target."""

    pass


class LastSuperadminError(AdminError):
    """Refusing to demote the only remaining superadmin."""

    pass


class UnknownRoleError(AdminError):
    pass


_VALID_ROLES = {r.value for r in Role}


def change_user_role(*, actor: User, target: User, new_role: str) -> User:
    if new_role not in _VALID_ROLES:
        raise UnknownRoleError(new_role)
    if actor.id == target.id:
        raise CannotEditSelfError()

    actor_rank = role_rank(actor.role)
    target_rank = role_rank(target.role)
    new_rank = role_rank(new_role)

    # Only superadmins may touch admin+ targets, and only superadmins may
    # promote anyone *to* admin+. Below admin: any admin+ may edit.
    admin_threshold = role_rank(Role.ADMIN)
    if (target_rank >= admin_threshold or new_rank >= admin_threshold) and \
       actor_rank < role_rank(Role.SUPERADMIN):
        raise InsufficientRankError()

    # Last-superadmin protection: if target is currently superadmin and
    # we're demoting them, ensure at least one other superadmin remains.
    if target.role == Role.SUPERADMIN and new_role != Role.SUPERADMIN:
        other_supers = db.session.scalar(
            select(func.count(User.id))
            .where(User.role == Role.SUPERADMIN)
            .where(User.id != target.id)
        )
        if not other_supers:
            raise LastSuperadminError()

    before_role = target.role
    target.role = new_role
    db.session.commit()

    # Append to the audit trail. Best-effort — never raises into caller.
    from . import admin_audit

    admin_audit.log_action(
        actor_user_id=actor.id,
        action="role_change",
        target_user_id=target.id,
        before={"role": before_role},
        after={"role": new_role},
    )
    return target
