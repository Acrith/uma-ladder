from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from ..extensions import db
from ..models import RacePreset
from ..models.enums import PresetSource
from .preset_parser import ParsedPreset, parse_lines

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "seeds" / "custom_races.txt"
)


@dataclass(frozen=True)
class SeedReport:
    inserted: int
    updated: int
    skipped: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


def _natural_key(p: ParsedPreset) -> tuple[str, str, int, str, str | None]:
    return (p.venue, p.surface, p.distance_meters, p.direction, p.course_variant)


def seed_custom_presets(path: Path | None = None) -> SeedReport:
    """Idempotent upsert of custom presets parsed from the appendix file."""
    src = path or DEFAULT_SEED_PATH
    parsed = parse_lines(src.read_text(encoding="utf-8"))
    now = datetime.now(UTC)

    inserted = updated = skipped = 0
    for p in parsed:
        existing = db.session.scalars(
            select(RacePreset).where(
                RacePreset.venue == p.venue,
                RacePreset.surface == p.surface,
                RacePreset.distance_meters == p.distance_meters,
                RacePreset.direction == p.direction,
                RacePreset.course_variant.is_(p.course_variant)
                if p.course_variant is None
                else RacePreset.course_variant == p.course_variant,
            )
        ).first()

        if existing is None:
            db.session.add(
                RacePreset(
                    source=PresetSource.CUSTOM_BUILTIN,
                    name=p.name,
                    venue=p.venue,
                    surface=p.surface,
                    distance_meters=p.distance_meters,
                    distance_category=p.distance_category,
                    direction=p.direction,
                    course_variant=p.course_variant,
                    max_runners=p.max_runners,
                    enabled=True,
                    imported_at=now,
                )
            )
            inserted += 1
            continue

        changed = (
            existing.name != p.name
            or existing.distance_category != p.distance_category
            or existing.max_runners != p.max_runners
        )
        if changed:
            existing.name = p.name
            existing.distance_category = p.distance_category
            existing.max_runners = p.max_runners
            existing.imported_at = now
            updated += 1
        else:
            skipped += 1

    db.session.commit()
    return SeedReport(inserted=inserted, updated=updated, skipped=skipped)
