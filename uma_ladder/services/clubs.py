"""First-class Club entity helpers (PR-M1).

Sits between the uma.moe trainer-summary cache (which stores raw
JSON keyed by friend_code) and per-club surfaces (``/clubs/<id>``,
the future per-club ladder, the future multi-club race allowlist).

The ``clubs`` table is purely a metadata cache: name + cached_at
keyed by uma.moe's ``circle_id``. ``UserProfile.club_id`` keeps
acting as the per-user mirror.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select

from ..extensions import db
from ..models import Club, User, UserProfile


def get_club(circle_id: int) -> Club | None:
    return db.session.get(Club, circle_id)


def upsert_club_from_trainer(trainer) -> Club | None:  # noqa: ANN001
    """Create or refresh a ``Club`` row from a ``TrainerSummary``.

    Returns the ``Club`` (created or refreshed) when the trainer
    summary carries a usable ``circle_id``, else ``None``. Idempotent:
    the row is written only when the cached name actually changes,
    so repeated profile views don't generate noisy commits.

    ``trainer`` is typed loosely (``Any``) to avoid a circular import
    with ``services.uma_moe`` — duck-typed access of ``circle_id`` /
    ``circle_name`` is enough.
    """
    if trainer is None:
        return None
    circle_id = getattr(trainer, "circle_id", None)
    if not circle_id:
        return None
    name = getattr(trainer, "circle_name", None)

    club = db.session.get(Club, circle_id)
    if club is None:
        club = Club(
            circle_id=circle_id,
            name=name,
            cached_at=datetime.now(UTC),
        )
        db.session.add(club)
        db.session.commit()
        return club

    # Refresh only if the upstream name changed; otherwise touch
    # cached_at lazily once a day to avoid a write on every profile
    # view. The "freshness" signal here is for the user's eye, not
    # for staleness-driven re-fetch logic — uma_moe_cache already
    # handles that.
    now = datetime.now(UTC)
    cached_at = club.cached_at
    if cached_at is not None and cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=UTC)
    name_changed = club.name != name
    stale = cached_at is None or (now - cached_at).total_seconds() > 86400
    if name_changed or stale:
        club.name = name
        club.cached_at = now
        db.session.commit()
    return club


def list_members(circle_id: int) -> list[tuple[User, UserProfile]]:
    """Uma Ladder users whose ``UserProfile.club_id`` matches.

    Roster is local-only: this is "who from Uma Ladder is in this
    club", not "every uma.moe member of this club" (which would
    require a separate uma.moe endpoint we don't currently fetch).

    Ordered by username for stable rendering.
    """
    rows = db.session.execute(
        select(User, UserProfile)
        .join(UserProfile, UserProfile.user_id == User.id)
        .where(UserProfile.club_id == circle_id)
        .order_by(User.username.asc())
    ).all()
    return [(u, p) for u, p in rows]


def known_circle_ids() -> Iterable[int]:
    """All ``circle_id``s with a Club row. Useful for future
    autocomplete on the multi-club race allowlist form."""
    return db.session.scalars(select(Club.circle_id)).all()
