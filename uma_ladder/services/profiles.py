from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from ..extensions import db
from ..models import UmaCharacter, User, UserProfile


class ProfileError(Exception):
    pass


class UnknownOshiError(ProfileError):
    pass


@dataclass
class ProfileUpdate:
    display_name: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    friend_code: str | None = None
    discord_handle: str | None = None
    oshi_character_id: int | None = None


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

    profile.display_name = update.display_name
    profile.avatar_url = update.avatar_url
    profile.description = update.description
    profile.friend_code = update.friend_code
    profile.discord_handle = update.discord_handle
    profile.oshi_character_id = update.oshi_character_id
    db.session.commit()
    return profile


def find_user_by_username(username: str) -> User | None:
    return db.session.scalars(
        select(User).where(User.username == username.strip().lower())
    ).first()


def list_enabled_characters() -> list[UmaCharacter]:
    return list(
        db.session.scalars(
            select(UmaCharacter).where(UmaCharacter.enabled.is_(True)).order_by(UmaCharacter.name_en)
        )
    )
