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
        "icon": "star",
        "tier": "gold",
        "source_kind": "manual",
        "sort_order": 10,
    },
    {
        "key": "link_discord",
        "name": "Discord Linked",
        "description": "Connected your Discord account via OAuth.",
        "icon": "link",
        "tier": "slate",
        "source_kind": "oauth_link",
        "sort_order": 100,
    },
    {
        "key": "link_google",
        "name": "Google Linked",
        "description": "Connected your Google account via OAuth.",
        "icon": "link",
        "tier": "slate",
        "source_kind": "oauth_link",
        "sort_order": 101,
    },
    {
        "key": "first_official_race",
        "name": "First Official Race",
        "description": "Completed your first Official race.",
        "icon": "flag",
        "tier": "bronze",
        "source_kind": "race_result",
        "sort_order": 200,
    },
    {
        "key": "first_official_podium",
        "name": "First Official Podium",
        "description": "Finished top 3 in an Official race.",
        "icon": "award",
        "tier": "bronze",
        "source_kind": "race_result",
        "sort_order": 201,
    },
    {
        "key": "first_official_win",
        "name": "First Official Win",
        "description": "Won your first Official race.",
        "icon": "trophy",
        "tier": "silver",
        "source_kind": "race_result",
        "sort_order": 202,
    },
    {
        "key": "first_draft_match",
        "name": "First Draft Match",
        "description": "Completed your first Draft match.",
        "icon": "dice",
        "tier": "bronze",
        "source_kind": "draft_match",
        "sort_order": 300,
    },
    {
        "key": "first_draft_win",
        "name": "First Draft Win",
        "description": "Won your first Draft match.",
        "icon": "swords",
        "tier": "silver",
        "source_kind": "draft_match",
        "sort_order": 301,
    },
    {
        "key": "season_top_3",
        "name": "Season Podium",
        "description": "Finished top 3 on a season's Official ladder.",
        "icon": "award",
        "tier": "gold",
        "source_kind": "season_close",
        "sort_order": 400,
    },
    {
        "key": "season_champion",
        "name": "Season Champion",
        "description": "Finished #1 on a season's Official ladder.",
        "icon": "crown",
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


# ─── Showcase (PR-P4) ───────────────────────────────────────────


# Hard cap on how many achievements a user can pin to their
# Overview hero showcase. Tunable here; changing it doesn't need
# a migration. Keep small enough that the row fits on the hero's
# left column without wrapping awkwardly.
SHOWCASE_MAX = 6


def list_showcased_for_user(user_id: int) -> list[Achievement]:
    """Return the user's pinned achievements in their chosen
    order, filtered to ones they actually have unlocked AND that
    are still enabled in the catalogue.

    Stale ids (achievement deleted, disabled, or no longer
    granted to the user) are silently dropped — the persisted
    list never blocks rendering even if it gets out of sync.
    """
    from ..models import UserProfile

    profile = db.session.scalars(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).first()
    if profile is None or not profile.showcased_achievement_ids:
        return []

    requested_ids: list[int] = list(profile.showcased_achievement_ids)
    if not requested_ids:
        return []

    granted_ids = {
        ua.achievement_id
        for ua in db.session.scalars(
            select(UserAchievement).where(
                UserAchievement.user_id == user_id
            )
        )
    }
    achievements_by_id = {
        a.id: a
        for a in db.session.scalars(
            select(Achievement).where(
                Achievement.id.in_(requested_ids),
                Achievement.enabled.is_(True),
            )
        )
    }

    out: list[Achievement] = []
    for aid in requested_ids:
        if aid not in granted_ids:
            continue
        a = achievements_by_id.get(aid)
        if a is None:
            continue
        out.append(a)
    return out[:SHOWCASE_MAX]


def set_showcase(user: User, ordered_ids: list[int]) -> list[int]:
    """Persist the user's showcase as an ordered list of
    achievement ids. Validates that:
      - Every id refers to an enabled, granted achievement
        (silently drops invalid ids — same defensive policy as
        ``list_showcased_for_user``).
      - The final list is capped at ``SHOWCASE_MAX``.
      - Duplicates are removed (first-position wins).

    Returns the list actually persisted (post-validation, post-
    cap). Empty list clears the showcase.
    """
    from ..models import UserProfile

    profile = db.session.scalars(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).first()
    if profile is None:
        # Should never happen — get_or_create_profile is called
        # in the route before update_profile — but defend anyway.
        from .profiles import get_or_create_profile

        profile = get_or_create_profile(user)

    if not ordered_ids:
        profile.showcased_achievement_ids = None
        db.session.commit()
        return []

    granted_ids = {
        ua.achievement_id
        for ua in db.session.scalars(
            select(UserAchievement).where(
                UserAchievement.user_id == user.id
            )
        )
    }
    enabled_ids = {
        a.id
        for a in db.session.scalars(
            select(Achievement).where(
                Achievement.id.in_(ordered_ids),
                Achievement.enabled.is_(True),
            )
        )
    }

    seen: set[int] = set()
    final: list[int] = []
    for aid in ordered_ids:
        if aid in seen:
            continue
        if aid not in granted_ids or aid not in enabled_ids:
            continue
        seen.add(aid)
        final.append(aid)
        if len(final) >= SHOWCASE_MAX:
            break

    profile.showcased_achievement_ids = final or None
    db.session.commit()
    return final


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


# ─── Auto-grant hooks (PR-P5) ────────────────────────────────────
#
# Wrapped in try/except at the boundary so a grant bug — unknown
# achievement key, FK collision, transient DB error — never breaks
# the parent result-save / season-close transaction. Idempotent at
# both layers: `grant()` already returns the existing row when the
# user has the achievement, AND these helpers can be called more
# than once safely (e.g. across `submit_results` + `edit_results`
# replays).


def _safe_grant(user_id: int, key: str, *, source: str) -> None:
    """Resolve user_id → User and call `grant()`, swallowing any
    error so an achievement issue never breaks the calling
    transaction. Logs at warning level so Sentry catches the
    failure path."""
    try:
        user = db.session.get(User, user_id)
        if user is None:
            return
        grant(user, key, source=source)
    except Exception:  # noqa: BLE001
        try:
            from flask import current_app

            current_app.logger.warning(
                "auto-grant failed: key=%s user_id=%s", key, user_id,
                exc_info=True,
            )
        except Exception:  # noqa: BLE001 — even logger may fail outside app ctx
            pass


def grant_on_official_result(user_id: int, placement: int) -> None:
    """PR-P5 — fired after a result row lands in `submit_results`.
    Grants milestone achievements based on placement:

    - `first_official_race`: any placement (just participating).
    - `first_official_podium`: placement <= 3.
    - `first_official_win`: placement == 1.
    """
    _safe_grant(user_id, "first_official_race", source="auto:race_result")
    if placement <= 3:
        _safe_grant(
            user_id, "first_official_podium", source="auto:race_result"
        )
    if placement == 1:
        _safe_grant(
            user_id, "first_official_win", source="auto:race_result"
        )


def grant_on_draft_match_completion(
    *,
    host_user_id: int,
    opponent_user_id: int | None,
    winner_user_id: int | None,
) -> None:
    """PR-P5 — fired after a draft match transitions to COMPLETED
    via `_apply_result_decision` (covers both fresh `submit_results`
    and admin `edit_results`).

    - `first_draft_match`: both participants. Even if opponent is
      None (shouldn't happen in practice at this point), we just
      skip them.
    - `first_draft_win`: only the winner.
    """
    _safe_grant(host_user_id, "first_draft_match", source="auto:draft_match")
    if opponent_user_id is not None:
        _safe_grant(
            opponent_user_id, "first_draft_match", source="auto:draft_match"
        )
    if winner_user_id is not None:
        _safe_grant(winner_user_id, "first_draft_win", source="auto:draft_match")


def grant_on_season_close(season_id: int) -> None:
    """PR-P5 — fired when a season's status flips to COMPLETED.
    Reads the final Official ladder for that season and grants:

    - `season_champion` to rank 1.
    - `season_top_3` to ranks 1, 2, 3.

    Draft ladder finishers are intentionally NOT auto-granted today:
    the season_top_3 / season_champion seed entries are scoped to the
    Official ladder per their descriptions; a parallel "Draft season
    podium" achievement would belong as a follow-up to the
    achievements backlog (separate keys, separate seed).
    """
    try:
        # Lazy import — services/seasons would otherwise import
        # services/achievements which imports User; the indirection
        # avoids a tangle in cold-start.
        from . import official as official_service

        rows = official_service.season_ladder(season_id, limit=3)
    except Exception:  # noqa: BLE001
        try:
            from flask import current_app

            current_app.logger.warning(
                "season-close ladder lookup failed: season_id=%s",
                season_id,
                exc_info=True,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    source = f"auto:season_close:{season_id}"
    for idx, row in enumerate(rows):
        _safe_grant(row.user_id, "season_top_3", source=source)
        if idx == 0:
            _safe_grant(row.user_id, "season_champion", source=source)
