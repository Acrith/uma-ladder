from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from ..extensions import db
from ..models import Season, SeasonStatus


def list_seasons(*, status: str | None = None) -> Sequence[Season]:
    stmt = select(Season)
    if status is not None:
        stmt = stmt.where(Season.status == status)
    stmt = stmt.order_by(Season.starts_at.desc())
    return list(db.session.scalars(stmt))


def get_active_season(*, now: datetime | None = None) -> Season | None:
    moment = now or datetime.now(UTC)
    stmt = (
        select(Season)
        .where(Season.status == SeasonStatus.ACTIVE)
        .where(Season.starts_at <= moment)
        .where(Season.ends_at >= moment)
        .order_by(Season.starts_at.desc())
        .limit(1)
    )
    return db.session.scalars(stmt).first()


def create_season(
    *,
    name: str,
    starts_at: datetime,
    ends_at: datetime,
    created_by_user_id: int | None = None,
    status: str = SeasonStatus.ACTIVE,
) -> Season:
    if ends_at <= starts_at:
        raise ValueError("ends_at must be after starts_at")
    season = Season(
        name=name,
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
        created_by_user_id=created_by_user_id,
    )
    db.session.add(season)
    db.session.commit()
    return season


class SeasonError(Exception):
    pass


class SeasonNotFoundError(SeasonError):
    pass


_VALID_STATUSES = {s.value for s in SeasonStatus}


def get_season(season_id: int) -> Season:
    season = db.session.get(Season, season_id)
    if season is None:
        raise SeasonNotFoundError(str(season_id))
    return season


def set_status(season_id: int, new_status: str) -> Season:
    """Transition a season to a new status. Validates the new value is
    a known SeasonStatus; doesn't enforce a state machine — admins can
    move seasons freely between PLANNED/ACTIVE/COMPLETED/ARCHIVED.
    """
    if new_status not in _VALID_STATUSES:
        raise SeasonError(f"unknown status {new_status!r}")
    season = get_season(season_id)
    was_completed = season.status == SeasonStatus.COMPLETED.value
    season.status = new_status
    db.session.commit()
    # PR-P5 — season-close auto-grants for the top 3 on the Official
    # ladder. Only fires on the PLANNED/ACTIVE → COMPLETED edge —
    # going COMPLETED → ARCHIVED → COMPLETED back wouldn't re-fire
    # the grant pass (idempotent anyway, but no point doing the work).
    if (
        new_status == SeasonStatus.COMPLETED.value
        and not was_completed
    ):
        from . import achievements as achievements_service

        achievements_service.grant_on_season_close(season_id)
    return season


def _as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; user input arrives tz-aware.
    Normalize to UTC for safe comparison."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def update_season(
    season_id: int,
    *,
    name: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    official_enabled: bool | None = None,
    draft_enabled: bool | None = None,
) -> Season:
    """Patch-style update — only fields provided as non-None are
    written. Validates date ordering when both endpoints are touched
    (or only one is touched against the existing other endpoint)."""
    season = get_season(season_id)
    new_starts = _as_utc(starts_at if starts_at is not None else season.starts_at)
    new_ends = _as_utc(ends_at if ends_at is not None else season.ends_at)
    if new_ends <= new_starts:
        raise SeasonError("ends_at must be after starts_at")

    if name is not None:
        season.name = name
    if starts_at is not None:
        season.starts_at = starts_at
    if ends_at is not None:
        season.ends_at = ends_at
    if official_enabled is not None:
        season.official_enabled = official_enabled
    if draft_enabled is not None:
        season.draft_enabled = draft_enabled
    db.session.commit()
    return season
