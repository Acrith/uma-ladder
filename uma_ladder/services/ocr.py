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
from collections.abc import Callable, Mapping
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
    """Structured OCR output. All fields optional; routes render what's there.

    The provider is screen-agnostic: it returns whatever it can extract.
    Result-summary screenshots populate ``rows`` (placement + uma_name).
    Stat-screen screenshots populate ``stats`` (speed/stamina/power/guts/
    wisdom) and ``skills`` (a flat list of skill names). The same provider
    can populate both if the input image has both visible.
    """

    raw_text: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)
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
            stats={
                "speed": 1100,
                "stamina": 900,
                "power": 1000,
                "guts": 600,
                "wisdom": 800,
            },
            skills=["Warning Shot!", "Accelerator X", "Made-up Skill"],
            confidence={"overall": 0.85},
        )


ProviderFactory = Callable[[], OcrProvider]

_PROVIDERS: dict[str, ProviderFactory] = {
    "manual": ManualOcrProvider,
    "mock": MockOcrProvider,
}


def get_provider() -> OcrProvider:
    """Resolve the configured provider. Default: manual.

    `google_vision` is imported lazily so its module is only loaded in apps
    that actually use it.
    """
    name = current_app.config.get("OCR_PROVIDER", "manual")
    if name == "google_vision":
        from .ocr_google_vision import build_google_vision_provider

        return build_google_vision_provider()
    factory = _PROVIDERS.get(name)
    if factory is None:
        raise RuntimeError(f"unknown OCR_PROVIDER: {name!r}")
    return factory()


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Test/extension hook for plugging in additional providers."""
    _PROVIDERS[name] = factory


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
    attempt.parsed_json = {
        "rows": parse.rows,
        "stats": parse.stats,
        "skills": parse.skills,
    }
    attempt.confidence_json = parse.confidence
    attempt.status = OcrParseStatus.PARSED
    db.session.commit()
    return attempt


def confirm_parse(
    attempt_id: int,
    *,
    confirmed_by_user_id: int,
    edited_rows: list[Mapping[str, Any]] | None = None,
    draft_match_id: int | None = None,
) -> OcrParseAttempt:
    """Mark an attempt confirmed and store any human edits.

    This call only marks the parse confirmed in the audit log. Writing
    actual race results remains the caller's responsibility — the
    confirmed parse is metadata, not a result.

    `draft_match_id` (PR-J4) ties the confirmed attempt to the draft
    match whose results it seeded so the completed-match card can
    surface the source screenshots later.
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
    if draft_match_id is not None:
        attempt.draft_match_id = draft_match_id
    db.session.commit()
    return attempt


def get_draft_match_screenshots(draft_match_id: int) -> list[UploadedImage]:
    """Return source UploadedImages for every confirmed OCR attempt
    tied to this draft match. Includes the primary uploaded_image_id
    plus any extras stashed in parsed_json["screenshot_image_ids"]
    during a multi-screenshot upload (services/draft routes), in
    upload order, deduped by image id.
    """
    attempts = (
        db.session.query(OcrParseAttempt)
        .filter(
            OcrParseAttempt.draft_match_id == draft_match_id,
            OcrParseAttempt.status == OcrParseStatus.CONFIRMED,
        )
        .order_by(OcrParseAttempt.created_at.asc())
        .all()
    )
    seen: set[int] = set()
    image_ids: list[int] = []
    for att in attempts:
        extras = (att.parsed_json or {}).get("screenshot_image_ids") or []
        # Primary first, then any extras the multi-upload merged in.
        for img_id in [att.uploaded_image_id, *extras]:
            if img_id is None or img_id in seen:
                continue
            seen.add(img_id)
            image_ids.append(img_id)
    if not image_ids:
        return []
    images = (
        db.session.query(UploadedImage)
        .filter(UploadedImage.id.in_(image_ids))
        .all()
    )
    by_id = {img.id: img for img in images}
    return [by_id[i] for i in image_ids if i in by_id]


def user_can_view_image(image_id: int, user_id: int) -> bool:
    """PR-J4 access widening — the legacy rule was uploader-only
    (plus admin). For OCR_RESULT screenshots tied to a draft match,
    both match participants need to see the screenshots on the
    completed-match card. This helper answers that single question
    so `ocr.serve_image` can stay short.
    """
    from ..models import DraftMatch

    rows = (
        db.session.query(OcrParseAttempt)
        .filter(
            OcrParseAttempt.uploaded_image_id == image_id,
            OcrParseAttempt.draft_match_id.isnot(None),
        )
        .all()
    )
    match_ids = {a.draft_match_id for a in rows if a.draft_match_id}
    # Multi-screenshot uploads keep the extra images only on the
    # primary attempt's parsed_json. Walk the confirmed primaries to
    # catch those.
    extra_primaries = (
        db.session.query(OcrParseAttempt)
        .filter(
            OcrParseAttempt.draft_match_id.isnot(None),
            OcrParseAttempt.status == OcrParseStatus.CONFIRMED,
        )
        .all()
    )
    for att in extra_primaries:
        extras = (att.parsed_json or {}).get("screenshot_image_ids") or []
        if image_id in extras and att.draft_match_id is not None:
            match_ids.add(att.draft_match_id)
    if not match_ids:
        return False
    matches = (
        db.session.query(DraftMatch)
        .filter(DraftMatch.id.in_(match_ids))
        .all()
    )
    return any(
        user_id in (m.host_user_id, m.opponent_user_id) for m in matches
    )
