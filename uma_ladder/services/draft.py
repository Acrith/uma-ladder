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


def _k_multiplier(
    winner_score: int, loser_score: int, umas_per_player: int
) -> float:
    """Scale K by SIGNED team-placement spread. Drives the
    *magnitude* of the rating delta — the *winner* is decided
    separately by best individual placement (see
    _apply_result_decision).

    Signed margin = loser_score - winner_score, so:

      - Positive when the winner's team also had the lower sum
        (clean sweep — winner's umas placed better overall).
      - Zero when sums tied (winner held the #1 finisher but
        otherwise the teams matched).
      - Negative when the winner held #1 but their other umas
        placed worse than the loser's. Caters to debuffer-style
        playstyles where one strong uma carries while teammates
        place at the back.

    1v1 stays at 1.0 — sum margin is just the placement gap, and
    the standard ELO already reflects that.

      2v2  1+2 vs 3+4 → margin +4 / 4 → mult 1.50  (sweep)
      2v2  1+3 vs 2+4 → margin +2 / 4 → mult 1.00  (mid)
      2v2  1+4 vs 2+3 → margin  0     → mult 0.50  (coin-flip)
      2v2  1+8 vs 2+3 → margin -4 / 4 → mult floor (scrap; bots fill)
      3v3  1+2+3 vs 4+5+6 → margin +9 / 9 → mult 1.50
      3v3  1+3+5 vs 2+4+6 → margin +3 / 9 → mult ~0.83
      3v3  1+5+6 vs 2+3+4 → margin -3 / 9 → mult ~0.17  (scrap)

    Capped at [0.1, 1.5]:
      - Floor 0.1 keeps the rating delta ≥ ~2 even on extreme
        scraps; ELO that doesn't move feels broken.
      - Cap 1.5 prevents 9-uma rooms (with bot-filled gaps) from
        producing absurd swings when player teams are far apart.
    """
    if umas_per_player <= 1:
        return 1.0
    margin = loser_score - winner_score
    max_positive = umas_per_player * umas_per_player
    multiplier = 0.5 + margin / max_positive
    return max(0.1, min(1.5, multiplier))


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
    # Roll race-day conditions at the same time the preset locks. Snowy
    # is restricted to Winter inside the helper. Pass the same RNG so a
    # deterministic test seed reproduces both selections together.
    from . import track_conditions as track_conditions_service

    season, weather, ground = track_conditions_service.roll_random(rng=rng)
    match.race_season = season
    match.weather = weather
    match.ground_condition = ground
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

    # Aggregate placements per side. PR-I2: in a 2v2 / 3v3 draft each
    # player owns multiple umas, so the "team score" is the sum of
    # their umas' placements (lower = better). This rewards strong
    # placement spreads — 1+2 vs 3+4 (sum 3 vs 7) is a decisive win;
    # 1+4 vs 2+3 (sum 5 vs 5) is a near-tie even though one side has
    # the #1 finisher. Tiebreak on best individual placement so a
    # match is never undecidable when sums collide.
    winner_change, loser_change = _apply_result_decision(
        match, lines, confirmed_by_user_id=confirmed_by_user_id
    )

    if notify:
        try:
            from ..notifications import services as notif_services

            notif_services.notify_draft_results(
                match,
                winner_username=match.host.username
                if match.winner_user_id == match.host_user_id
                else (match.opponent.username if match.opponent else "?"),
                loser_username=match.host.username
                if match.loser_user_id == match.host_user_id
                else (match.opponent.username if match.opponent else "?"),
                winner_delta=winner_change.delta,
                loser_delta=loser_change.delta,
                placements=lines,
            )
        except Exception:  # noqa: BLE001
            pass
    return match


def _apply_result_decision(
    match: DraftMatch,
    lines: Sequence[DraftResultLine],
    *,
    confirmed_by_user_id: int,
):
    """Determine winner + ELO magnitude, insert DraftRaceResult and
    DraftEloChange rows, flip match status to COMPLETED. Shared by
    submit_results and edit_results.

    Two separate axes (PR-I4 corrected from PR-I2):

    - **Winner** = side with the best (lowest) individual placement.
      Whoever has the #1 finisher (or, failing that, the lowest of
      either team's umas) wins, regardless of team sum. Placements
      are unique across the race so this is always decisive.

    - **ELO magnitude** = scaled by team-sum margin. A clean sweep
      (1+2 vs 3+4) and a coin-flip (1+4 vs 2+3) award the same WIN
      to the same player, but the K-multiplier scales the rating
      delta — decisive wins net ~1.5× DEFAULT_K, near-ties net ~0.5×.

    Returns (winner_change, loser_change) so the caller can pass
    the deltas to a notification embed without re-querying.
    """
    by_user_lines: dict[int, list[int]] = {}
    for line in lines:
        by_user_lines.setdefault(line.user_id, []).append(line.placement)

    if (
        match.host_user_id not in by_user_lines
        or match.opponent_user_id not in by_user_lines
    ):
        raise DraftError("each player must have at least one placement")

    host_score = sum(by_user_lines[match.host_user_id])
    opp_score = sum(by_user_lines[match.opponent_user_id])
    host_best = min(by_user_lines[match.host_user_id])
    opp_best = min(by_user_lines[match.opponent_user_id])
    if host_best == opp_best:
        # Mathematically only reachable when a placement is shared
        # between the teams, which the dedupe check upstream rejects.
        # Keep the guard so we never silently pick the wrong winner.
        raise DraftError("two teams share the best placement")
    winner_id, loser_id = (
        (match.host_user_id, match.opponent_user_id)
        if host_best < opp_best
        else (match.opponent_user_id, match.host_user_id)
    )

    for line in lines:
        db.session.add(
            DraftRaceResult(
                draft_match_id=match.id,
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
    winner_score = host_score if winner_id == match.host_user_id else opp_score
    loser_score = opp_score if winner_id == match.host_user_id else host_score
    multiplier = _k_multiplier(winner_score, loser_score, match.umas_per_player)
    effective_k = max(1, int(round(DEFAULT_K * multiplier)))
    winner_change, loser_change = apply_match(
        winner_rating, loser_rating, outcome_a=1.0, k=effective_k
    )
    db.session.add(
        DraftEloChange(
            draft_match_id=match.id,
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
            draft_match_id=match.id,
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
    return winner_change, loser_change


def list_results_for_match(match_id: int) -> list[DraftRaceResult]:
    """Result rows ordered by placement (ascending — winner first)."""
    return list(
        db.session.scalars(
            select(DraftRaceResult)
            .where(DraftRaceResult.draft_match_id == match_id)
            .order_by(DraftRaceResult.placement.asc())
        )
    )


def list_elo_changes_for_match(match_id: int) -> list[DraftEloChange]:
    return list(
        db.session.scalars(
            select(DraftEloChange).where(DraftEloChange.draft_match_id == match_id)
        )
    )


def validate_completeness(
    match: DraftMatch, lines: Sequence[DraftResultLine]
) -> list[str]:
    """Soft-warning check: returns a list of human-readable warnings
    when the submitted result lines don't match what the match
    contract expects. A 2v2 should have 4 lines (2 per player); an
    uneven submission is allowed but flagged so the UI can prompt
    "are you sure?" — never raises.
    """
    warnings: list[str] = []
    expected_per_player = match.umas_per_player
    by_user: dict[int, int] = {}
    for line in lines:
        by_user[line.user_id] = by_user.get(line.user_id, 0) + 1

    host_count = by_user.get(match.host_user_id, 0)
    opp_count = (
        by_user.get(match.opponent_user_id, 0)
        if match.opponent_user_id is not None
        else 0
    )
    host_label = match.host.username if match.host else f"user {match.host_user_id}"
    opp_label = (
        match.opponent.username
        if match.opponent
        else (
            f"user {match.opponent_user_id}"
            if match.opponent_user_id is not None
            else "opponent"
        )
    )
    if host_count != expected_per_player:
        warnings.append(
            f"{host_label} has {host_count} uma placement(s); "
            f"a {expected_per_player}v{expected_per_player} match expects "
            f"{expected_per_player}."
        )
    if opp_count != expected_per_player:
        warnings.append(
            f"{opp_label} has {opp_count} uma placement(s); "
            f"a {expected_per_player}v{expected_per_player} match expects "
            f"{expected_per_player}."
        )
    return warnings


def edit_results(
    match_id: int,
    lines: Sequence[DraftResultLine],
    *,
    by_user_id: int,
) -> DraftMatch:
    """Admin correction path — wipe a completed match's results and
    ELO change rows, then apply new ones. The match's per-row
    rating_before/rating_after on the new rows reflects the *current*
    summed rating; ELO snapshots on later matches in the same season
    are not retroactively recomputed (a chain rebuild is out of scope
    for this PR).

    Caller is responsible for verifying admin role; this layer only
    guards state.
    """
    match = get_match(match_id)
    if match.status != DraftMatchStatus.COMPLETED:
        raise InvalidMatchStateError(
            f"can only edit results on a completed match (status: {match.status})"
        )
    if not lines:
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

    # Snapshot for audit before we wipe.
    old_results = list_results_for_match(match_id)
    old_summary = ", ".join(
        f"{r.placement}={r.user_id}" for r in old_results
    ) or "(none)"

    # Wipe existing results + elo. CASCADE handles result skills if any.
    for r in old_results:
        db.session.delete(r)
    for c in list_elo_changes_for_match(match_id):
        db.session.delete(c)
    # Need to drop COMPLETED guard so _apply_result_decision can re-set it.
    match.winner_user_id = None
    match.loser_user_id = None
    match.completed_at = None
    match.status = DraftMatchStatus.ROOM_CODE_AVAILABLE  # transient
    db.session.commit()

    _apply_result_decision(match, lines, confirmed_by_user_id=by_user_id)

    # Audit trail.
    try:
        from . import admin_audit

        admin_audit.log_action(
            actor_user_id=by_user_id,
            action="draft_match_edit_results",
            target_kind="draft_match",
            target_id=match.id,
            details=f"old: {old_summary}",
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
class EloSummary:
    """Compact view of a user's Draft Elo state for profile rendering.

    `rating` is the current rating (DEFAULT_RATING if no matches yet),
    `last_delta` is signed (+ or -) from the most recent change, and
    `match_count` is the total number of changes recorded for the user
    in the season — i.e. matches played that produced an Elo update.
    """

    rating: int
    last_delta: int
    match_count: int


def elo_summary_for_user(
    user_id: int, *, season_id: int | None = None
) -> EloSummary | None:
    """Resolve a user's current Elo + last delta + match count.

    `season_id=None` means "use the active season" — returns None when
    no season is active (we don't have a coherent rating to show in
    that case). Use this from profile / dashboard read paths.
    """
    if season_id is None:
        from .seasons import get_active_season

        season = get_active_season()
        if season is None:
            return None
        season_id = season.id

    latest = db.session.scalars(
        select(DraftEloChange)
        .where(DraftEloChange.user_id == user_id)
        .where(DraftEloChange.season_id == season_id)
        .order_by(DraftEloChange.id.desc())
        .limit(1)
    ).first()
    if latest is None:
        return EloSummary(rating=DEFAULT_RATING, last_delta=0, match_count=0)
    count = db.session.scalar(
        select(func.count(DraftEloChange.id))
        .where(DraftEloChange.user_id == user_id)
        .where(DraftEloChange.season_id == season_id)
    ) or 0
    return EloSummary(
        rating=latest.rating_after,
        last_delta=latest.delta,
        match_count=count,
    )


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


class NotAParticipantOfMatchError(DraftError):
    pass


def submit_forfeit(
    match_id: int,
    *,
    forfeiter_user_id: int,
    by_user_id: int,
    reason: str | None = None,
) -> DraftMatch:
    """Close a match by forfeit — the *other* player wins. Used for
    rule violations (banned uma in the room) and ghosting. Skips the
    placement validation and the banned-uma check that submit_results
    enforces; that's the whole point of this path.

    Records:
    - DraftRaceResult is NOT written (no in-game placements to capture).
    - winner_user_id / loser_user_id are set normally so the ladder
      treats this as a played match.
    - forfeit_user_id + forfeit_reason explain *why* there are no
      placement rows.
    - Elo applied with K=DEFAULT_K just like a normal loss; this is a
      deliberate choice — using a banned uma in the room is a real
      loss, not a no-result.

    Permissions are enforced at the route layer.
    """
    match = get_match(match_id)
    if match.status == DraftMatchStatus.COMPLETED:
        raise InvalidMatchStateError("match is already completed")
    if match.status == DraftMatchStatus.CANCELLED:
        raise InvalidMatchStateError("cannot forfeit a cancelled match")
    if match.opponent_user_id is None:
        raise InvalidMatchStateError("match has no opponent")
    if forfeiter_user_id not in (match.host_user_id, match.opponent_user_id):
        raise NotAParticipantOfMatchError(
            f"user {forfeiter_user_id} is not in match {match_id}"
        )

    winner_id = (
        match.opponent_user_id
        if forfeiter_user_id == match.host_user_id
        else match.host_user_id
    )
    loser_id = forfeiter_user_id

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
    match.forfeit_user_id = forfeiter_user_id
    match.forfeit_reason = (reason or "").strip() or None
    match.completed_at = _utcnow()
    match.status = DraftMatchStatus.COMPLETED
    db.session.commit()

    try:
        from . import admin_audit

        admin_audit.log_action(
            actor_user_id=by_user_id,
            action="draft_match_forfeit",
            target_user_id=forfeiter_user_id,
            target_kind="draft_match",
            target_id=match.id,
            details=match.forfeit_reason,
        )
    except Exception:  # noqa: BLE001
        pass

    # Reuse the standard DRAFT_RESULTS notification — a forfeit IS a
    # result, the embed copy just happens to mention forfeit reason.
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

    del by_user_id  # accepted for symmetry / audit context
    return match


def cancel_match(match_id: int, *, by_user_id: int) -> DraftMatch:
    """Organiser action — wipes a match short of completion. Refuses to
    touch already-completed matches (those represent applied Elo and need
    a separate rollback path). Caller is responsible for verifying the
    organiser role; this layer only guards state."""
    match = get_match(match_id)
    if match.status == DraftMatchStatus.COMPLETED:
        raise InvalidMatchStateError("cannot cancel a completed match")
    if match.status == DraftMatchStatus.CANCELLED:
        raise InvalidMatchStateError("match is already cancelled")
    match.status = DraftMatchStatus.CANCELLED
    match.cancelled_at = _utcnow()
    match.cancelled_by_user_id = by_user_id
    db.session.commit()
    _notify_match_cancelled(match, by_user_id)
    try:
        from . import admin_audit

        admin_audit.log_action(
            actor_user_id=by_user_id,
            action="draft_match_cancel",
            target_kind="draft_match",
            target_id=match.id,
        )
    except Exception:  # noqa: BLE001
        pass
    return match


def _notify_match_cancelled(match: DraftMatch, by_user_id: int) -> None:
    """Best-effort: never raises into the caller."""
    try:
        from ..models import User
        from ..notifications import services as notif_services

        actor = db.session.get(User, by_user_id)
        notif_services.notify_draft_match_cancelled(
            match, cancelled_by_username=actor.username if actor else None
        )
    except Exception:  # noqa: BLE001
        pass


def delete_match(match_id: int, *, by_user_id: int) -> None:
    """Hard-delete a draft match and its bans / results / elo changes
    via the existing CASCADE FKs.

    Admin-only — caller verifies the role. Distinct from `cancel_match`
    in that it removes the audit history of the match entirely; use
    only for smoke-test / mistake cleanup.

    Audit row written BEFORE delete so target_id still references a
    real row when the audit log is read back.
    """
    match = get_match(match_id)
    try:
        from . import admin_audit

        admin_audit.log_action(
            actor_user_id=by_user_id,
            action="draft_match_delete",
            target_kind="draft_match",
            target_id=match.id,
            details=(
                f"host_user_id={match.host_user_id} "
                f"opponent_user_id={match.opponent_user_id} "
                f"status={match.status}"
            ),
        )
    except Exception:  # noqa: BLE001
        pass
    db.session.delete(match)
    db.session.commit()


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
