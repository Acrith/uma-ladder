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
