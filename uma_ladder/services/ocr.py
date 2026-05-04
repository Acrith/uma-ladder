"""OCR provider interface, persistence, and a confirmation gate.

Design intent (PROJECT_INTENTIONS.md §13):
- OCR assists humans, not silently decides results.
- Persist every parse attempt so a human can confirm/correct it.
- No OfficialRaceResult or DraftRaceResult is written from raw OCR output —
  callers go through the existing services/official.submit_results and
  services/draft.submit_results, which already require confirmed_by_user_id.

This PR ships the foundation:
- OcrProvider ABC.
- ManualOcrProvider — returns an empty parse so the human types it in.
- MockOcrProvider — returns deterministic fixture data, used in tests and
  available behind OCR_PROVIDER=mock for local UI dev.
- save_uploaded_image stores the file under instance/uploads/<uuid>.
- run_parse runs the configured provider and writes OcrParseAttempt.
- confirm_parse marks an attempt confirmed and stores any human edits.

Real OCR providers (tesseract, easyocr, vision API, LLM-vision) plug in
later behind the same ABC.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import (
    OcrParseAttempt,
    OcrParseStatus,
    UploadedImage,
)
from ..models.enums import UploadPurpose

# ---------- Provider interface ----------


@dataclass(frozen=True)
class OcrParse:
    """Structured OCR output. All fields optional; routes render what's there."""

    raw_text: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    confidence: dict[str, Any] = field(default_factory=dict)


class OcrProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def parse(self, image_path: Path) -> OcrParse:
        ...


class ManualOcrProvider(OcrProvider):
    """No-op provider: returns an empty parse for the human to fill in."""

    name = "manual"

    def parse(self, image_path: Path) -> OcrParse:  # noqa: ARG002
        return OcrParse(
            raw_text=None,
            rows=[],
            confidence={"note": "manual provider; type the result yourself"},
        )


class MockOcrProvider(OcrProvider):
    """Deterministic provider for tests and local UI dev.

    Returns fixed placement rows so the confirmation UI is exercise-able
    without a real OCR runtime.
    """

    name = "mock"

    def parse(self, image_path: Path) -> OcrParse:  # noqa: ARG002
        return OcrParse(
            raw_text="MOCK OCR OUTPUT",
            rows=[
                {"placement": 1, "uma_name": "MockUma A", "strategy": "Front"},
                {"placement": 2, "uma_name": "MockUma B", "strategy": "Pace"},
                {"placement": 3, "uma_name": "MockUma C", "strategy": "End"},
            ],
            confidence={"overall": 0.85},
        )


_PROVIDERS: dict[str, type[OcrProvider]] = {
    "manual": ManualOcrProvider,
    "mock": MockOcrProvider,
}


def get_provider() -> OcrProvider:
    """Resolve the configured provider. Default: manual."""
    name = current_app.config.get("OCR_PROVIDER", "manual")
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise RuntimeError(f"unknown OCR_PROVIDER: {name!r}")
    return cls()


def register_provider(name: str, cls: type[OcrProvider]) -> None:
    """Test/extension hook for plugging in additional providers."""
    _PROVIDERS[name] = cls


# ---------- Storage + persistence ----------


class OcrError(Exception):
    pass


_ALLOWED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB


def _uploads_dir() -> Path:
    base = Path(current_app.instance_path) / "uploads"
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_uploaded_image(
    file: FileStorage,
    *,
    uploader_user_id: int | None,
    purpose: str = UploadPurpose.OCR_RESULT,
) -> UploadedImage:
    if file is None or not getattr(file, "filename", ""):
        raise OcrError("no file provided")
    original = secure_filename(file.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise OcrError(f"unsupported file extension: {ext!r}")

    storage_key = f"{uuid.uuid4().hex}.{ext}"
    target = _uploads_dir() / storage_key
    file.save(target)
    size = target.stat().st_size
    if size > _MAX_BYTES:
        target.unlink(missing_ok=True)
        raise OcrError(f"file exceeds {_MAX_BYTES} bytes")

    image = UploadedImage(
        uploader_user_id=uploader_user_id,
        storage_key=storage_key,
        original_filename=original,
        mime_type=file.mimetype,
        size_bytes=size,
        purpose=purpose,
    )
    db.session.add(image)
    db.session.commit()
    return image


def image_path(image: UploadedImage) -> Path:
    return _uploads_dir() / image.storage_key


def run_parse(image: UploadedImage, *, provider: OcrProvider | None = None) -> OcrParseAttempt:
    """Run the configured (or supplied) provider against an uploaded image.

    Always writes an OcrParseAttempt row, even on provider failure.
    """
    chosen = provider or get_provider()
    attempt = OcrParseAttempt(
        uploaded_image_id=image.id,
        provider=chosen.name,
        status=OcrParseStatus.PENDING,
    )
    db.session.add(attempt)
    db.session.commit()

    try:
        parse = chosen.parse(image_path(image))
    except Exception as exc:  # noqa: BLE001
        attempt.status = OcrParseStatus.FAILED
        attempt.error_message = str(exc)
        db.session.commit()
        return attempt

    attempt.raw_text = parse.raw_text
    attempt.parsed_json = {"rows": parse.rows}
    attempt.confidence_json = parse.confidence
    attempt.status = OcrParseStatus.PARSED
    db.session.commit()
    return attempt


def confirm_parse(
    attempt_id: int,
    *,
    confirmed_by_user_id: int,
    edited_rows: list[Mapping[str, Any]] | None = None,
) -> OcrParseAttempt:
    """Mark an attempt confirmed and store any human edits.

    This call only marks the parse confirmed in the audit log. Writing
    actual race results remains the caller's responsibility — the
    confirmed parse is metadata, not a result.
    """
    attempt = db.session.get(OcrParseAttempt, attempt_id)
    if attempt is None:
        raise OcrError(f"attempt {attempt_id} not found")
    if attempt.status not in (OcrParseStatus.PARSED, OcrParseStatus.CONFIRMED):
        raise OcrError(f"cannot confirm parse in status {attempt.status}")
    if edited_rows is not None:
        attempt.parsed_json = {"rows": list(edited_rows)}
    attempt.confirmed_by_user_id = confirmed_by_user_id
    attempt.confirmed_at = datetime.now(UTC)
    attempt.status = OcrParseStatus.CONFIRMED
    db.session.commit()
    return attempt
