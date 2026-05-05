from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from ..extensions import db
from ..models import UmaCharacter, UmaOutfit, User, UserProfile


class ProfileError(Exception):
    pass


class UnknownOshiError(ProfileError):
    pass


class UnknownOutfitError(ProfileError):
    pass


@dataclass
class ProfileUpdate:
    display_name: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    friend_code: str | None = None
    discord_handle: str | None = None
    oshi_character_id: int | None = None
    oshi_outfit_id: int | None = None


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

    profile.display_name = update.display_name
    profile.avatar_url = update.avatar_url
    profile.description = update.description
    profile.friend_code = update.friend_code
    profile.discord_handle = update.discord_handle
    profile.oshi_character_id = update.oshi_character_id
    profile.oshi_outfit_id = final_outfit_id
    db.session.commit()
    return profile


def find_user_by_username(username: str) -> User | None:
    return db.session.scalars(
        select(User).where(User.username == username.strip().lower())
    ).first()


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


def list_recent_history_for_user(
    user_id: int, *, limit: int = 10
) -> list[HistoryEntry]:
    """Combined draft + official race history for a user, newest first.

    Pulls completed entries from both sources and merges them in Python
    rather than UNION'ing in SQL — the combined volume per user is small
    and this keeps the join shape simple.
    """
    from ..models import (
        DraftMatch,
        DraftRaceResult,
        OfficialRace,
        OfficialRaceResult,
    )

    entries: list[HistoryEntry] = []

    draft_rows = db.session.scalars(
        select(DraftRaceResult)
        .where(DraftRaceResult.user_id == user_id)
        .order_by(DraftRaceResult.created_at.desc())
        .limit(limit)
    ).all()
    for r in draft_rows:
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

    official_rows = db.session.scalars(
        select(OfficialRaceResult)
        .where(OfficialRaceResult.user_id == user_id)
        .order_by(OfficialRaceResult.created_at.desc())
        .limit(limit)
    ).unique().all()
    for r in official_rows:
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
    return entries[:limit]
