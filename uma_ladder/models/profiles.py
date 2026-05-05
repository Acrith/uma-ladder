from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UserProfile(db.Model):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    friend_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discord_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Discord snowflake (64-bit int rendered as decimal string). Drives
    # `<@id>` mentions in webhook embeds so the user gets a desktop /
    # mobile push when an actionable event fires for them.
    discord_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oshi_character_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_characters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    oshi_outfit_id: Mapped[int | None] = mapped_column(
        ForeignKey("uma_outfits.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", backref="profile", uselist=False
    )
    oshi: Mapped[UmaCharacter | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "UmaCharacter", lazy="joined"
    )
    oshi_outfit: Mapped[UmaOutfit | None] = relationship(  # noqa: F821
        "UmaOutfit", lazy="joined", foreign_keys=[oshi_outfit_id]
    )

    def __repr__(self) -> str:
        return f"<UserProfile user_id={self.user_id}>"
