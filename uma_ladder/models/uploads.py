from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .enums import OcrParseStatus, UploadPurpose


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UploadedImage(db.Model):
    __tablename__ = "uploaded_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploader_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str] = mapped_column(
        String(32), nullable=False, default=UploadPurpose.OCR_RESULT
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    uploader = relationship("User", lazy="joined", foreign_keys=[uploader_user_id])


class OcrParseAttempt(db.Model):
    __tablename__ = "ocr_parse_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_image_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OcrParseStatus.PENDING, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    image = relationship("UploadedImage", lazy="joined")
    confirmed_by = relationship("User", lazy="joined", foreign_keys=[confirmed_by_user_id])
