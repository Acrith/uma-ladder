from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UmaCharacter(db.Model):
    __tablename__ = "uma_characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name_en: Mapped[str] = mapped_column(String(128), nullable=False)
    name_jp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="seed_curated")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<UmaCharacter {self.slug}>"
