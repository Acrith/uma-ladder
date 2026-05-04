from __future__ import annotations

import random
import secrets
import string
from collections.abc import Sequence
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
    DraftMatchUmaEntry,
    DraftRaceResult,
    RacePreset,
)
from .elo import DEFAULT_K, DEFAULT_RATING, apply_match
from .randomizer import Bans, RandomizerError, pick_preset

ROOM_CODE_TTL = timedelta(hours=24)
JOIN_CODE_LEN = 8
JOIN_CODE_ALPHABET = string.ascii_uppercase + string.digits


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
    umas_per_player: int
    preset_pool: str  # "g1" | "custom" | "g1+custom"


@dataclass(frozen=True)
class UmaSubmission:
    uma_character_id: int | None = None
    custom_uma_name: str | None = None
    build_nickname: str | None = None
    notes: str | None = None


def create_match(req: CreateMatchRequest) -> DraftMatch:
    if req.umas_per_player not in (2, 3):
        raise InvalidUmaCountError("umas_per_player must be 2 or 3")
    # generate a unique join code; collision is astronomically unlikely but loop anyway.
    for _ in range(10):
        code = _generate_join_code()
        if db.session.scalars(
            select(DraftMatch).where(DraftMatch.join_code == code)
        ).first() is None:
            break
    else:  # pragma: no cover — defence in depth
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
    match.status = DraftMatchStatus.SUBMITTING_UMAS
    db.session.commit()
    return match


def _require_participant(match: DraftMatch, user_id: int) -> None:
    if user_id not in (match.host_user_id, match.opponent_user_id):
        raise NotAParticipantError()


def submit_umas(
    match_id: int, user_id: int, submissions: Sequence[UmaSubmission]
) -> Sequence[DraftMatchUmaEntry]:
    match = get_match(match_id)
    _require_participant(match, user_id)
    if match.status not in (
        DraftMatchStatus.SUBMITTING_UMAS,
        DraftMatchStatus.READY_CHECK,
    ):
        raise InvalidMatchStateError(
            f"cannot submit Umas in state {match.status}"
        )
    if len(submissions) != match.umas_per_player:
        raise InvalidUmaCountError(
            f"expected {match.umas_per_player} Umas, got {len(submissions)}"
        )

    # Replace any prior submissions for this user (re-submit before lock).
    existing = db.session.scalars(
        select(DraftMatchUmaEntry)
        .where(DraftMatchUmaEntry.draft_match_id == match_id)
        .where(DraftMatchUmaEntry.user_id == user_id)
        .where(DraftMatchUmaEntry.locked_at.is_(None))
    ).all()
    for row in existing:
        db.session.delete(row)

    saved: list[DraftMatchUmaEntry] = []
    for sub in submissions:
        if sub.uma_character_id is None and not (sub.custom_uma_name or "").strip():
            raise InvalidUmaCountError("each Uma needs a character or custom name")
        entry = DraftMatchUmaEntry(
            draft_match_id=match_id,
            user_id=user_id,
            uma_character_id=sub.uma_character_id,
            custom_uma_name=sub.custom_uma_name,
            build_nickname=sub.build_nickname,
            notes=sub.notes,
        )
        db.session.add(entry)
        saved.append(entry)
    db.session.commit()
    return saved


def list_uma_entries(match_id: int) -> Sequence[DraftMatchUmaEntry]:
    return list(
        db.session.scalars(
            select(DraftMatchUmaEntry)
            .where(DraftMatchUmaEntry.draft_match_id == match_id)
            .order_by(DraftMatchUmaEntry.user_id, DraftMatchUmaEntry.id)
        )
    )


def ready_up(match_id: int, user_id: int) -> DraftMatch:
    match = get_match(match_id)
    _require_participant(match, user_id)
    if match.status not in (
        DraftMatchStatus.SUBMITTING_UMAS,
        DraftMatchStatus.READY_CHECK,
    ):
        raise InvalidMatchStateError(f"cannot ready in state {match.status}")

    entries = list_uma_entries(match_id)
    by_user: dict[int, list[DraftMatchUmaEntry]] = {}
    for e in entries:
        by_user.setdefault(e.user_id, []).append(e)

    user_entries = by_user.get(user_id, [])
    if len(user_entries) != match.umas_per_player:
        raise InvalidUmaCountError(
            f"submit {match.umas_per_player} Umas before readying"
        )
    now = _utcnow()
    for e in user_entries:
        if e.locked_at is None:
            e.locked_at = now

    # If both players have locked entries, advance to ban_phase.
    locked_users = {
        uid
        for uid, items in by_user.items()
        if all(i.locked_at is not None or uid == user_id for i in items)
    }
    if (
        match.host_user_id in locked_users
        and match.opponent_user_id is not None
        and match.opponent_user_id in locked_users
    ):
        match.status = DraftMatchStatus.BAN_PHASE
    else:
        match.status = DraftMatchStatus.READY_CHECK
    db.session.commit()
    return match


def submit_bans(
    match_id: int,
    user_id: int,
    *,
    banned_uma_entry_id: int,
    track_ban_type: str,
    track_condition_key: str,
) -> Sequence[DraftMatchBan]:
    match = get_match(match_id)
    _require_participant(match, user_id)
    if match.status != DraftMatchStatus.BAN_PHASE:
        raise InvalidMatchStateError(f"cannot ban in state {match.status}")

    if track_ban_type not in (
        DraftBanType.DIRECTION,
        DraftBanType.DISTANCE_CATEGORY,
        DraftBanType.VENUE,
        DraftBanType.SURFACE,
    ):
        raise UnknownBanTargetError(f"track_ban_type must be a track condition, got {track_ban_type}")

    target_entry = db.session.get(DraftMatchUmaEntry, banned_uma_entry_id)
    if target_entry is None or target_entry.draft_match_id != match_id:
        raise UnknownBanTargetError("banned uma entry not part of this match")
    if target_entry.user_id == user_id:
        raise UnknownBanTargetError("you cannot ban your own Uma")

    # Reject re-bans by the same user.
    existing = db.session.scalars(
        select(DraftMatchBan)
        .where(DraftMatchBan.draft_match_id == match_id)
        .where(DraftMatchBan.user_id == user_id)
    ).all()
    if existing:
        raise DuplicateBanError()

    now = _utcnow()
    uma_ban = DraftMatchBan(
        draft_match_id=match_id,
        user_id=user_id,
        ban_type=DraftBanType.UMA,
        banned_uma_entry_id=banned_uma_entry_id,
        locked_at=now,
    )
    track_ban = DraftMatchBan(
        draft_match_id=match_id,
        user_id=user_id,
        ban_type=track_ban_type,
        condition_key=track_condition_key,
        locked_at=now,
    )
    target_entry.is_banned = True
    db.session.add_all([uma_ban, track_ban])
    db.session.commit()
    return [uma_ban, track_ban]


def list_bans(match_id: int) -> Sequence[DraftMatchBan]:
    return list(
        db.session.scalars(
            select(DraftMatchBan).where(DraftMatchBan.draft_match_id == match_id)
        )
    )


def both_players_banned(match_id: int) -> bool:
    match = get_match(match_id)
    if match.opponent_user_id is None:
        return False
    bans = list_bans(match_id)
    by_user: dict[int, list[DraftMatchBan]] = {}
    for b in bans:
        by_user.setdefault(b.user_id, []).append(b)
    needed = (match.host_user_id, match.opponent_user_id)
    return all(uid in by_user and len(by_user[uid]) >= 2 for uid in needed)


def randomize_preset(
    match_id: int, *, rng: random.Random | None = None
) -> DraftMatch:
    match = get_match(match_id)
    if match.status != DraftMatchStatus.BAN_PHASE:
        raise InvalidMatchStateError(f"cannot randomize in state {match.status}")
    if not both_players_banned(match_id):
        raise InvalidMatchStateError("waiting on both players' bans")

    # Aggregate track-condition bans.
    bans_query = list_bans(match_id)
    venues: set[str] = set()
    directions: set[str] = set()
    distance_cats: set[str] = set()
    surfaces: set[str] = set()
    for b in bans_query:
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
        )
    except RandomizerError as exc:
        match.status = DraftMatchStatus.RANDOMIZATION_FAILED
        db.session.commit()
        raise exc

    match.selected_preset_id = chosen.id
    match.status = DraftMatchStatus.ROOM_CODE_PENDING
    db.session.commit()
    return match


def set_room_code(match_id: int, code: str, *, now: datetime | None = None) -> DraftMatch:
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
    return match


def is_room_code_expired(match: DraftMatch, *, now: datetime | None = None) -> bool:
    if match.room_code_expires_at is None:
        return False
    return _as_utc(now or _utcnow()) >= _as_utc(match.room_code_expires_at)


@dataclass(frozen=True)
class DraftResultLine:
    user_id: int
    placement: int
    uma_entry_id: int | None = None


def submit_results(
    match_id: int,
    lines: Sequence[DraftResultLine],
    *,
    confirmed_by_user_id: int,
) -> DraftMatch:
    match = get_match(match_id)
    if match.opponent_user_id is None:
        raise InvalidMatchStateError("match has no opponent")
    if match.status not in (
        DraftMatchStatus.ROOM_CODE_AVAILABLE,
        DraftMatchStatus.ROOM_CODE_EXPIRED,
        DraftMatchStatus.RESULTS_PENDING,
    ):
        raise InvalidMatchStateError(f"cannot submit results in state {match.status}")
    if len(lines) < 2:
        raise DraftError("need at least both players' placements")
    placements = [line.placement for line in lines]
    if len(set(placements)) != len(placements):
        raise DraftError("duplicate placements")

    # Build per-player best (lowest = best) placement using only their non-banned entries.
    by_user_best: dict[int, int] = {}
    for line in lines:
        prev = by_user_best.get(line.user_id)
        if prev is None or line.placement < prev:
            by_user_best[line.user_id] = line.placement

    if match.host_user_id not in by_user_best or match.opponent_user_id not in by_user_best:
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
                uma_entry_id=line.uma_entry_id,
                placement=line.placement,
                confirmed_by_user_id=confirmed_by_user_id,
            )
        )

    # Apply Elo using each player's current rating.
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
    return match


def current_rating(user_id: int, season_id: int) -> int:
    """Return the user's current rating for this season, or DEFAULT_RATING."""
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
    from sqlalchemy import case as sql_case  # local import to avoid top-level shadow

    from ..models import User

    # Latest change per (user, season). SQLite-portable: take MAX(id) per user.
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
            func.count(DraftEloChange.id).label("matches_for_user"),
        )
        .join(DraftEloChange, DraftEloChange.user_id == User.id)
        .join(latest_subq, latest_subq.c.max_id == DraftEloChange.id)
        .where(DraftEloChange.season_id == season_id)
        .group_by(User.id, User.username, DraftEloChange.rating_after)
        .order_by(DraftEloChange.rating_after.desc(), User.username.asc())
    )
    rows = db.session.execute(stmt).all()

    # Wins query (separate, simpler): count outcome == 1 per user.
    wins_stmt = (
        select(
            DraftEloChange.user_id,
            func.sum(sql_case((DraftEloChange.outcome == 1.0, 1), else_=0)).label("wins"),
            func.count(DraftEloChange.id).label("matches"),
        )
        .where(DraftEloChange.season_id == season_id)
        .group_by(DraftEloChange.user_id)
    )
    stats = {r.user_id: (int(r.wins or 0), int(r.matches or 0)) for r in db.session.execute(wins_stmt).all()}

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
