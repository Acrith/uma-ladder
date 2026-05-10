from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select

from ..extensions import db
from ..models import UmaCharacter, UmaOutfit, User, UserProfile


class ProfileError(Exception):
    pass


class UnknownOshiError(ProfileError):
    pass


class UnknownOutfitError(ProfileError):
    pass


class UnknownAvatarBorderError(ProfileError):
    pass


# PR-P1 — fixed allowlist of avatar border tones. Each key maps
# to a Tailwind ring + (optional) glow class set in the
# `avatar_macros.html` partial. Storing keys (not raw hex) keeps
# us in a sealed XSS surface — no inline style ever reaches the
# DOM, just class names from this allowlist.
#
# Tones come from the existing app palette (cyan / fuchsia /
# emerald / amber / rose / violet) plus 6 extras (sky / indigo /
# lime / orange / pink / slate). User picked the wider 12-tone
# palette during PR-P1 scoping. If that proves visually noisy
# the allowlist can be trimmed; existing rows with a now-removed
# tone fall back to the default border on render.
AVATAR_BORDER_PALETTE: frozenset[str] = frozenset(
    {
        "cyan",
        "fuchsia",
        "emerald",
        "amber",
        "rose",
        "violet",
        "sky",
        "indigo",
        "lime",
        "orange",
        "pink",
        "slate",
    }
)


@dataclass
class ProfileUpdate:
    display_name: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    friend_code: str | None = None
    discord_handle: str | None = None
    discord_user_id: str | None = None
    oshi_character_id: int | None = None
    oshi_outfit_id: int | None = None
    avatar_border: str | None = None


def get_or_create_profile(user: User) -> UserProfile:
    profile = db.session.scalars(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).first()
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.session.add(profile)
        db.session.commit()
    return profile


def update_profile(user: User, update: ProfileUpdate) -> UserProfile:
    profile = get_or_create_profile(user)

    if update.oshi_character_id is not None:
        oshi = db.session.get(UmaCharacter, update.oshi_character_id)
        if oshi is None or not oshi.enabled:
            raise UnknownOshiError(str(update.oshi_character_id))

    if update.oshi_outfit_id is not None:
        outfit = db.session.get(UmaOutfit, update.oshi_outfit_id)
        if outfit is None or not outfit.enabled:
            raise UnknownOutfitError(str(update.oshi_outfit_id))
        # Outfit must belong to the chosen character (or no character is chosen).
        if (
            update.oshi_character_id is not None
            and outfit.uma_character_id != update.oshi_character_id
        ):
            raise UnknownOutfitError(
                "outfit does not belong to the selected character"
            )

    # Clearing the character clears the outfit too.
    final_outfit_id = update.oshi_outfit_id
    if update.oshi_character_id is None:
        final_outfit_id = None

    # PR-P1 — avatar_border is allowlisted; reject anything not in
    # AVATAR_BORDER_PALETTE so a crafted form post can't inject an
    # arbitrary string. None / empty clears the choice (renders
    # the default border).
    if update.avatar_border is not None and update.avatar_border != "":
        if update.avatar_border not in AVATAR_BORDER_PALETTE:
            raise UnknownAvatarBorderError(update.avatar_border)
        final_avatar_border: str | None = update.avatar_border
    else:
        final_avatar_border = None

    profile.display_name = update.display_name
    profile.avatar_url = update.avatar_url
    profile.description = update.description
    profile.friend_code = update.friend_code
    profile.discord_handle = update.discord_handle
    profile.discord_user_id = update.discord_user_id
    profile.oshi_character_id = update.oshi_character_id
    profile.oshi_outfit_id = final_outfit_id
    profile.avatar_border = final_avatar_border
    db.session.commit()
    return profile


def find_user_by_username(username: str) -> User | None:
    return db.session.scalars(
        select(User).where(User.username == username.strip().lower())
    ).first()


def sync_club_id_from_trainer(profile: UserProfile, trainer) -> None:  # noqa: ANN001
    """Reconcile ``profile.club_id`` from a freshly-fetched
    ``TrainerSummary`` (PR-L1) and refresh the first-class Club
    cache (PR-M1).

    The mirror is what Club-only race visibility consults — kept on
    UserProfile so the visibility check is a single field compare,
    not a fan-out across ``uma_moe_cache`` JSON blobs. Fed naturally
    by every profile-view path that already calls
    ``uma_moe.fetch_trainer_summary``.

    Idempotent: only writes when the value actually changes, so
    repeated profile views don't generate noisy commits. The Club
    upsert is also idempotent (skips writes when the cached name
    hasn't changed and the row is fresh).

    `trainer` is typed loosely (``Any``) to avoid a circular import
    with ``services.uma_moe`` — duck-typed access of ``circle_id`` /
    ``circle_name`` is enough.
    """
    from . import clubs as clubs_service

    new_value = getattr(trainer, "circle_id", None) if trainer else None
    if profile.club_id != new_value:
        profile.club_id = new_value
        db.session.commit()

    # Always feed the trainer summary into the Club cache, even when
    # the user's club_id didn't change — the *club's* name may have
    # been renamed upstream.
    clubs_service.upsert_club_from_trainer(trainer)


@dataclass(frozen=True)
class PlayersPage:
    rows: list[tuple[User, UserProfile | None]]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


def list_players(
    *, q: str = "", page: int = 1, page_size: int = 30
) -> PlayersPage:
    """Paginated player browse for /profiles/. Joins User + UserProfile
    so the template can render avatar / display_name / oshi inline.
    Search is case-insensitive substring on username."""
    from sqlalchemy import func as sa_func

    page = max(1, page)
    page_size = max(1, page_size)

    base = select(User).order_by(User.username.asc())
    qstr = q.strip().lower()
    if qstr:
        base = base.where(sa_func.lower(User.username).contains(qstr))

    total = db.session.scalar(
        select(sa_func.count()).select_from(base.subquery())
    ) or 0
    users = list(
        db.session.scalars(
            base.limit(page_size).offset((page - 1) * page_size)
        )
    )
    # Fetch profiles in a single round trip then zip — avoids N+1.
    profiles_by_uid: dict[int, UserProfile] = {}
    if users:
        rows = db.session.scalars(
            select(UserProfile).where(
                UserProfile.user_id.in_([u.id for u in users])
            )
        ).all()
        profiles_by_uid = {p.user_id: p for p in rows}
    pairs = [(u, profiles_by_uid.get(u.id)) for u in users]
    return PlayersPage(rows=pairs, total=total, page=page, page_size=page_size)


def list_enabled_characters() -> list[UmaCharacter]:
    return list(
        db.session.scalars(
            select(UmaCharacter)
            .where(UmaCharacter.enabled.is_(True))
            .order_by(UmaCharacter.name_en)
        )
    )


def list_outfits_for_character(character_id: int) -> Sequence[UmaOutfit]:
    return list(
        db.session.scalars(
            select(UmaOutfit)
            .where(UmaOutfit.uma_character_id == character_id)
            .where(UmaOutfit.enabled.is_(True))
            .order_by(UmaOutfit.costume_id)
        )
    )


def list_all_outfits() -> Sequence[UmaOutfit]:
    """Every enabled outfit joined to its character, sorted by character
    name then costume id. Used by the draft uma-ban tile picker."""
    return list(
        db.session.scalars(
            select(UmaOutfit)
            .join(UmaCharacter, UmaCharacter.id == UmaOutfit.uma_character_id)
            .where(UmaOutfit.enabled.is_(True))
            .where(UmaCharacter.enabled.is_(True))
            .order_by(UmaCharacter.name_en, UmaOutfit.costume_id)
        )
    )


AVATAR_MAX_DIMENSION = 256


def save_avatar_upload(file, *, user: User) -> str:
    """Save an uploaded avatar image (downscaled to ≤256x256) and return
    the served URL.

    Wraps services.ocr.save_uploaded_image with purpose=AVATAR, then
    rewrites the saved file in-place via Pillow with a max-side cap so a
    full-resolution phone photo doesn't bloat every page that shows the
    avatar. The returned URL points at the public avatar serving route.
    """
    from flask import url_for
    from PIL import Image, ImageOps

    from ..models.enums import UploadPurpose
    from . import ocr as ocr_service

    image_row = ocr_service.save_uploaded_image(
        file, uploader_user_id=user.id, purpose=UploadPurpose.AVATAR
    )
    # Downscale on disk. Image.thumbnail preserves aspect ratio and
    # short-circuits if the input is already small. ImageOps.exif_transpose
    # picks up phone-camera orientation tags so portrait shots don't end up
    # sideways.
    path = ocr_service.image_path(image_row)
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail(
                (AVATAR_MAX_DIMENSION, AVATAR_MAX_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            img.save(path)
    except Exception:  # noqa: BLE001
        # Resize is best-effort; the original upload is still on disk
        # and serviceable. Don't fail the upload over a Pillow quirk.
        pass

    return url_for("profiles.serve_avatar", image_id=image_row.id)


def clear_avatar(user: User) -> UserProfile:
    """Remove the avatar reference from the profile. Doesn't delete the
    UploadedImage row — keeping the file lets the user revert by pasting
    the URL back, and storage is cheap."""
    profile = get_or_create_profile(user)
    profile.avatar_url = None
    db.session.commit()
    return profile


def resolve_oshi_image(profile: UserProfile) -> str | None:
    """Pick the best Oshi image: chosen outfit > character default > none."""
    if profile.oshi_outfit and profile.oshi_outfit.image_url:
        return profile.oshi_outfit.image_url
    if profile.oshi and profile.oshi.image_url:
        return profile.oshi.image_url
    return None


# ---------- Race history ----------


@dataclass(frozen=True)
class HistoryEntry:
    """One race result for a user — covers both draft and official."""

    kind: str  # "draft" | "official"
    placement: int
    when: object  # datetime; kept loose so callers don't need to import
    title: str
    detail_url_endpoint: str
    detail_url_kwargs: dict
    venue: str | None = None


@dataclass(frozen=True)
class HistoryPage:
    entries: list[HistoryEntry]
    total: int  # within the active kind filter
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


def _all_history_for_user(
    user_id: int, *, kind: str | None
) -> list[HistoryEntry]:
    """Internal — returns the full sorted list across the chosen kind
    filter. Per-user volume is small (a few hundred at most), so pulling
    everything into Python and sorting beats a SQL UNION ALL with two
    different result shapes."""
    from ..models import (
        DraftMatch,
        DraftRaceResult,
        OfficialRace,
        OfficialRaceResult,
    )

    entries: list[HistoryEntry] = []

    if kind in (None, "draft"):
        # In 2v2 / 3v3 each player owns N DraftRaceResult rows per
        # match (one per uma they ran). Profile history is a per-
        # MATCH timeline, not a per-uma timeline — so group rows by
        # draft_match_id and keep just the BEST placement (lowest
        # number) for the row that represents the match. Otherwise
        # a single match shows up N times on the player's history
        # page, which is what the user flagged.
        rows = list(
            db.session.scalars(
                select(DraftRaceResult)
                .where(DraftRaceResult.user_id == user_id)
                .order_by(DraftRaceResult.created_at.desc())
            )
        )
        best_per_match: dict[int, DraftRaceResult] = {}
        for r in rows:
            current = best_per_match.get(r.draft_match_id)
            if current is None or r.placement < current.placement:
                best_per_match[r.draft_match_id] = r
        for r in best_per_match.values():
            match = db.session.get(DraftMatch, r.draft_match_id)
            title = (
                match.selected_preset.name
                if match and match.selected_preset
                else f"Match #{r.draft_match_id}"
            )
            venue = (
                match.selected_preset.venue
                if match and match.selected_preset
                else None
            )
            entries.append(
                HistoryEntry(
                    kind="draft",
                    placement=r.placement,
                    when=r.created_at,
                    title=title,
                    detail_url_endpoint="draft.detail",
                    detail_url_kwargs={"match_id": r.draft_match_id},
                    venue=venue,
                )
            )

    if kind in (None, "official"):
        for r in db.session.scalars(
            select(OfficialRaceResult)
            .where(OfficialRaceResult.user_id == user_id)
            .order_by(OfficialRaceResult.created_at.desc())
        ).unique().all():
            race = db.session.get(OfficialRace, r.official_race_id)
            title = race.name if race else f"Race #{r.official_race_id}"
            venue = race.preset.venue if race and race.preset else None
            entries.append(
                HistoryEntry(
                    kind="official",
                    placement=r.placement,
                    when=r.created_at,
                    title=title,
                    detail_url_endpoint="official.detail",
                    detail_url_kwargs={"race_id": r.official_race_id},
                    venue=venue,
                )
            )

    entries.sort(key=lambda e: e.when, reverse=True)
    return entries


@dataclass(frozen=True)
class UmaUsage:
    """One row in the 'most used Umas' card. `wins` is placement==1,
    `podiums` is placement<=3, `avg_placement` is mean placement
    (rounded to 1 decimal). Counted across the chosen kind only —
    official and draft are separate cards."""

    character_id: int | None
    name: str
    image_url: str | None
    races: int
    wins: int
    podiums: int
    avg_placement: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.races if self.races else 0.0

    @property
    def podium_rate(self) -> float:
        return self.podiums / self.races if self.races else 0.0


def most_used_umas_for_user(
    user_id: int,
    *,
    kind: str,
    limit: int = 5,
    min_races: int = 3,
) -> list[UmaUsage]:
    """Top Umas by race count for the given kind ('official' | 'draft').

    Filters to characters with at least `min_races` races so a single
    accidental pick doesn't dominate the card. Custom-name results
    (no uma_character_id) are skipped — without a stable id we'd be
    counting free-text typos as separate Umas.
    """
    from sqlalchemy import case

    from ..models import (
        DraftMatch,
        DraftRaceResult,
        OfficialRace,
        OfficialRaceResult,
        UmaCharacter,
    )

    if kind not in ("official", "draft"):
        return []

    if kind == "official":
        result_cls = OfficialRaceResult
        # OfficialRaceResult has no winner_user_id concept — placement
        # alone tells us the outcome. winner = placement 1, podium <= 3.
        race_id_col = OfficialRaceResult.official_race_id
        race_cls = OfficialRace
    else:
        result_cls = DraftRaceResult
        race_id_col = DraftRaceResult.draft_match_id
        race_cls = DraftMatch

    # Aggregate in SQL, then resolve UmaCharacter once per group.
    races_count = func.count(result_cls.id).label("races")
    wins = func.sum(case((result_cls.placement == 1, 1), else_=0)).label("wins")
    podiums = func.sum(case((result_cls.placement <= 3, 1), else_=0)).label(
        "podiums"
    )
    avg_place = func.avg(result_cls.placement).label("avg_place")

    stmt = (
        select(
            result_cls.uma_character_id,
            races_count,
            wins,
            podiums,
            avg_place,
        )
        .where(result_cls.user_id == user_id)
        .where(result_cls.uma_character_id.is_not(None))
        .group_by(result_cls.uma_character_id)
        .having(races_count >= min_races)
        .order_by(races_count.desc(), avg_place.asc())
        .limit(limit)
    )
    # Reference race_cls so static checkers don't drop the import — and
    # so future per-season filtering has the join target available.
    del race_id_col, race_cls  # noqa: F841

    rows = db.session.execute(stmt).all()
    if not rows:
        return []

    char_ids = [r.uma_character_id for r in rows]
    chars = {
        c.id: c
        for c in db.session.scalars(
            select(UmaCharacter).where(UmaCharacter.id.in_(char_ids))
        )
    }

    out: list[UmaUsage] = []
    for r in rows:
        c = chars.get(r.uma_character_id)
        out.append(
            UmaUsage(
                character_id=r.uma_character_id,
                name=c.name_en if c else "?",
                image_url=c.image_url if c else None,
                races=int(r.races or 0),
                wins=int(r.wins or 0),
                podiums=int(r.podiums or 0),
                avg_placement=round(float(r.avg_place or 0), 1),
            )
        )
    return out


@dataclass(frozen=True)
class TrackBucket:
    """One row in the track-strength breakdown."""

    label: str  # e.g. "Mile" or "Turf"
    races: int
    wins: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.races if self.races else 0.0


@dataclass(frozen=True)
class TrackStrengths:
    """Combined Official + Draft view of which conditions a user wins
    in. `best_distance` / `best_surface` are the highest-WR buckets
    that clear the sample-size threshold, or None when no bucket
    clears it."""

    by_distance: list[TrackBucket]
    by_surface: list[TrackBucket]
    best_distance: TrackBucket | None
    best_surface: TrackBucket | None
    total_races: int


@dataclass(frozen=True)
class TrackStrengthsByMode:
    """Per-mode track-strengths breakdown so each card on the
    profile uses the metric appropriate to its format:

    - ``draft.wins`` counts MATCHES the user won (winner_user_id ==
      user_id, including forfeit wins). 1v1-team format → 50%
      baseline, so "win rate" is a meaningful primary signal.
    - ``official.wins`` counts PODIUM finishes (placement <= 3).
      12-18 player fields → 8-25% baseline for placement-1, which
      structurally undersells skilled players. Top-3 captures the
      "consistently competitive" signal at a comparable scale.

    Either side can be empty (`total_races == 0`) when the user
    hasn't raced that format; the template shows only the
    populated section(s).
    """

    draft: TrackStrengths
    official: TrackStrengths


def track_strengths_for_user(
    user_id: int,
    *,
    min_races: int = 3,
) -> TrackStrengthsByMode:
    """Per-mode aggregation grouped by preset's distance_category +
    surface. PR-I5: draft and official no longer share a single
    "win = placement 1" bucket — each card uses a metric scaled
    to its format. See TrackStrengthsByMode for definitions."""
    from ..models import (
        DraftMatch,
        DraftMatchStatus,
        OfficialRace,
        OfficialRaceResult,
        RacePreset,
    )

    # ---- Draft side: per-MATCH (deduped by match_id), win = match win ----
    draft_distance: dict[str, dict[str, int]] = {}
    draft_surface: dict[str, dict[str, int]] = {}
    draft_rows = db.session.execute(
        select(
            RacePreset.distance_category,
            RacePreset.surface,
            DraftMatch.winner_user_id,
        )
        .join(RacePreset, RacePreset.id == DraftMatch.selected_preset_id)
        .where(
            (DraftMatch.host_user_id == user_id)
            | (DraftMatch.opponent_user_id == user_id)
        )
        .where(DraftMatch.status == DraftMatchStatus.COMPLETED)
    ).all()
    for cat, surf, winner_id in draft_rows:
        won = winner_id == user_id
        if cat:
            _bump_bucket(draft_distance, cat, won)
        if surf:
            _bump_bucket(draft_surface, surf, won)

    # ---- Official side: per-result row, "win" = podium (placement <= 3) ----
    official_distance: dict[str, dict[str, int]] = {}
    official_surface: dict[str, dict[str, int]] = {}
    official_rows = db.session.execute(
        select(
            RacePreset.distance_category,
            RacePreset.surface,
            OfficialRaceResult.placement,
        )
        .join(OfficialRace, OfficialRace.id == OfficialRaceResult.official_race_id)
        .join(RacePreset, RacePreset.id == OfficialRace.preset_id)
        .where(OfficialRaceResult.user_id == user_id)
    ).all()
    for cat, surf, place in official_rows:
        podium = place is not None and place <= 3
        if cat:
            _bump_bucket(official_distance, cat, podium)
        if surf:
            _bump_bucket(official_surface, surf, podium)

    return TrackStrengthsByMode(
        draft=_assemble_track_strengths(
            draft_distance, draft_surface, min_races=min_races
        ),
        official=_assemble_track_strengths(
            official_distance, official_surface, min_races=min_races
        ),
    )


def _bump_bucket(
    table: dict[str, dict[str, int]], key: str, won: bool
) -> None:
    slot = table.setdefault(key, {"races": 0, "wins": 0})
    slot["races"] += 1
    if won:
        slot["wins"] += 1


def _assemble_track_strengths(
    distance_counts: dict[str, dict[str, int]],
    surface_counts: dict[str, dict[str, int]],
    *,
    min_races: int,
) -> TrackStrengths:
    """Stable display order + best-bucket pick. Same shape for both
    modes; the *meaning* of `wins` is set by the caller (matches
    won for draft, podiums for official)."""
    distance_order = ("Sprint", "Mile", "Medium", "Long")
    surface_order = ("Turf", "Dirt")

    def _ordered(table: dict[str, dict[str, int]], order: tuple[str, ...]) -> list[TrackBucket]:
        seen = set(table.keys())
        out = [
            TrackBucket(label=k, races=table[k]["races"], wins=table[k]["wins"])
            for k in order
            if k in seen
        ]
        for k in seen - set(order):
            out.append(
                TrackBucket(label=k, races=table[k]["races"], wins=table[k]["wins"])
            )
        return out

    by_distance = _ordered(distance_counts, distance_order)
    by_surface = _ordered(surface_counts, surface_order)

    def _best(buckets: list[TrackBucket]) -> TrackBucket | None:
        eligible = [b for b in buckets if b.races >= min_races]
        if not eligible:
            return None
        # Highest win-rate; tiebreak on race count so a 100% on 3 races
        # doesn't beat 95% on 50.
        return max(eligible, key=lambda b: (b.win_rate, b.races))

    total = sum(b.races for b in by_distance)
    return TrackStrengths(
        by_distance=by_distance,
        by_surface=by_surface,
        best_distance=_best(by_distance),
        best_surface=_best(by_surface),
        total_races=total,
    )


def list_recent_history_for_user(
    user_id: int,
    *,
    page: int = 1,
    page_size: int = 10,
    kind: str | None = None,
) -> HistoryPage:
    """Paginated combined draft + official race history.

    `kind` is None (both), "draft", or "official"; anything else is
    treated as None. Pages are 1-indexed. `total` reflects the active
    kind filter — switching the filter changes the total.
    """
    if kind not in (None, "draft", "official"):
        kind = None
    page = max(1, page)
    page_size = max(1, page_size)

    all_entries = _all_history_for_user(user_id, kind=kind)
    total = len(all_entries)
    start = (page - 1) * page_size
    end = start + page_size
    return HistoryPage(
        entries=all_entries[start:end],
        total=total,
        page=page,
        page_size=page_size,
    )
