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


def soft_delete_user(*, actor: User, target: User) -> None:
    """PR-Q3a — disable an account without nuking its FK'd history.

    The "delete" we want for active racers: race results, draft
    history, opponent ELO chains all stay intact, but the user can't
    log in and their identity is stripped from anywhere it surfaces
    publicly. Re-enable via `restore_user`.

    What happens:
    1. Blank the linked UserProfile so no display_name / oshi /
       avatar / friend code / Discord handle leaks. The profile row
       stays (FKs to it remain valid).
    2. Delete all linked AuthIdentity rows so the OAuth provider's
       external_id is free to re-bind to another account (the
       legitimate-original-account case: someone OAuth'd into a
       duplicate, we disable the duplicate, they re-link Discord
       to their real account).
    3. Set `users.disabled_at = now()` so login refuses, render
       helpers mask the username, and the Players index / rankings
       hide the row.
    4. Scramble `password_hash` so password login is broken even if
       a future bug skips the `disabled_at` check.
    5. Audit-log under `user_disable`.

    Guards: cannot disable yourself, cannot disable the last
    superadmin. Route layer additionally restricts who can call this
    (`min_role_required(Role.SUPERADMIN)` for now — same policy as
    hard delete; relax later as moderation tooling matures).

    Note: the linked race history rows survive intact, so the user
    still appears in past race detail pages — but renders via
    `masked_display_for` as `K***e`.
    """
    if actor.id == target.id:
        raise CannotEditSelfError()

    if target.role == Role.SUPERADMIN:
        other_supers = db.session.scalar(
            select(func.count(User.id))
            .where(User.role == Role.SUPERADMIN)
            .where(User.id != target.id)
        )
        if not other_supers:
            raise LastSuperadminError()

    import secrets
    from datetime import UTC, datetime

    from ..models import AuthIdentity

    # 1. Blank the profile (idempotent — profile may not exist yet).
    profile = target.profile
    if profile is not None:
        profile.display_name = None
        profile.avatar_url = None
        profile.description = None
        profile.friend_code = None
        profile.discord_handle = None
        profile.discord_user_id = None
        profile.club_id = None
        profile.avatar_border = None
        profile.showcased_achievement_ids = None
        profile.oshi_character_id = None
        profile.oshi_outfit_id = None

    # 2. Drop linked OAuth identities so their external_id is free
    #    to re-bind elsewhere.
    db.session.query(AuthIdentity).filter(
        AuthIdentity.user_id == target.id
    ).delete(synchronize_session=False)

    # 3 + 4. Stamp the tombstone + scramble the password.
    target.disabled_at = datetime.now(UTC)
    target.password_hash = secrets.token_urlsafe(32)

    db.session.commit()

    from . import admin_audit

    admin_audit.log_action(
        actor_user_id=actor.id,
        action="user_disable",
        target_user_id=target.id,
        details=f"@{target.username} (id={target.id}, role={target.role})",
    )


def restore_user(*, actor: User, target: User) -> None:
    """PR-Q3a — undo a soft-delete. Clears `disabled_at` so the
    account becomes loginable again. Does NOT re-populate the
    blanked profile fields — the user can fill them back in
    themselves once they're back in. Also requires the admin to
    issue a password reset link (existing /admin/users/<id>/
    reset-password) since the password was scrambled by soft_delete.

    Audit-logged. Same guards as the other admin actions on user
    rows (self-block; rank checks happen at the route layer).
    """
    if actor.id == target.id:
        raise CannotEditSelfError()

    target.disabled_at = None
    db.session.commit()

    from . import admin_audit

    admin_audit.log_action(
        actor_user_id=actor.id,
        action="user_restore",
        target_user_id=target.id,
        details=f"@{target.username} (id={target.id})",
    )


def delete_user(*, actor: User, target: User) -> None:
    """PR-R1 — hard-delete an account.

    Wired up after an organizer hit "Continue with Discord" for an
    existing player who had forgotten their password, creating a
    second account on prod. CASCADE FKs on `users.id` already sweep
    the noisy children (profile, identities, draft participations,
    race registrations); `official_race_results.user_id` is SET NULL
    so historical placings survive as `@?` rather than disappearing.

    Guards: cannot delete yourself, and the last superadmin cannot be
    deleted. Route-level gate (`min_role_required(Role.SUPERADMIN)`)
    limits this to superadmin actors — that's the policy decision for
    today; if we later want to grant admins this surface we relax the
    route decorator, not the service.

    Audit-logged with the deleted username + role captured in the
    `details` field, since the user row is gone after commit and the
    audit FKs are SET NULL.
    """
    if actor.id == target.id:
        raise CannotEditSelfError()

    if target.role == Role.SUPERADMIN:
        other_supers = db.session.scalar(
            select(func.count(User.id))
            .where(User.role == Role.SUPERADMIN)
            .where(User.id != target.id)
        )
        if not other_supers:
            raise LastSuperadminError()

    target_username = target.username
    target_role = target.role
    target_id = target.id

    db.session.delete(target)
    db.session.commit()

    from . import admin_audit

    admin_audit.log_action(
        actor_user_id=actor.id,
        action="user_delete",
        target_user_id=None,  # row is gone; SET NULL anyway
        details=f"@{target_username} (id={target_id}, role={target_role})",
    )


def issue_password_reset_url(*, actor: User, target: User) -> str:
    """PR-J11 — admin-requested password reset stopgap.

    Generates a one-time reset URL for `target` (signed with
    itsdangerous, 1-hour TTL — see auth_service.issue_reset_token /
    consume_reset_token). The URL is RETURNED to the caller for the
    admin to share out-of-band (Discord DM, in-person, etc.); we
    don't have email infrastructure and the auth roadmap
    (project_auth_roadmap.md) deliberately keeps it that way until
    multi-provider OAuth lands.

    Rank protection mirrors change_user_role: a non-superadmin
    cannot generate a reset URL for an admin+ target — otherwise
    a compromised admin account could rotate a peer's password.

    Audit-logged via the standard AdminAuditLog feed.
    """
    actor_rank = role_rank(actor.role)
    target_rank = role_rank(target.role)
    admin_threshold = role_rank(Role.ADMIN)
    if (
        target_rank >= admin_threshold
        and actor_rank < role_rank(Role.SUPERADMIN)
    ):
        raise InsufficientRankError()

    from flask import current_app, url_for

    from . import admin_audit
    from . import auth as auth_service

    token = auth_service.issue_reset_token(
        current_app.config["SECRET_KEY"], target
    )
    url = url_for("auth.reset_password", token=token, _external=True)

    admin_audit.log_action(
        actor_user_id=actor.id,
        action="password_reset_issued",
        target_user_id=target.id,
        details="One-time reset URL generated; admin shares out-of-band.",
    )
    return url
