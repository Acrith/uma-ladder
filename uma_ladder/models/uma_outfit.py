from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UmaOutfit(db.Model):
    __tablename__ = "uma_outfits"
    __table_args__ = (
        UniqueConstraint(
            "uma_character_id", "costume_id", name="uq_uma_outfits_character_costume"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uma_character_id: Mapped[int] = mapped_column(
        ForeignKey("uma_characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # GameTora's 6-digit costume identifier (char_id * 100 + outfit suffix).
    costume_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title_en: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title_jp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rarity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    released_globally: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="gametora")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    character = relationship("UmaCharacter", lazy="joined")

    @property
    def display_title(self) -> str:
        return self.title_en or self.title_jp or "(default)"

    def __repr__(self) -> str:
        return f"<UmaOutfit char={self.uma_character_id} costume={self.costume_id}>"
