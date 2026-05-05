"""GameTora G1 race-preset importer.

Reads a checked-in JSON snapshot at ``data/seeds/g1_races.json`` and
upserts each entry into ``race_presets``. Idempotent by the same
natural key as the custom-races seeder
(venue, surface, distance_meters, direction, course_variant).

PROJECT_INTENTIONS.md §10 + §21 guidance:
- Never scrape on every page load.
- Store source URL + ``imported_at`` per row.
- Make the importer re-runnable.
- Keep attribution explicit.

A separate subcommand can fetch a fresh JSON snapshot from GameTora;
that's wired up only for explicit, one-off use. The day-to-day path
is to commit a JSON refresh and run ``flask uma import-g1-races``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from ..extensions import db
from ..models import RacePreset
from ..models.enums import (
    VENUES,
    Direction,
    DistanceCategory,
    PresetSource,
    Surface,
)

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "seeds" / "g1_races.json"
)


@dataclass(frozen=True)
class SeedReport:
    inserted: int
    updated: int
    skipped: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


class G1ImportError(ValueError):
    pass


def _validate(row: dict, idx: int) -> None:
    required = (
        "name",
        "grade",
        "venue",
        "surface",
        "distance_meters",
        "distance_category",
        "direction",
        "max_runners",
    )
    for field in required:
        if field not in row:
            raise G1ImportError(f"row {idx}: missing required field {field!r}")

    if row["venue"] not in VENUES:
        raise G1ImportError(f"row {idx}: unknown venue {row['venue']!r}")
    if row["surface"] not in (s.value for s in Surface):
        raise G1ImportError(f"row {idx}: unknown surface {row['surface']!r}")
    if row["distance_category"] not in (d.value for d in DistanceCategory):
        raise G1ImportError(
            f"row {idx}: unknown distance_category {row['distance_category']!r}"
        )
    if row["direction"] not in (d.value for d in Direction):
        raise G1ImportError(f"row {idx}: unknown direction {row['direction']!r}")
    if not isinstance(row["distance_meters"], int) or row["distance_meters"] <= 0:
        raise G1ImportError(
            f"row {idx}: distance_meters must be a positive int, got {row['distance_meters']!r}"
        )
    if not isinstance(row["max_runners"], int) or row["max_runners"] <= 0:
        raise G1ImportError(
            f"row {idx}: max_runners must be a positive int, got {row['max_runners']!r}"
        )


def import_g1_races(path: Path | None = None) -> SeedReport:
    src = path or DEFAULT_SEED_PATH
    payload = json.loads(src.read_text(encoding="utf-8"))
    races = payload.get("races")
    if not isinstance(races, list):
        raise G1ImportError("expected top-level 'races' list")

    now = datetime.now(UTC)
    inserted = updated = skipped = 0

    for idx, row in enumerate(races, start=1):
        _validate(row, idx)
        course_variant = row.get("course_variant")

        # Match on (name, venue) — race name is unique within a venue
        # for JRA G1s, and this is what lets Tokyo Yushun + Japanese
        # Oaks coexist (same track config, different races). PR-G2.
        existing = db.session.scalars(
            select(RacePreset).where(
                RacePreset.name == row["name"],
                RacePreset.venue == row["venue"],
            )
        ).first()

        if existing is None:
            db.session.add(
                RacePreset(
                    source=PresetSource.G1_IMPORT,
                    external_source_url=row.get("external_source_url"),
                    name=row["name"],
                    grade=row["grade"],
                    venue=row["venue"],
                    surface=row["surface"],
                    distance_meters=row["distance_meters"],
                    distance_category=row["distance_category"],
                    direction=row["direction"],
                    course_variant=course_variant,
                    max_runners=row["max_runners"],
                    enabled=True,
                    imported_at=now,
                )
            )
            inserted += 1
            continue

        changed = (
            existing.grade != row["grade"]
            or existing.surface != row["surface"]
            or existing.distance_meters != row["distance_meters"]
            or existing.distance_category != row["distance_category"]
            or existing.direction != row["direction"]
            or existing.course_variant != course_variant
            or existing.max_runners != row["max_runners"]
            or existing.external_source_url != row.get("external_source_url")
        )
        if changed:
            existing.grade = row["grade"]
            existing.surface = row["surface"]
            existing.distance_meters = row["distance_meters"]
            existing.distance_category = row["distance_category"]
            existing.direction = row["direction"]
            existing.course_variant = course_variant
            existing.max_runners = row["max_runners"]
            existing.external_source_url = row.get("external_source_url")
            existing.source = PresetSource.G1_IMPORT
            existing.imported_at = now
            updated += 1
        else:
            skipped += 1

    db.session.commit()
    return SeedReport(inserted=inserted, updated=updated, skipped=skipped)
