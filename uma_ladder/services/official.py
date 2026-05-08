from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from ..extensions import db
from ..models import (
    OfficialRace,
    OfficialRaceInvitee,
    OfficialRaceRegistration,
    OfficialRaceResult,
    OfficialRaceResultSkill,
    OfficialRaceStatus,
    OfficialRaceVisibility,
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
    race_season: str | None = None
    weather: str | None = None
    ground_condition: str | None = None
    # PR-J13 — defaults to public so callers that don't care
    # behave as before. Accepts "public" or "private"; "club" is
    # rejected here until the deferred Club follow-up lands.
    visibility: str | None = None


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


_VALID_CREATE_VISIBILITIES = {
    OfficialRaceVisibility.PUBLIC.value,
    OfficialRaceVisibility.PRIVATE.value,
}


def create_race(req: CreateRaceRequest) -> OfficialRace:
    season = db.session.get(Season, req.season_id)
    if season is None:
        raise OfficialError(f"season {req.season_id} not found")
    from . import track_conditions as track_conditions_service

    race_season, weather, ground = track_conditions_service.normalize(
        race_season=req.race_season,
        weather=req.weather,
        ground_condition=req.ground_condition,
    )
    visibility = (
        req.visibility or OfficialRaceVisibility.PUBLIC.value
    ).strip().lower()
    if visibility not in _VALID_CREATE_VISIBILITIES:
        # CLUB is reserved for the deferred follow-up; reject here
        # so the form can't sneak it through before that work.
        raise OfficialError(f"unsupported visibility: {visibility!r}")
    race = OfficialRace(
        season_id=req.season_id,
        preset_id=req.preset_id,
        name=req.name,
        status=OfficialRaceStatus.DRAFT,
        organizer_user_id=req.organizer_user_id,
        scheduled_at=req.scheduled_at,
        max_players=req.max_players,
        notes=req.notes,
        race_season=race_season,
        weather=weather,
        ground_condition=ground,
        visibility=visibility,
    )
    db.session.add(race)
    db.session.commit()
    return race


# ---------- PR-J13: visibility + invitees ----------


def list_invitees(race_id: int) -> Sequence[OfficialRaceInvitee]:
    return list(
        db.session.scalars(
            select(OfficialRaceInvitee)
            .where(OfficialRaceInvitee.official_race_id == race_id)
            .order_by(OfficialRaceInvitee.created_at.asc())
        )
    )


def add_invitee(
    race_id: int,
    *,
    invitee_username: str,
    invited_by_user_id: int,
) -> OfficialRaceInvitee:
    """Add a user to a Private race's allowlist. Idempotent —
    re-adding an already-invited user is a no-op (returns the
    existing row). Refuses on Public races (no allowlist semantics
    there) and on unknown usernames."""
    race = _get_race(race_id)
    if race.visibility != OfficialRaceVisibility.PRIVATE.value:
        raise OfficialError(
            "invitees only apply to private races"
        )
    target = db.session.scalars(
        select(User).where(
            User.username == invitee_username.strip().lower()
        )
    ).first()
    if target is None:
        raise OfficialError(f"no user named {invitee_username!r}")
    existing = db.session.scalars(
        select(OfficialRaceInvitee).where(
            OfficialRaceInvitee.official_race_id == race_id,
            OfficialRaceInvitee.user_id == target.id,
        )
    ).first()
    if existing is not None:
        return existing
    invitee = OfficialRaceInvitee(
        official_race_id=race_id,
        user_id=target.id,
        invited_by_user_id=invited_by_user_id,
    )
    db.session.add(invitee)
    db.session.commit()
    return invitee


def remove_invitee(race_id: int, *, user_id: int) -> int:
    """Remove a user from a Private race's allowlist. Returns
    count removed (0 or 1). Does NOT also unregister the user if
    they had registered — that's a separate organizer action.
    Reasoning: pulling someone off the allowlist may be a
    correction (typo invite) and the registration cleanup should
    be deliberate."""
    invitee = db.session.scalars(
        select(OfficialRaceInvitee).where(
            OfficialRaceInvitee.official_race_id == race_id,
            OfficialRaceInvitee.user_id == user_id,
        )
    ).first()
    if invitee is None:
        return 0
    db.session.delete(invitee)
    db.session.commit()
    return 1


# PR-J14 — terminal states where flipping visibility serves no
# purpose; refuse so the page can't get into weird states (e.g.
# "make completed race private" hiding historical results).
_TERMINAL_RACE_STATUSES = frozenset(
    {OfficialRaceStatus.CANCELLED, OfficialRaceStatus.COMPLETED}
)


def change_visibility(
    race_id: int,
    *,
    new_visibility: str,
    by_user_id: int,
) -> OfficialRace:
    """Toggle a race between Public and Private after creation
    (PR-J14). Used by organizers who realised a Public race should
    have been Private (or vice versa).

    Public → Private auto-promotes everyone currently in the
    REGISTERED state into the invitee allowlist so they don't lose
    access to a race they already joined. (Cancelled registrations
    aren't re-granted.)

    Private → Public just opens the gate; existing invitee rows
    stay but become harmless.

    Refuses on terminal states (CANCELLED / COMPLETED) — changing
    a finished race's visibility serves no purpose and would
    rewrite history. Only the organizer (and senior_organizer+
    moderators per `assert_can_act_on_race`) may flip.
    """
    race = _get_race(race_id)
    from .permissions import assert_can_act_on_race

    assert_can_act_on_race(race, by_user_id=by_user_id)

    new_visibility = new_visibility.strip().lower()
    if new_visibility not in _VALID_CREATE_VISIBILITIES:
        raise OfficialError(f"unsupported visibility: {new_visibility!r}")
    if race.status in _TERMINAL_RACE_STATUSES:
        raise InvalidRaceStateError(
            f"cannot change visibility on a {race.status} race"
        )
    if race.visibility == new_visibility:
        return race  # no-op

    if new_visibility == OfficialRaceVisibility.PRIVATE.value:
        # Public → Private: protect existing registrants. Pull the
        # active registrations and ensure each user has a matching
        # invitee row. Idempotent — if any user is somehow already
        # invited, we skip them.
        active_regs = db.session.scalars(
            select(OfficialRaceRegistration).where(
                OfficialRaceRegistration.official_race_id == race_id,
                OfficialRaceRegistration.status == RegistrationStatus.REGISTERED,
            )
        ).all()
        existing_invitees = {
            inv.user_id
            for inv in db.session.scalars(
                select(OfficialRaceInvitee).where(
                    OfficialRaceInvitee.official_race_id == race_id
                )
            )
        }
        for reg in active_regs:
            if reg.user_id in existing_invitees:
                continue
            db.session.add(
                OfficialRaceInvitee(
                    official_race_id=race_id,
                    user_id=reg.user_id,
                    invited_by_user_id=by_user_id,
                )
            )

    race.visibility = new_visibility
    db.session.commit()
    return race


def user_can_view_race(race: OfficialRace, *, user_id: int | None) -> bool:
    """Single source of truth for "is this race visible to this
    viewer?". Drives both the index list and the detail-page gate.

    - Public race: anyone (logged in or not) can view.
    - Private race: organizer + invitees + senior_organizer+
      moderators. Anonymous users see nothing for Private.
    """
    if race.visibility == OfficialRaceVisibility.PUBLIC.value:
        return True
    if user_id is None:
        return False
    if user_id == race.organizer_user_id:
        return True
    user = db.session.get(User, user_id)
    if user is not None and user.has_at_least("senior_organizer"):
        return True
    invitee = db.session.scalars(
        select(OfficialRaceInvitee).where(
            OfficialRaceInvitee.official_race_id == race.id,
            OfficialRaceInvitee.user_id == user_id,
        )
    ).first()
    return invitee is not None


def open_registration(
    race_id: int, *, by_user_id: int | None = None
) -> OfficialRace:
    race = _get_race(race_id)
    if by_user_id is not None:
        from .permissions import assert_can_act_on_race

        assert_can_act_on_race(race, by_user_id=by_user_id)
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


def close_registration(
    race_id: int, *, by_user_id: int | None = None
) -> OfficialRace:
    race = _get_race(race_id)
    if by_user_id is not None:
        from .permissions import assert_can_act_on_race

        assert_can_act_on_race(race, by_user_id=by_user_id)
    if race.status != OfficialRaceStatus.REGISTRATION_OPEN:
        raise InvalidRaceStateError(f"cannot close registration from {race.status}")
    race.status = OfficialRaceStatus.REGISTRATION_CLOSED
    db.session.commit()
    return race


def register(race_id: int, user_id: int) -> OfficialRaceRegistration:
    race = _get_race(race_id)
    if race.status != OfficialRaceStatus.REGISTRATION_OPEN:
        raise InvalidRaceStateError("registration is not open")
    # PR-J13 — Private races gate registration on the invitee
    # allowlist. The detail page is already invisible to non-
    # invitees (user_can_view_race), but defending in depth here
    # in case someone POSTs the register endpoint directly.
    if not user_can_view_race(race, user_id=user_id):
        raise OfficialError("not invited to this private race")

    # Look up *any* existing row for this (race, user). The
    # uq_official_race_registrations_race_user constraint forbids two
    # rows regardless of status — so a previously-cancelled row needs
    # to be revived in place rather than re-INSERTed.
    existing = db.session.scalars(
        select(OfficialRaceRegistration).where(
            OfficialRaceRegistration.official_race_id == race_id,
            OfficialRaceRegistration.user_id == user_id,
        )
    ).first()
    if existing is not None and existing.status == RegistrationStatus.REGISTERED:
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

    if existing is not None:
        # Revive a CANCELLED row — re-registering after being kicked
        # / unregistering. Updates the existing row in place so the
        # unique constraint is honoured.
        existing.status = RegistrationStatus.REGISTERED
        db.session.commit()
        return existing

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
    by_user_id: int | None = None,
    now: datetime | None = None,
    notify: bool = True,
) -> OfficialRace:
    race = _get_race(race_id)
    if by_user_id is not None:
        from .permissions import assert_can_act_on_race

        assert_can_act_on_race(race, by_user_id=by_user_id)
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
    # confirmed_by_user_id doubles as the actor for ownership.
    from .permissions import assert_can_act_on_race

    assert_can_act_on_race(race, by_user_id=confirmed_by_user_id)
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


def list_races(
    *,
    season_id: int | None = None,
    viewer_user_id: int | None = None,
) -> Sequence[OfficialRace]:
    """List races, scoped to what `viewer_user_id` is allowed to
    see (PR-J13). Pass ``viewer_user_id=None`` for anonymous (only
    sees Public races) or the moderator/admin path which already
    has full access via ``user_can_view_race``."""
    stmt = select(OfficialRace)
    if season_id is not None:
        stmt = stmt.where(OfficialRace.season_id == season_id)
    stmt = stmt.order_by(OfficialRace.created_at.desc())
    rows = list(db.session.scalars(stmt))
    return [r for r in rows if user_can_view_race(r, user_id=viewer_user_id)]


_UPCOMING_STATUSES = (
    OfficialRaceStatus.REGISTRATION_OPEN,
    OfficialRaceStatus.REGISTRATION_CLOSED,
    OfficialRaceStatus.ROOM_CODE_PENDING,
    OfficialRaceStatus.ROOM_CODE_AVAILABLE,
)


def list_upcoming_races(
    *,
    season_id: int | None = None,
    limit: int | None = None,
    viewer_user_id: int | None = None,
) -> Sequence[OfficialRace]:
    """Races a player can still join or that are about to launch — used
    by the dashboard upcoming-races card. Excludes draft (organiser still
    setting up), expired, and completed/cancelled.

    Sort order: scheduled_at ascending when set (soonest first), with a
    fallback bucket of unscheduled races ordered by created_at desc.
    SQLite NULLs sort before non-NULLs by default, so we coalesce to a
    far-future timestamp to keep unscheduled races below the scheduled
    ones in the same query.

    PR-J13: scope to what `viewer_user_id` is allowed to see — Private
    races appear only for the organizer + invitees + moderators+. We
    over-fetch then filter in Python to keep the visibility rule in
    one place; with `limit` set, we apply the limit AFTER filtering
    so the caller gets the requested number of *visible* rows.
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
    rows = list(db.session.scalars(stmt))
    visible = [r for r in rows if user_can_view_race(r, user_id=viewer_user_id)]
    if limit is not None:
        visible = visible[:limit]
    return visible


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
    """Cancel an in-flight race. Allowed actors per
    docs/permissions.md: the race's own organizer, plus any
    senior-organizer+. Refuses on already-completed / already-
    cancelled races."""
    race = _get_race(race_id)
    from .permissions import assert_can_act_on_race

    assert_can_act_on_race(race, by_user_id=by_user_id)
    if race.status == OfficialRaceStatus.COMPLETED:
        raise InvalidRaceStateError("cannot cancel a completed race")
    if race.status == OfficialRaceStatus.CANCELLED:
        raise InvalidRaceStateError("race is already cancelled")
    # Capture the registered usernames + actor BEFORE flipping status,
    # since list_registrations filters by REGISTERED and we want to
    # surface the affected players in the notification embed.
    affected_regs = [r for r in list_registrations(race_id) if r.user]
    affected_usernames = [r.user.username for r in affected_regs]
    affected_user_ids = [r.user_id for r in affected_regs]
    actor = db.session.get(User, by_user_id)
    actor_username = actor.username if actor else None

    race.status = OfficialRaceStatus.CANCELLED
    race.cancelled_at = _utcnow()
    race.cancelled_by_user_id = by_user_id
    db.session.commit()
    _notify_race_cancelled(
        race, actor_username, affected_usernames, affected_user_ids
    )
    # Audit trail (best-effort).
    try:
        from . import admin_audit

        admin_audit.log_action(
            actor_user_id=by_user_id,
            action="official_race_cancel",
            target_kind="official_race",
            target_id=race.id,
            details=f"affected_registrations={len(affected_regs)}",
        )
    except Exception:  # noqa: BLE001
        pass
    return race


def _notify_race_cancelled(
    race: OfficialRace,
    actor_username: str | None,
    affected_usernames: list[str],
    affected_user_ids: list[int],
) -> None:
    try:
        from ..notifications import services as notif_services

        notif_services.notify_official_race_cancelled(
            race,
            cancelled_by_username=actor_username,
            registered_usernames=affected_usernames,
            registered_user_ids=affected_user_ids,
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


def delete_race(race_id: int, *, by_user_id: int) -> None:
    """Hard-delete a race and everything tied to it (registrations,
    results, result skills) via the existing CASCADE FKs.

    Admin-only action — caller is responsible for verifying the role.
    Distinct from `cancel_race`, which leaves the row intact for audit
    and ladder-history purposes. Use this only for smoke-test / mistake
    cleanup; cancel is the right tool for "this race won't happen."

    Writes an audit row before the delete so the trail still references
    the race name + id even though the row is about to disappear.
    """
    race = _get_race(race_id)
    name = race.name
    # Audit BEFORE delete — once the row is gone, target_id refers to
    # nothing and the action becomes hard to reconstruct from logs.
    try:
        from . import admin_audit

        admin_audit.log_action(
            actor_user_id=by_user_id,
            action="official_race_delete",
            target_kind="official_race",
            target_id=race_id,
            details=f"name={name!r}",
        )
    except Exception:  # noqa: BLE001
        pass
    db.session.delete(race)
    db.session.commit()


def _get_race(race_id: int) -> OfficialRace:
    race = db.session.get(OfficialRace, race_id)
    if race is None:
        raise RaceNotFoundError(str(race_id))
    return race
