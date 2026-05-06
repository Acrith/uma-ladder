"""Role-rank gating + ownership checks.

Two layers of permission live in this codebase:

1. **Role floor** — `min_role_required(Role.X)` decorator at the
   route level rejects users whose role rank is below X. Coarse:
   anyone above the floor is allowed in.

2. **Ownership** — service-level `assert_can_act_on_race(...)`
   adds fine-grained gating: an organizer can only act on races
   they CREATED. Senior organizers and admins skip the ownership
   check (they're moderators-of-organizers).

The two layers compose: routes set the floor, services enforce
ownership. Calling the service directly (e.g. from a script) still
runs the ownership check.

The full permission matrix is documented in `docs/permissions.md`.
Update it alongside any change here so the matrix doesn't drift.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import abort
from flask_login import current_user

from ..models.users import Role, role_rank


class PermissionDeniedError(Exception):
    """Raised by service-level ownership checks. Routes catch this
    and translate to `abort(403)`. Distinct from
    `min_role_required`'s 403 so callers can tell whether a denial
    came from rank-too-low (decorator) or wrong-owner (service)."""


def has_role(user: Any, *roles: str) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.role in roles


def has_at_least(user: Any, role: str) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return role_rank(user.role) >= role_rank(role)


def assert_can_act_on_race(
    race: Any,
    *,
    by_user_id: int,
    actor_role: str | None = None,
) -> None:
    """Service-level ownership gate for state-changing actions on
    OfficialRace. Allows:

      - The race's own organizer (organizer_user_id matches by_user_id).
      - Anyone with role >= SENIOR_ORGANIZER (moderators).
      - The decorator already enforced the role floor; this only
        denies the organizer-on-someone-else's-race case.

    `actor_role` is the role string from the route layer; pass
    `current_user.role`. When omitted, the helper looks up the
    user — slightly slower (one DB round-trip), but tolerable for
    state-changing endpoints that aren't on a hot path.
    """
    if actor_role is None:
        # Lazy import: services/permissions doesn't normally need
        # the User model on the import path.
        from ..extensions import db
        from ..models import User

        actor = db.session.get(User, by_user_id)
        if actor is None:
            raise PermissionDeniedError("unknown actor")
        actor_role = actor.role

    if role_rank(actor_role) >= role_rank(Role.SENIOR_ORGANIZER):
        return  # senior+ act on any race
    organizer_id = getattr(race, "organizer_user_id", None)
    if organizer_id is not None and organizer_id == by_user_id:
        return  # organizer acting on their own race
    raise PermissionDeniedError(
        "only the race organizer or a senior organizer+ can do that"
    )


def role_required(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: 401 if anonymous, 403 if role not in allow-list."""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not getattr(current_user, "is_authenticated", False):
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def min_role_required(min_role: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: 401 if anonymous, 403 if rank below min_role."""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not getattr(current_user, "is_authenticated", False):
                abort(401)
            if not has_at_least(current_user, min_role):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator
