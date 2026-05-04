from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from ..extensions import db
from ..models import UmaCharacter

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "seeds" / "uma_characters.json"
)


@dataclass(frozen=True)
class SeedReport:
    inserted: int
    updated: int
    skipped: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


def _load_seed(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    chars = payload.get("characters")
    if not isinstance(chars, list):
        raise ValueError(f"{path}: expected top-level 'characters' list")
    return chars


def seed_characters(path: Path | None = None, *, source: str = "seed_curated") -> SeedReport:
    """Idempotent upsert of curated Uma characters by slug.

    Returns counts of inserted, updated, and skipped (already up-to-date) rows.
    Safe to re-run.
    """
    src = path or DEFAULT_SEED_PATH
    rows = _load_seed(src)
    now = datetime.now(UTC)

    inserted = updated = skipped = 0
    for raw in rows:
        slug = raw["slug"]
        name_en = raw["name_en"]
        name_jp = raw.get("name_jp")
        image_url = raw.get("image_url")
        profile_url = raw.get("profile_url")

        existing = db.session.scalars(
            select(UmaCharacter).where(UmaCharacter.slug == slug)
        ).first()

        if existing is None:
            db.session.add(
                UmaCharacter(
                    slug=slug,
                    name_en=name_en,
                    name_jp=name_jp,
                    image_url=image_url,
                    profile_url=profile_url,
                    source=source,
                    imported_at=now,
                )
            )
            inserted += 1
            continue

        changed = (
            existing.name_en != name_en
            or existing.name_jp != name_jp
            or existing.image_url != image_url
            or existing.profile_url != profile_url
        )
        if changed:
            existing.name_en = name_en
            existing.name_jp = name_jp
            existing.image_url = image_url
            existing.profile_url = profile_url
            existing.imported_at = now
            updated += 1
        else:
            skipped += 1

    db.session.commit()
    return SeedReport(inserted=inserted, updated=updated, skipped=skipped)
