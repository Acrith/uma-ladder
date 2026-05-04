from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UmaSkill(db.Model):
    """Skills imported from GameTora.

    The local autoincrement `id` is what app code references (and what
    future result tables FK to). `gametora_id` is the upstream identifier
    used by the seeder for idempotent upserts. Inherited / "gene_version"
    variants are stored as separate rows with `is_inherited=True` and
    `parent_gametora_id` pointing at the main skill's upstream id.
    """

    __tablename__ = "uma_skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    gametora_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True
    )
    # Indexed because OCR text-match looks skills up by name. No unique
    # constraint — different skills (originals vs inherited variants)
    # can share an English name in some edge cases.
    name_en: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name_jp: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_jp: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rarity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_unique: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_inherited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Plain int (not FK) so the seeder doesn't need a two-pass insert: the
    # parent row may not exist yet when the inherited variant is processed.
    parent_gametora_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    def __repr__(self) -> str:
        return f"<UmaSkill {self.id} {self.name_en!r}>"
