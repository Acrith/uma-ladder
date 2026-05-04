"""Draft PvP service — Workflow Z.

Phase order:
  WAITING_FOR_OPPONENT
    → READY_CHECK            (both joined, neither readied)
    → TRACK_BAN_PHASE        (each player bans one track condition)
    → randomize_preset()     (synchronous; locks selected_preset_id)
    → UMA_BAN_PHASE          (each player bans one UmaCharacter — blind)
    → ROOM_CODE_PENDING
    → ROOM_CODE_AVAILABLE    (host pastes code; 24h TTL)
    → COMPLETED              (host submits placements + which Uma was used)

No pre-race Uma submission — the Uma each player actually used is
captured at result time. Banned characters cannot appear as a result's
`uma_character_id`.
"""

from __future__ import annotations

import random
import secrets
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    DraftBanType,
    DraftEloChange,
    DraftMatch,
    DraftMatchBan,
    DraftMatchStatus,
    DraftRaceResult,
    RacePreset,
    UmaCharacter,
)
from .elo import DEFAULT_K, DEFAULT_RATING, apply_match
from .randomizer import Bans, RandomizerError, filter_presets, pick_preset

ROOM_CODE_TTL = timedelta(hours=24)
JOIN_CODE_LEN = 8
JOIN_CODE_ALPHABET = string.ascii_uppercase + string.digits

_TRACK_BAN_TYPES = frozenset(
    {
        DraftBanType.VENUE,
        DraftBanType.DIRECTION,
        DraftBanType.DISTANCE_CATEGORY,
        DraftBanType.SURFACE,
    }
)


class DraftError(Exception):
    pass


class DraftNotFoundError(DraftError):
    pass


class CannotJoinOwnMatchError(DraftError):
    pass


class MatchFullError(DraftError):
    pass


class InvalidMatchStateError(DraftError):
    pass


class InvalidUmaCountError(DraftError):
    pass


class NotAParticipantError(DraftError):
    pass


class DuplicateBanError(DraftError):
    pass


class UnknownBanTargetError(DraftError):
    pass


class BanWouldEmptyPoolError(DraftError):
    pass


class BannedCharacterUsedError(DraftError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _generate_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LEN))


@dataclass(frozen=True)
class CreateMatchRequest:
    season_id: int
    host_user_id: int
    umas_per_player: int  # 2 or 3
    preset_pool: str  # "g1" | "custom" | "g1+custom"


def create_match(req: CreateMatchRequest) -> DraftMatch:
    if req.umas_per_player not in (2, 3):
        raise InvalidUmaCountError("umas_per_player must be 2 or 3")
    for _ in range(10):
        code = _generate_join_code()
        if db.session.scalars(
            select(DraftMatch).where(DraftMatch.join_code == code)
        ).first() is None:
            break
    else:  # pragma: no cover
        raise DraftError("could not allocate a unique join code")

    match = DraftMatch(
        season_id=req.season_id,
        host_user_id=req.host_user_id,
        join_code=code,
        umas_per_player=req.umas_per_player,
        preset_pool=req.preset_pool,
        status=DraftMatchStatus.WAITING_FOR_OPPONENT,
    )
    db.session.add(match)
    db.session.commit()
    return match


def get_match(match_id: int) -> DraftMatch:
    match = db.session.get(DraftMatch, match_id)
    if match is None:
        raise DraftNotFoundError(str(match_id))
    return match


def find_by_join_code(code: str) -> DraftMatch | None:
    return db.session.scalars(
        select(DraftMatch).where(DraftMatch.join_code == code.strip().upper())
    ).first()


def join_match(match_id: int, user_id: int) -> DraftMatch:
    match = get_match(match_id)
    if match.status != DraftMatchStatus.WAITING_FOR_OPPONENT:
        raise InvalidMatchStateError(f"cannot join match in state {match.status}")
    if match.host_user_id == user_id:
        raise CannotJoinOwnMatchError()
    if match.opponent_user_id is not None:
        raise MatchFullError()
    match.opponent_user_id = user_id
    match.status = DraftMatchStatus.READY_CHECK
    db.session.commit()
    return match


def _require_participant(match: DraftMatch, user_id: int) -> None:
    if user_id not in (match.host_user_id, match.opponent_user_id):
        raise NotAParticipantError()


def ready_up(match_id: int, user_id: int) -> DraftMatch:
    match = get_match(match_id)
    _require_participant(match, user_id)
    if match.status != DraftMatchStatus.READY_CHECK:
        raise InvalidMatchStateError(f"cannot ready in state {match.status}")
    if user_id == match.host_user_id:
        match.host_ready = True
    else:
        match.opponent_ready = True
    if match.host_ready and match.opponent_ready:
        match.status = DraftMatchStatus.TRACK_BAN_PHASE
    db.session.commit()
    return match


_SKIP_SENTINEL = "__skip__"


def _bans_from_rows(rows: Iterable[DraftMatchBan]) -> Bans:
    """Build a randomizer Bans object from existing ban rows, ignoring
    the `__skip__` placeholder a player records when opting out of a ban."""
    venues: set[str] = set()
    directions: set[str] = set()
    distance_cats: set[str] = set()
    surfaces: set[str] = set()
    for b in rows:
        key = (b.condition_key or "").strip()
        if not key or key == _SKIP_SENTINEL:
            continue
        if b.ban_type == DraftBanType.VENUE:
            venues.add(key)
        elif b.ban_type == DraftBanType.DIRECTION:
            directions.add(key)
        elif b.ban_type == DraftBanType.DISTANCE_CATEGORY:
            distance_cats.add(key)
        elif b.ban_type == DraftBanType.SURFACE:
            surfaces.add(key)
    return Bans(
        venues=frozenset(venues),
        directions=frozenset(directions),
        distance_categories=frozenset(distance_cats),
        surfaces=frozenset(surfaces),
    )


def _add_to_bans(bans: Bans, ban_type: str, value: str) -> Bans:
    if ban_type == DraftBanType.VENUE:
        return Bans(
            venues=bans.venues | {value},
            directions=bans.directions,
            distance_categories=bans.distance_categories,
            surfaces=bans.surfaces,
        )
    if ban_type == DraftBanType.DIRECTION:
        return Bans(
            venues=bans.venues,
            directions=bans.directions | {value},
            distance_categories=bans.distance_categories,
            surfaces=bans.surfaces,
        )
    if ban_type == DraftBanType.DISTANCE_CATEGORY:
        return Bans(
            venues=bans.venues,
            directions=bans.directions,
            distance_categories=bans.distance_categories | {value},
            surfaces=bans.surfaces,
        )
    if ban_type == DraftBanType.SURFACE:
        return Bans(
            venues=bans.venues,
            directions=bans.directions,
            distance_categories=bans.distance_categories,
            surfaces=bans.surfaces | {value},
        )
    return bans


def feasible_track_ban_options(
    match_id: int,
    user_id: int,
    *,
    static_options: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Return the static option lists pruned to values that are *meaningful*
    bans given the opponent's existing track ban. Two filters apply:

    - Empty-pool: the candidate ban must leave at least one preset.
      Catches direct conflicts (opp banned Direction=Left, Direction=Right
      would empty everything).
    - Non-redundant: the candidate ban must actually remove at least one
      preset from the current pool. Catches cross-category dependencies
      (opp banned Surface=Turf → there are no Long-Dirt presets, so
      Distance=Long would ban nothing that wasn't already eliminated).
    """
    match = get_match(match_id)
    rows = list_bans(match_id)
    opponent_track_rows = [
        b for b in rows
        if b.user_id != user_id and b.ban_type in _TRACK_BAN_TYPES
    ]
    base_bans = _bans_from_rows(opponent_track_rows)
    pool_presets = list(db.session.scalars(select(RacePreset)))
    min_runners = 2 * match.umas_per_player

    base_survivors = filter_presets(
        pool_presets,
        match.preset_pool,
        base_bans,
        min_max_runners=min_runners,
    )

    # Direction is a binary axis (Left ↔ Right) for game-aptitude purposes.
    # If the opponent already banned a direction, the current player can't
    # also ban a direction — even if "Stretch" / "Straight" presets keep the
    # pool non-empty, banning both directions makes no game sense.
    opp_banned_direction = any(
        b.ban_type == DraftBanType.DIRECTION and b.condition_key
        and b.condition_key != _SKIP_SENTINEL
        for b in opponent_track_rows
    )

    out: dict[str, list[str]] = {}
    for ban_type, values in static_options.items():
        if ban_type == DraftBanType.DIRECTION and opp_banned_direction:
            out[ban_type] = []
            continue
        kept: list[str] = []
        for v in values:
            candidate = _add_to_bans(base_bans, ban_type, v)
            after = filter_presets(
                pool_presets,
                match.preset_pool,
                candidate,
                min_max_runners=min_runners,
            )
            if not after:
                continue  # would empty pool
            if len(after) == len(base_survivors):
                continue  # redundant — bans nothing the opponent didn't already
            kept.append(v)
        out[ban_type] = kept
    return out


def submit_track_ban(
    match_id: int,
    user_id: int,
    *,
    ban_type: str,
    condition_key: str,
) -> DraftMatchBan:
    match = get_match(match_id)
    _require_participant(match, user_id)
    if match.status != DraftMatchStatus.TRACK_BAN_PHASE:
        raise InvalidMatchStateError(f"cannot ban track in state {match.status}")
    if ban_type not in _TRACK_BAN_TYPES:
        raise UnknownBanTargetError(f"{ban_type!r} is not a track condition")
    key = condition_key.strip()
    if not key:
        raise UnknownBanTargetError("condition_key is required")

    existing = db.session.scalars(
        select(DraftMatchBan)
        .where(DraftMatchBan.draft_match_id == match_id)
        .where(DraftMatchBan.user_id == user_id)
        .where(DraftMatchBan.ban_type.in_(_TRACK_BAN_TYPES))
    ).first()
    if existing is not None:
        raise DuplicateBanError()

    # Server-side feasibility: even if the dropdown filtered correctly,
    # a concurrent opponent ban could have landed between page render
    # and submit. Re-check that this ban + opponent's leaves the pool
    # non-empty. Skip placeholders bypass this check (they don't ban
    # anything for real).
    if key != _SKIP_SENTINEL:
        rows = list_bans(match_id)
        opp_rows = [
            b for b in rows
            if b.user_id != user_id and b.ban_type in _TRACK_BAN_TYPES
        ]
        # Direction is binary; reject a second direction ban from the
        # opposite player even if the pool would survive (Straight/Stretch
        # presets exist in the data but don't represent real game tracks).
        if ban_type == DraftBanType.DIRECTION and any(
            b.ban_type == DraftBanType.DIRECTION
            and b.condition_key
            and b.condition_key != _SKIP_SENTINEL
            for b in opp_rows
        ):
            raise BanWouldEmptyPoolError(
                "direction is already banned by the opponent"
            )
        base_bans = _bans_from_rows(opp_rows)
        candidate_bans = _add_to_bans(base_bans, ban_type, key)
        pool_presets = list(db.session.scalars(select(RacePreset)))
        min_runners = 2 * match.umas_per_player
        base_survivors = filter_presets(
            pool_presets, match.preset_pool, base_bans, min_max_runners=min_runners
        )
        survivors = filter_presets(
            pool_presets,
            match.preset_pool,
            candidate_bans,
            min_max_runners=min_runners,
        )
        if not survivors:
            raise BanWouldEmptyPoolError(
                f"{ban_type}={key} would leave no eligible tracks"
            )
        if len(survivors) == len(base_survivors):
            raise BanWouldEmptyPoolError(
                f"{ban_type}={key} doesn't ban anything the opponent didn't already"
            )

    ban = DraftMatchBan(
        draft_match_id=match_id,
        user_id=user_id,
        ban_type=ban_type,
        condition_key=key,
        locked_at=_utcnow(),
    )
    db.session.add(ban)
    db.session.commit()
    return ban


def submit_uma_ban(
    match_id: int,
    user_id: int,
    uma_character_id: int,
    *,
    uma_outfit_id: int | None = None,
) -> DraftMatchBan:
    match = get_match(match_id)
    _require_participant(match, user_id)
    if match.status != DraftMatchStatus.UMA_BAN_PHASE:
        raise InvalidMatchStateError(f"cannot ban Uma in state {match.status}")

    char = db.session.get(UmaCharacter, uma_character_id)
    if char is None or not char.enabled:
        raise UnknownBanTargetError(f"unknown character {uma_character_id}")

    # Bans must always target a specific costume — banning an entire
    # character would block all of its costumes including ones the
    # opponent might otherwise have legitimately picked.
    if uma_outfit_id is None:
        raise UnknownBanTargetError("ban must target a specific costume")

    from ..models import UmaOutfit

    outfit = db.session.get(UmaOutfit, uma_outfit_id)
    if outfit is None or not outfit.enabled:
        raise UnknownBanTargetError(f"unknown outfit {uma_outfit_id}")
    if outfit.uma_character_id != uma_character_id:
        raise UnknownBanTargetError(
            "outfit does not belong to the chosen character"
        )

    existing = db.session.scalars(
        select(DraftMatchBan)
        .where(DraftMatchBan.draft_match_id == match_id)
        .where(DraftMatchBan.user_id == user_id)
        .where(DraftMatchBan.ban_type == DraftBanType.UMA)
    ).first()
    if existing is not None:
        raise DuplicateBanError()

    ban = DraftMatchBan(
        draft_match_id=match_id,
        user_id=user_id,
        ban_type=DraftBanType.UMA,
        uma_character_id=uma_character_id,
        uma_outfit_id=uma_outfit_id,
        locked_at=_utcnow(),
    )
    db.session.add(ban)
    db.session.commit()

    if both_players_uma_banned(match_id):
        match.status = DraftMatchStatus.ROOM_CODE_PENDING
        db.session.commit()
    return ban


def list_bans(match_id: int) -> Sequence[DraftMatchBan]:
    return list(
        db.session.scalars(
            select(DraftMatchBan).where(DraftMatchBan.draft_match_id == match_id)
        )
    )


def both_players_track_banned(match_id: int) -> bool:
    match = get_match(match_id)
    if match.opponent_user_id is None:
        return False
    rows = db.session.scalars(
        select(DraftMatchBan)
        .where(DraftMatchBan.draft_match_id == match_id)
        .where(DraftMatchBan.ban_type.in_(_TRACK_BAN_TYPES))
    ).all()
    by_user = {b.user_id for b in rows}
    return match.host_user_id in by_user and match.opponent_user_id in by_user


def both_players_uma_banned(match_id: int) -> bool:
    match = get_match(match_id)
    if match.opponent_user_id is None:
        return False
    rows = db.session.scalars(
        select(DraftMatchBan)
        .where(DraftMatchBan.draft_match_id == match_id)
        .where(DraftMatchBan.ban_type == DraftBanType.UMA)
    ).all()
    by_user = {b.user_id for b in rows}
    return match.host_user_id in by_user and match.opponent_user_id in by_user


def randomize_preset(
    match_id: int, *, rng: random.Random | None = None
) -> DraftMatch:
    match = get_match(match_id)
    if match.status != DraftMatchStatus.TRACK_BAN_PHASE:
        raise InvalidMatchStateError(f"cannot randomize in state {match.status}")
    if not both_players_track_banned(match_id):
        raise InvalidMatchStateError("waiting on both players' track bans")

    venues: set[str] = set()
    directions: set[str] = set()
    distance_cats: set[str] = set()
    surfaces: set[str] = set()
    for b in list_bans(match_id):
        if b.ban_type == DraftBanType.VENUE and b.condition_key:
            venues.add(b.condition_key)
        elif b.ban_type == DraftBanType.DIRECTION and b.condition_key:
            directions.add(b.condition_key)
        elif b.ban_type == DraftBanType.DISTANCE_CATEGORY and b.condition_key:
            distance_cats.add(b.condition_key)
        elif b.ban_type == DraftBanType.SURFACE and b.condition_key:
            surfaces.add(b.condition_key)

    pool_presets = list(db.session.scalars(select(RacePreset)))
    try:
        chosen = pick_preset(
            pool_presets,
            match.preset_pool,
            Bans(
                venues=frozenset(venues),
                directions=frozenset(directions),
                distance_categories=frozenset(distance_cats),
                surfaces=frozenset(surfaces),
            ),
            rng=rng,
            min_max_runners=2 * match.umas_per_player,
        )
    except RandomizerError:
        match.status = DraftMatchStatus.RANDOMIZATION_FAILED
        db.session.commit()
        raise

    match.selected_preset_id = chosen.id
    match.status = DraftMatchStatus.UMA_BAN_PHASE
    db.session.commit()
    return match


def set_room_code(
    match_id: int,
    code: str,
    *,
    now: datetime | None = None,
    notify: bool = True,
) -> DraftMatch:
    match = get_match(match_id)
    if match.status not in (
        DraftMatchStatus.ROOM_CODE_PENDING,
        DraftMatchStatus.ROOM_CODE_AVAILABLE,
        DraftMatchStatus.ROOM_CODE_EXPIRED,
    ):
        raise InvalidMatchStateError(f"cannot set room code in state {match.status}")
    code = code.strip()
    if not code:
        raise DraftError("room code is empty")
    issued = now or _utcnow()
    match.room_code = code
    match.room_code_expires_at = issued + ROOM_CODE_TTL
    match.status = DraftMatchStatus.ROOM_CODE_AVAILABLE
    db.session.commit()
    if notify:
        try:
            from ..notifications import services as notif_services

            notif_services.notify_draft_room_code(match)
        except Exception:  # noqa: BLE001
            pass
    return match


def is_room_code_expired(match: DraftMatch, *, now: datetime | None = None) -> bool:
    if match.room_code_expires_at is None:
        return False
    return _as_utc(now or _utcnow()) >= _as_utc(match.room_code_expires_at)


@dataclass(frozen=True)
class DraftResultLine:
    user_id: int
    placement: int
    uma_character_id: int | None = None
    uma_outfit_id: int | None = None
    custom_uma_name: str | None = None


def banned_uma_character_ids(match_id: int) -> set[int]:
    """Character ids that are banned with no specific outfit (whole-character bans)."""
    rows = db.session.scalars(
        select(DraftMatchBan)
        .where(DraftMatchBan.draft_match_id == match_id)
        .where(DraftMatchBan.ban_type == DraftBanType.UMA)
    ).all()
    return {
        b.uma_character_id
        for b in rows
        if b.uma_character_id is not None and b.uma_outfit_id is None
    }


def banned_uma_outfit_ids(match_id: int) -> set[int]:
    """Outfit ids that are banned (specific-outfit bans)."""
    rows = db.session.scalars(
        select(DraftMatchBan)
        .where(DraftMatchBan.draft_match_id == match_id)
        .where(DraftMatchBan.ban_type == DraftBanType.UMA)
    ).all()
    return {b.uma_outfit_id for b in rows if b.uma_outfit_id is not None}


def submit_results(
    match_id: int,
    lines: Sequence[DraftResultLine],
    *,
    confirmed_by_user_id: int,
    notify: bool = True,
) -> DraftMatch:
    match = get_match(match_id)
    if match.opponent_user_id is None:
        raise InvalidMatchStateError("match has no opponent")
    if match.status not in (
        DraftMatchStatus.ROOM_CODE_AVAILABLE,
        DraftMatchStatus.ROOM_CODE_EXPIRED,
    ):
        raise InvalidMatchStateError(f"cannot submit results in state {match.status}")
    if len(lines) < 2:
        raise DraftError("need at least both players' placements")

    placements = [line.placement for line in lines]
    if len(set(placements)) != len(placements):
        raise DraftError("duplicate placements")

    banned_chars = banned_uma_character_ids(match_id)
    banned_outfits = banned_uma_outfit_ids(match_id)
    for line in lines:
        if line.uma_character_id is not None and line.uma_character_id in banned_chars:
            raise BannedCharacterUsedError(
                f"character {line.uma_character_id} was banned"
            )
        if line.uma_outfit_id is not None and line.uma_outfit_id in banned_outfits:
            raise BannedCharacterUsedError(
                f"outfit {line.uma_outfit_id} was banned"
            )

    by_user_best: dict[int, int] = {}
    for line in lines:
        prev = by_user_best.get(line.user_id)
        if prev is None or line.placement < prev:
            by_user_best[line.user_id] = line.placement

    if (
        match.host_user_id not in by_user_best
        or match.opponent_user_id not in by_user_best
    ):
        raise DraftError("each player must have at least one placement")

    host_best = by_user_best[match.host_user_id]
    opp_best = by_user_best[match.opponent_user_id]
    if host_best == opp_best:
        raise DraftError("best placements are tied; cannot determine winner")
    winner_id, loser_id = (
        (match.host_user_id, match.opponent_user_id)
        if host_best < opp_best
        else (match.opponent_user_id, match.host_user_id)
    )

    for line in lines:
        db.session.add(
            DraftRaceResult(
                draft_match_id=match_id,
                user_id=line.user_id,
                uma_character_id=line.uma_character_id,
                uma_outfit_id=line.uma_outfit_id,
                custom_uma_name=line.custom_uma_name,
                placement=line.placement,
                confirmed_by_user_id=confirmed_by_user_id,
            )
        )

    winner_rating = current_rating(winner_id, match.season_id)
    loser_rating = current_rating(loser_id, match.season_id)
    winner_change, loser_change = apply_match(
        winner_rating, loser_rating, outcome_a=1.0, k=DEFAULT_K
    )
    db.session.add(
        DraftEloChange(
            draft_match_id=match_id,
            season_id=match.season_id,
            user_id=winner_id,
            opponent_user_id=loser_id,
            rating_before=winner_change.rating_before,
            rating_after=winner_change.rating_after,
            delta=winner_change.delta,
            outcome=winner_change.outcome,
        )
    )
    db.session.add(
        DraftEloChange(
            draft_match_id=match_id,
            season_id=match.season_id,
            user_id=loser_id,
            opponent_user_id=winner_id,
            rating_before=loser_change.rating_before,
            rating_after=loser_change.rating_after,
            delta=loser_change.delta,
            outcome=loser_change.outcome,
        )
    )

    match.winner_user_id = winner_id
    match.loser_user_id = loser_id
    match.completed_at = _utcnow()
    match.status = DraftMatchStatus.COMPLETED
    db.session.commit()

    if notify:
        try:
            from ..notifications import services as notif_services

            notif_services.notify_draft_results(
                match,
                winner_username=match.host.username
                if winner_id == match.host_user_id
                else (match.opponent.username if match.opponent else "?"),
                loser_username=match.host.username
                if loser_id == match.host_user_id
                else (match.opponent.username if match.opponent else "?"),
                winner_delta=winner_change.delta,
                loser_delta=loser_change.delta,
            )
        except Exception:  # noqa: BLE001
            pass
    return match


def current_rating(user_id: int, season_id: int) -> int:
    latest = db.session.scalars(
        select(DraftEloChange)
        .where(DraftEloChange.user_id == user_id)
        .where(DraftEloChange.season_id == season_id)
        .order_by(DraftEloChange.id.desc())
        .limit(1)
    ).first()
    return latest.rating_after if latest is not None else DEFAULT_RATING


@dataclass(frozen=True)
class EloLadderRow:
    user_id: int
    username: str
    rating: int
    matches: int
    wins: int


def season_elo_ladder(season_id: int, *, limit: int | None = None) -> list[EloLadderRow]:
    from sqlalchemy import case as sql_case

    from ..models import User

    latest_subq = (
        select(
            DraftEloChange.user_id.label("user_id"),
            func.max(DraftEloChange.id).label("max_id"),
        )
        .where(DraftEloChange.season_id == season_id)
        .group_by(DraftEloChange.user_id)
        .subquery()
    )
    stmt = (
        select(
            User.id.label("user_id"),
            User.username.label("username"),
            DraftEloChange.rating_after.label("rating"),
        )
        .join(DraftEloChange, DraftEloChange.user_id == User.id)
        .join(latest_subq, latest_subq.c.max_id == DraftEloChange.id)
        .where(DraftEloChange.season_id == season_id)
        .group_by(User.id, User.username, DraftEloChange.rating_after)
        .order_by(DraftEloChange.rating_after.desc(), User.username.asc())
    )
    rows = db.session.execute(stmt).all()

    wins_stmt = (
        select(
            DraftEloChange.user_id,
            func.sum(sql_case((DraftEloChange.outcome == 1.0, 1), else_=0)).label("wins"),
            func.count(DraftEloChange.id).label("matches"),
        )
        .where(DraftEloChange.season_id == season_id)
        .group_by(DraftEloChange.user_id)
    )
    stats = {
        r.user_id: (int(r.wins or 0), int(r.matches or 0))
        for r in db.session.execute(wins_stmt).all()
    }

    out = [
        EloLadderRow(
            user_id=r.user_id,
            username=r.username,
            rating=int(r.rating),
            matches=stats.get(r.user_id, (0, 0))[1],
            wins=stats.get(r.user_id, (0, 0))[0],
        )
        for r in rows
    ]
    if limit is not None:
        out = out[:limit]
    return out


def list_matches_for_user(user_id: int) -> Sequence[DraftMatch]:
    return list(
        db.session.scalars(
            select(DraftMatch)
            .where(
                (DraftMatch.host_user_id == user_id)
                | (DraftMatch.opponent_user_id == user_id)
            )
            .order_by(DraftMatch.created_at.desc())
        )
    )
