"""Achievement grants + lookups (PR-P2).

The catalogue lives in the ``achievements`` table (seeded via
migration in production; via :func:`ensure_starter_seed` in tests
where ``db.create_all()`` skips data migrations). Service code
dispatches on the stable ``key`` field so seeded ids don't
matter. Grants are idempotent — calling ``grant`` for an
already-earned achievement returns the existing row without
writing.

The seed list is duplicated in the create_achievements migration
so each migration stays a self-contained snapshot. Drift between
the two would only manifest as a test/prod difference; if you
add a new starter entry, update both.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from ..extensions import db
from ..models import Achievement, User, UserAchievement

# ─── Starter catalogue (mirror of migration 6f86401b628f) ────────


STARTER_ACHIEVEMENT_DEFINITIONS: list[dict] = [
    {
        "key": "founding_member",
        "name": "Founding Member",
        "description": "Joined Uma Ladder during its closed-club era.",
        "icon": "★",
        "tier": "gold",
        "source_kind": "manual",
        "sort_order": 10,
    },
    {
        "key": "link_discord",
        "name": "Discord Linked",
        "description": "Connected your Discord account via OAuth.",
        "icon": "🔗",
        "tier": "slate",
        "source_kind": "oauth_link",
        "sort_order": 100,
    },
    {
        "key": "link_google",
        "name": "Google Linked",
        "description": "Connected your Google account via OAuth.",
        "icon": "🔗",
        "tier": "slate",
        "source_kind": "oauth_link",
        "sort_order": 101,
    },
    {
        "key": "first_official_race",
        "name": "First Official Race",
        "description": "Completed your first Official race.",
        "icon": "🏁",
        "tier": "bronze",
        "source_kind": "race_result",
        "sort_order": 200,
    },
    {
        "key": "first_official_podium",
        "name": "First Official Podium",
        "description": "Finished top 3 in an Official race.",
        "icon": "🥉",
        "tier": "bronze",
        "source_kind": "race_result",
        "sort_order": 201,
    },
    {
        "key": "first_official_win",
        "name": "First Official Win",
        "description": "Won your first Official race.",
        "icon": "🥇",
        "tier": "silver",
        "source_kind": "race_result",
        "sort_order": 202,
    },
    {
        "key": "first_draft_match",
        "name": "First Draft Match",
        "description": "Completed your first Draft match.",
        "icon": "🎲",
        "tier": "bronze",
        "source_kind": "draft_match",
        "sort_order": 300,
    },
    {
        "key": "first_draft_win",
        "name": "First Draft Win",
        "description": "Won your first Draft match.",
        "icon": "🥇",
        "tier": "silver",
        "source_kind": "draft_match",
        "sort_order": 301,
    },
    {
        "key": "season_top_3",
        "name": "Season Podium",
        "description": "Finished top 3 on a season's Official ladder.",
        "icon": "🏆",
        "tier": "gold",
        "source_kind": "season_close",
        "sort_order": 400,
    },
    {
        "key": "season_champion",
        "name": "Season Champion",
        "description": "Finished #1 on a season's Official ladder.",
        "icon": "👑",
        "tier": "gold",
        "source_kind": "season_close",
        "sort_order": 401,
    },
]


def ensure_starter_seed() -> int:
    """Idempotently insert the starter catalogue entries that are
    missing. Returns the number of rows inserted (0 when the
    catalogue is already complete).

    Useful in test setup (where ``db.create_all`` skips data
    migrations) and for re-syncing the catalogue if a partial
    migration ever leaves the table inconsistent. NOT called on
    app boot — production seeds via the migration's bulk_insert.
    """
    existing_keys = set(
        db.session.scalars(select(Achievement.key)).all()
    )
    inserted = 0
    for definition in STARTER_ACHIEVEMENT_DEFINITIONS:
        if definition["key"] in existing_keys:
            continue
        db.session.add(Achievement(**definition, enabled=True))
        inserted += 1
    if inserted:
        db.session.commit()
    return inserted


class AchievementError(Exception):
    pass


class UnknownAchievementError(AchievementError):
    """Raised when a service caller asks for an achievement key
    that isn't in the catalogue. Callers should never see this in
    auto-grant flows — the keys are static; if it fires, the
    seed is out of sync with the code."""


# ─── Catalogue lookups ───────────────────────────────────────────


def list_definitions(*, enabled_only: bool = True) -> Sequence[Achievement]:
    """All achievements in display order. Used by the admin grant
    UI + the (deferred) browse-all page."""
    stmt = select(Achievement)
    if enabled_only:
        stmt = stmt.where(Achievement.enabled.is_(True))
    stmt = stmt.order_by(
        Achievement.sort_order.asc(), Achievement.name.asc()
    )
    return list(db.session.scalars(stmt))


def get_by_key(key: str) -> Achievement | None:
    return db.session.scalars(
        select(Achievement).where(Achievement.key == key)
    ).first()


# ─── Per-user lookups ────────────────────────────────────────────


def list_for_user(user_id: int) -> Sequence[UserAchievement]:
    """Unlocked achievements for a user, in catalogue display
    order. Drives the public-profile render."""
    return list(
        db.session.scalars(
            select(UserAchievement)
            .join(Achievement, Achievement.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
            .where(Achievement.enabled.is_(True))
            .order_by(
                Achievement.sort_order.asc(), Achievement.name.asc()
            )
        )
    )


def has_achievement(user_id: int, key: str) -> bool:
    """Cheap probe used by auto-grant call sites to short-circuit
    before doing eligibility computation. Falls back to False if
    the achievement key is unknown — auto-grant for an unknown
    key is just a no-op."""
    achievement = get_by_key(key)
    if achievement is None:
        return False
    return (
        db.session.scalars(
            select(UserAchievement).where(
                UserAchievement.user_id == user_id,
                UserAchievement.achievement_id == achievement.id,
            )
        ).first()
        is not None
    )


# ─── Grants ──────────────────────────────────────────────────────


def grant(
    user: User,
    key: str,
    *,
    source: str | None = None,
) -> UserAchievement:
    """Grant an achievement by key. Idempotent — if the user
    already has it, returns the existing row without touching
    ``unlocked_at`` (the original earn timestamp is preserved).

    ``source`` is free-form context for audit / debug; e.g.
    ``"oauth_callback"``, ``"admin:<admin_username>"``,
    ``"season_close:<season_id>"``. Stored on the row only when
    the grant actually creates a new row.

    Raises ``UnknownAchievementError`` if ``key`` isn't in the
    catalogue. This is a programmer error (typo / out-of-sync
    seed); auto-grant call sites should never see it."""
    achievement = get_by_key(key)
    if achievement is None:
        raise UnknownAchievementError(key)
    if not achievement.enabled:
        # Disabled achievements can't be granted. Treat as no-op
        # rather than error — the catalogue entry exists, just
        # not awardable today (e.g. retired event).
        existing = db.session.scalars(
            select(UserAchievement).where(
                UserAchievement.user_id == user.id,
                UserAchievement.achievement_id == achievement.id,
            )
        ).first()
        if existing is not None:
            return existing
        raise AchievementError(f"achievement {key!r} is not currently grantable")

    existing = db.session.scalars(
        select(UserAchievement).where(
            UserAchievement.user_id == user.id,
            UserAchievement.achievement_id == achievement.id,
        )
    ).first()
    if existing is not None:
        return existing

    row = UserAchievement(
        user_id=user.id,
        achievement_id=achievement.id,
        unlocked_at=datetime.now(UTC),
        source=source,
    )
    db.session.add(row)
    db.session.commit()
    return row
