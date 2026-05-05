from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from ..extensions import db
from ..models import (
    OfficialRace,
    OfficialRaceRegistration,
    OfficialRaceResult,
    OfficialRaceResultSkill,
    OfficialRaceStatus,
    RegistrationStatus,
    Season,
    UmaSkill,
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
    was_draft = race.status == OfficialRaceStatus.DRAFT
    race.status = OfficialRaceStatus.REGISTRATION_OPEN
    db.session.commit()
    # Only broadcast the published event on the *first* open — re-opening
    # after closing is not a publish event, the channel already saw it.
    if was_draft:
        _notify_race_published(race)
    return race


def _notify_race_published(race: OfficialRace) -> None:
    """Best-effort: never raises into the caller."""
    try:
        from ..notifications import services as notif_services

        notif_services.notify_official_race_published(race)
    except Exception:  # noqa: BLE001
        pass


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


def set_room_code(
    race_id: int,
    code: str,
    *,
    now: datetime | None = None,
    notify: bool = True,
) -> OfficialRace:
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
    if notify:
        _notify_room_code(race)
    return race


def _notify_room_code(race: OfficialRace) -> None:
    """Best-effort: never raises into the caller."""
    try:
        from ..notifications import services as notif_services

        regs = list_registrations(race.id)
        notif_services.notify_official_room_code(race, regs)
    except Exception:  # noqa: BLE001
        # Notification failures are recorded in DiscordNotificationAttempt.
        pass


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
    notify: bool = True,
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
    if notify:
        _notify_results(race)
    return saved


def _notify_results(race: OfficialRace) -> None:
    try:
        from ..notifications import services as notif_services

        top = season_ladder(race.season_id, limit=5)
        notif_services.notify_official_results(race, top)
    except Exception:  # noqa: BLE001
        pass


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


@dataclass(frozen=True)
class SeasonStanding:
    """Where a user sits on the season's official ladder + their podium
    counts. ``rank`` is 1-based. ``total_players`` lets the UI render
    'rank #X of Y' for context."""

    rank: int
    total_players: int
    top1: int
    top2: int
    top3: int
    races_entered: int
    total_points: int


def season_standing_for_user(
    user_id: int, season_id: int
) -> SeasonStanding | None:
    """Reuses :func:`season_ladder` and walks the result to find the
    user's row + position. Returns ``None`` when the user hasn't raced
    in the given season — caller decides whether that means a 'no data'
    tile or hiding entirely.

    O(N) over the player count; fine for a small community ladder. If
    we ever scale we can replace with a windowed SQL query (RANK()
    OVER PARTITION BY).
    """
    rows = season_ladder(season_id)
    for i, r in enumerate(rows):
        if r.user_id == user_id:
            return SeasonStanding(
                rank=i + 1,
                total_players=len(rows),
                top1=r.top1,
                top2=r.top2,
                top3=r.top3,
                races_entered=r.races_entered,
                total_points=r.total_points,
            )
    return None


def list_races(*, season_id: int | None = None) -> Sequence[OfficialRace]:
    stmt = select(OfficialRace)
    if season_id is not None:
        stmt = stmt.where(OfficialRace.season_id == season_id)
    stmt = stmt.order_by(OfficialRace.created_at.desc())
    return list(db.session.scalars(stmt))


_UPCOMING_STATUSES = (
    OfficialRaceStatus.REGISTRATION_OPEN,
    OfficialRaceStatus.REGISTRATION_CLOSED,
    OfficialRaceStatus.ROOM_CODE_PENDING,
    OfficialRaceStatus.ROOM_CODE_AVAILABLE,
)


def list_upcoming_races(
    *, season_id: int | None = None, limit: int | None = None
) -> Sequence[OfficialRace]:
    """Races a player can still join or that are about to launch — used
    by the dashboard upcoming-races card. Excludes draft (organiser still
    setting up), expired, and completed/cancelled.

    Sort order: scheduled_at ascending when set (soonest first), with a
    fallback bucket of unscheduled races ordered by created_at desc.
    SQLite NULLs sort before non-NULLs by default, so we coalesce to a
    far-future timestamp to keep unscheduled races below the scheduled
    ones in the same query.
    """
    far_future = datetime(9999, 1, 1, tzinfo=UTC)
    stmt = (
        select(OfficialRace)
        .where(OfficialRace.status.in_(_UPCOMING_STATUSES))
        .order_by(
            func.coalesce(OfficialRace.scheduled_at, far_future).asc(),
            OfficialRace.created_at.desc(),
        )
    )
    if season_id is not None:
        stmt = stmt.where(OfficialRace.season_id == season_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.session.scalars(stmt))


def get_race(race_id: int) -> OfficialRace:
    return _get_race(race_id)


@dataclass(frozen=True)
class ResultDetailsUpdate:
    """Per-result detail enrichment from a stat-screen OCR pass."""

    speed: int | None = None
    stamina: int | None = None
    power: int | None = None
    guts: int | None = None
    wisdom: int | None = None
    strategy: str | None = None
    skill_names: tuple[str, ...] = ()


class ResultNotFoundError(OfficialError):
    pass


def submit_result_details(
    result_id: int, update: ResultDetailsUpdate, *, by_user_id: int
) -> OfficialRaceResult:
    """Apply stat + skill enrichment to a single race result.

    Skill names are matched case-insensitively against UmaSkill.name_en.
    Names that don't match anything in the catalogue are still recorded —
    raw_ocr_text is preserved with skill_id=NULL so an organiser can
    correct the spelling or add the skill later. Existing skill rows on
    the result are replaced atomically (delete-then-insert) so re-running
    the OCR pass produces the same result regardless of the previous
    state.

    `by_user_id` is currently used only for audit context; permissions
    are enforced at the route layer."""
    del by_user_id  # accepted for symmetry with cancel_race / etc.
    result = db.session.get(OfficialRaceResult, result_id)
    if result is None:
        raise ResultNotFoundError(str(result_id))

    if update.speed is not None:
        result.speed = update.speed
    if update.stamina is not None:
        result.stamina = update.stamina
    if update.power is not None:
        result.power = update.power
    if update.guts is not None:
        result.guts = update.guts
    if update.wisdom is not None:
        result.wisdom = update.wisdom
    if update.strategy is not None:
        result.strategy = update.strategy

    # Replace skill associations atomically. ORM-cascade delete via the
    # `skills` relationship handles the existing rows; we just clear and
    # rebuild.
    for existing in list(result.skills):
        db.session.delete(existing)

    matches = _match_skill_names(update.skill_names)
    for i, (name, skill_id) in enumerate(matches):
        db.session.add(
            OfficialRaceResultSkill(
                official_race_result_id=result.id,
                skill_id=skill_id,
                raw_ocr_text=name,
                position=i,
            )
        )

    db.session.commit()
    return result


_SKILL_NAME_NOISE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_skill_name(s: str) -> str:
    """Aggressive normalization for OCR-tolerant skill matching.

    Lowercases, strips all non-alphanumeric characters (so em-dash
    vs hyphen, smart quotes vs straight quotes, decorative ☆ stars,
    spaces, ・ separators, !'s, etc. all collapse to the same key).
    OCR commonly mis-reads these glyphs and we'd rather match than
    silently miss a skill that's clearly in the catalogue.
    """
    return _SKILL_NAME_NOISE_RE.sub("", s.lower())


def _match_skill_names(
    names: Iterable[str],
) -> list[tuple[str, int | None]]:
    """Map raw skill name strings to UmaSkill.id.

    Two-stage match per name: first case-insensitive exact on name_en
    (cheapest, definitive), then a normalized-key fallback that strips
    typography differences. Returns (raw_name, skill_id_or_None) in the
    input order. Empty / whitespace-only names are dropped.
    """
    cleaned = [n.strip() for n in names if n and n.strip()]
    if not cleaned:
        return []
    # Stage 1: exact case-insensitive match.
    lower_lookup = {n.lower() for n in cleaned}
    rows = db.session.scalars(
        select(UmaSkill)
        .where(UmaSkill.enabled.is_(True))
        .where(func.lower(UmaSkill.name_en).in_(lower_lookup))
    ).all()
    by_lower = {s.name_en.lower(): s.id for s in rows}

    # Stage 2: build a normalized lookup over ALL enabled skills for
    # any names that didn't get an exact hit. Only run the second SQL
    # query if at least one name needs it.
    needs_fuzzy = [n for n in cleaned if n.lower() not in by_lower]
    by_normalized: dict[str, int] = {}
    if needs_fuzzy:
        # Loading the catalogue once is cheap (≈2k rows); doing this in
        # SQL would require either FTS or a dedicated normalized column.
        all_enabled = db.session.scalars(
            select(UmaSkill).where(UmaSkill.enabled.is_(True))
        ).all()
        for s in all_enabled:
            by_normalized.setdefault(_normalize_skill_name(s.name_en), s.id)

    out: list[tuple[str, int | None]] = []
    for n in cleaned:
        sid = by_lower.get(n.lower())
        if sid is None:
            sid = by_normalized.get(_normalize_skill_name(n))
        out.append((n, sid))
    return out


def cancel_race(race_id: int, *, by_user_id: int) -> OfficialRace:
    """Organiser-or-higher action — wipes a race short of completion.
    Refuses to touch already-completed or already-cancelled races.
    Caller is responsible for verifying the organiser role; this layer
    only guards state."""
    race = _get_race(race_id)
    if race.status == OfficialRaceStatus.COMPLETED:
        raise InvalidRaceStateError("cannot cancel a completed race")
    if race.status == OfficialRaceStatus.CANCELLED:
        raise InvalidRaceStateError("race is already cancelled")
    # Capture the registered usernames + actor BEFORE flipping status,
    # since list_registrations filters by REGISTERED and we want to
    # surface the affected players in the notification embed.
    affected = [r.user.username for r in list_registrations(race_id) if r.user]
    actor = db.session.get(User, by_user_id)
    actor_username = actor.username if actor else None

    race.status = OfficialRaceStatus.CANCELLED
    race.cancelled_at = _utcnow()
    race.cancelled_by_user_id = by_user_id
    db.session.commit()
    _notify_race_cancelled(race, actor_username, affected)
    return race


def _notify_race_cancelled(
    race: OfficialRace, actor_username: str | None, affected_usernames: list[str]
) -> None:
    try:
        from ..notifications import services as notif_services

        notif_services.notify_official_race_cancelled(
            race,
            cancelled_by_username=actor_username,
            registered_usernames=affected_usernames,
        )
    except Exception:  # noqa: BLE001
        pass


def remove_registration(
    race_id: int, registration_id: int, *, by_user_id: int
) -> OfficialRaceRegistration:
    """Organiser action — soft-cancel a player's registration. Refuses
    once the race is COMPLETED (results are locked in by then)."""
    race = _get_race(race_id)
    if race.status == OfficialRaceStatus.COMPLETED:
        raise InvalidRaceStateError("cannot remove registration from a completed race")
    reg = db.session.get(OfficialRaceRegistration, registration_id)
    if reg is None or reg.official_race_id != race_id:
        raise InvalidRaceStateError("registration not found on this race")
    if reg.status == RegistrationStatus.CANCELLED:
        return reg  # already removed; idempotent
    reg.status = RegistrationStatus.CANCELLED
    db.session.commit()

    actor = db.session.get(User, by_user_id)
    _notify_registration_removed(
        race, reg, actor.username if actor else None
    )
    return reg


def _notify_registration_removed(
    race: OfficialRace,
    registration: OfficialRaceRegistration,
    actor_username: str | None,
) -> None:
    try:
        from ..notifications import services as notif_services

        notif_services.notify_official_registration_removed(
            race, registration, removed_by_username=actor_username
        )
    except Exception:  # noqa: BLE001
        pass


def _get_race(race_id: int) -> OfficialRace:
    race = db.session.get(OfficialRace, race_id)
    if race is None:
        raise RaceNotFoundError(str(race_id))
    return race
