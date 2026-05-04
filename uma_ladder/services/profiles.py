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


def resolve_oshi_image(profile: UserProfile) -> str | None:
    """Pick the best Oshi image: chosen outfit > character default > none."""
    if profile.oshi_outfit and profile.oshi_outfit.image_url:
        return profile.oshi_outfit.image_url
    if profile.oshi and profile.oshi.image_url:
        return profile.oshi.image_url
    return None
