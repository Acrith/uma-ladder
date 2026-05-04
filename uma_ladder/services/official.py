from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from ..extensions import db
from ..models import (
    OfficialRace,
    OfficialRaceRegistration,
    OfficialRaceResult,
    OfficialRaceStatus,
    RegistrationStatus,
    Season,
    User,
)
from .scoring import points_for_placement

ROOM_CODE_TTL = timedelta(hours=24)


class OfficialError(Exception):
    pass


class RaceNotFoundError(OfficialError):
    pass


class RaceFullError(OfficialError):
    pass


class AlreadyRegisteredError(OfficialError):
    pass


class InvalidRaceStateError(OfficialError):
    pass


class DuplicatePlacementError(OfficialError):
    pass


@dataclass(frozen=True)
class CreateRaceRequest:
    season_id: int
    name: str
    organizer_user_id: int
    preset_id: int | None = None
    scheduled_at: datetime | None = None
    max_players: int | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ResultLine:
    user_id: int
    placement: int
    uma_character_id: int | None = None
    uma_name: str | None = None
    strategy: str | None = None
    speed: int | None = None
    stamina: int | None = None
    power: int | None = None
    guts: int | None = None
    wisdom: int | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def create_race(req: CreateRaceRequest) -> OfficialRace:
    season = db.session.get(Season, req.season_id)
    if season is None:
        raise OfficialError(f"season {req.season_id} not found")
    race = OfficialRace(
        season_id=req.season_id,
        preset_id=req.preset_id,
        name=req.name,
        status=OfficialRaceStatus.DRAFT,
        organizer_user_id=req.organizer_user_id,
        scheduled_at=req.scheduled_at,
        max_players=req.max_players,
        notes=req.notes,
    )
    db.session.add(race)
    db.session.commit()
    return race


def open_registration(race_id: int) -> OfficialRace:
    race = _get_race(race_id)
    if race.status not in (
        OfficialRaceStatus.DRAFT,
        OfficialRaceStatus.REGISTRATION_CLOSED,
    ):
        raise InvalidRaceStateError(f"cannot open registration from {race.status}")
    race.status = OfficialRaceStatus.REGISTRATION_OPEN
    db.session.commit()
    return race


def close_registration(race_id: int) -> OfficialRace:
    race = _get_race(race_id)
    if race.status != OfficialRaceStatus.REGISTRATION_OPEN:
        raise InvalidRaceStateError(f"cannot close registration from {race.status}")
    race.status = OfficialRaceStatus.REGISTRATION_CLOSED
    db.session.commit()
    return race


def register(race_id: int, user_id: int) -> OfficialRaceRegistration:
    race = _get_race(race_id)
    if race.status != OfficialRaceStatus.REGISTRATION_OPEN:
        raise InvalidRaceStateError("registration is not open")

    existing = db.session.scalars(
        select(OfficialRaceRegistration).where(
            OfficialRaceRegistration.official_race_id == race_id,
            OfficialRaceRegistration.user_id == user_id,
            OfficialRaceRegistration.status == RegistrationStatus.REGISTERED,
        )
    ).first()
    if existing is not None:
        raise AlreadyRegisteredError()

    if race.max_players is not None:
        active = db.session.scalar(
            select(func.count())
            .select_from(OfficialRaceRegistration)
            .where(
                OfficialRaceRegistration.official_race_id == race_id,
                OfficialRaceRegistration.status == RegistrationStatus.REGISTERED,
            )
        )
        if active is not None and active >= race.max_players:
            raise RaceFullError()

    reg = OfficialRaceRegistration(
        official_race_id=race_id,
        user_id=user_id,
        status=RegistrationStatus.REGISTERED,
    )
    db.session.add(reg)
    db.session.commit()
    return reg


def list_registrations(race_id: int) -> Sequence[OfficialRaceRegistration]:
    return list(
        db.session.scalars(
            select(OfficialRaceRegistration)
            .where(
                OfficialRaceRegistration.official_race_id == race_id,
                OfficialRaceRegistration.status == RegistrationStatus.REGISTERED,
            )
            .order_by(OfficialRaceRegistration.created_at)
        )
    )


def set_room_code(race_id: int, code: str, *, now: datetime | None = None) -> OfficialRace:
    race = _get_race(race_id)
    if race.status not in (
        OfficialRaceStatus.REGISTRATION_OPEN,
        OfficialRaceStatus.REGISTRATION_CLOSED,
        OfficialRaceStatus.ROOM_CODE_AVAILABLE,
        OfficialRaceStatus.ROOM_CODE_EXPIRED,
        OfficialRaceStatus.ROOM_CODE_PENDING,
    ):
        raise InvalidRaceStateError(f"cannot set room code from {race.status}")
    code = code.strip()
    if not code:
        raise OfficialError("room code is empty")
    issued = now or _utcnow()
    race.room_code = code
    race.room_code_expires_at = issued + ROOM_CODE_TTL
    race.status = OfficialRaceStatus.ROOM_CODE_AVAILABLE
    db.session.commit()
    return race


def is_room_code_expired(race: OfficialRace, *, now: datetime | None = None) -> bool:
    if race.room_code_expires_at is None:
        return False
    return _as_utc(now or _utcnow()) >= _as_utc(race.room_code_expires_at)


def submit_results(
    race_id: int,
    lines: Sequence[ResultLine],
    *,
    confirmed_by_user_id: int,
    apply_grade_multiplier: bool = False,
) -> Sequence[OfficialRaceResult]:
    race = _get_race(race_id)
    if not lines:
        raise OfficialError("no result lines provided")
    placements = [line.placement for line in lines]
    if len(set(placements)) != len(placements):
        raise DuplicatePlacementError()

    grade = race.preset.grade if race.preset is not None else None
    saved: list[OfficialRaceResult] = []
    for line in lines:
        points = points_for_placement(
            line.placement,
            grade=grade,
            apply_grade_multiplier=apply_grade_multiplier,
        )
        result = OfficialRaceResult(
            official_race_id=race_id,
            user_id=line.user_id,
            uma_character_id=line.uma_character_id,
            uma_name=line.uma_name,
            placement=line.placement,
            points=points,
            strategy=line.strategy,
            speed=line.speed,
            stamina=line.stamina,
            power=line.power,
            guts=line.guts,
            wisdom=line.wisdom,
            confirmed_by_user_id=confirmed_by_user_id,
        )
        db.session.add(result)
        saved.append(result)

    race.status = OfficialRaceStatus.COMPLETED
    db.session.commit()
    return saved


@dataclass(frozen=True)
class LadderRow:
    user_id: int
    username: str
    total_points: int
    races_entered: int
    top1: int
    top2: int
    top3: int


def season_ladder(season_id: int, *, limit: int | None = None) -> list[LadderRow]:
    top1_expr = func.sum(case((OfficialRaceResult.placement == 1, 1), else_=0))
    top2_expr = func.sum(case((OfficialRaceResult.placement == 2, 1), else_=0))
    top3_expr = func.sum(case((OfficialRaceResult.placement == 3, 1), else_=0))
    total_points_expr = func.coalesce(func.sum(OfficialRaceResult.points), 0)
    stmt = (
        select(
            User.id.label("user_id"),
            User.username.label("username"),
            total_points_expr.label("total_points"),
            func.count(OfficialRaceResult.id).label("races_entered"),
            top1_expr.label("top1"),
            top2_expr.label("top2"),
            top3_expr.label("top3"),
        )
        .join(OfficialRaceResult, OfficialRaceResult.user_id == User.id)
        .join(OfficialRace, OfficialRace.id == OfficialRaceResult.official_race_id)
        .where(OfficialRace.season_id == season_id)
        .group_by(User.id, User.username)
        .order_by(
            total_points_expr.desc(),
            top1_expr.desc(),
            User.username.asc(),
        )
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = db.session.execute(stmt).all()
    return [
        LadderRow(
            user_id=r.user_id,
            username=r.username,
            total_points=int(r.total_points or 0),
            races_entered=int(r.races_entered or 0),
            top1=int(r.top1 or 0),
            top2=int(r.top2 or 0),
            top3=int(r.top3 or 0),
        )
        for r in rows
    ]


def list_races(*, season_id: int | None = None) -> Sequence[OfficialRace]:
    stmt = select(OfficialRace)
    if season_id is not None:
        stmt = stmt.where(OfficialRace.season_id == season_id)
    stmt = stmt.order_by(OfficialRace.created_at.desc())
    return list(db.session.scalars(stmt))


def get_race(race_id: int) -> OfficialRace:
    return _get_race(race_id)


def _get_race(race_id: int) -> OfficialRace:
    race = db.session.get(OfficialRace, race_id)
    if race is None:
        raise RaceNotFoundError(str(race_id))
    return race
