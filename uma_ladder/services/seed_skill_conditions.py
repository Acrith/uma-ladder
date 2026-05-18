"""Seed `skill_conditions` rows from a checked-in JSON snapshot.

Source of truth: ``data/seeds/skill_conditions.json``, written by
``flask uma fetch-gametora-skill-conditions``. Idempotent upsert by
`UmaSkill.gametora_id` → `SkillCondition.skill_id`. Rows whose
gametora_id has no matching UmaSkill are skipped (the catalog row
needs an existing skill to attach to).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from ..extensions import db
from ..models import SkillCondition, UmaSkill

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "seeds"
    / "skill_conditions.json"
)


@dataclass(frozen=True)
class SeedReport:
    inserted: int
    updated: int
    skipped_no_skill: int
    skipped_invalid: int

    @property
    def total(self) -> int:
        return (
            self.inserted
            + self.updated
            + self.skipped_no_skill
            + self.skipped_invalid
        )


def _load_seed(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("conditions")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected top-level 'conditions' list")
    return rows


# Column names on SkillCondition the seeder writes. All other columns
# (id, created_at, updated_at) are managed by SQLAlchemy / the DB.
_WRITABLE_COLUMNS: tuple[str, ...] = (
    "direction", "surface", "weather", "season",
    "distance_category", "strategy", "venue", "is_standard_distance",
    "buff_speed", "buff_stamina", "buff_power", "buff_guts", "buff_wisdom",
    "is_dynamic", "notes",
)


def seed_skill_conditions(path: Path | None = None) -> SeedReport:
    """Idempotent upsert of `skill_conditions` rows by skill_id
    (resolved via UmaSkill.gametora_id lookup)."""
    seed_path = path or DEFAULT_SEED_PATH
    rows = _load_seed(seed_path)

    # Pre-load skill_id by gametora_id so we don't N+1 the lookup.
    skill_id_by_gid: dict[int, int] = {
        gid: sid
        for (gid, sid) in db.session.execute(
            select(UmaSkill.gametora_id, UmaSkill.id)
        ).all()
    }

    existing: dict[int, SkillCondition] = {
        c.skill_id: c
        for c in db.session.scalars(select(SkillCondition)).all()
    }

    inserted = 0
    updated = 0
    skipped_no_skill = 0
    skipped_invalid = 0

    for row in rows:
        gid = row.get("gametora_id")
        if not isinstance(gid, int):
            skipped_invalid += 1
            continue
        skill_id = skill_id_by_gid.get(gid)
        if skill_id is None:
            skipped_no_skill += 1
            continue

        # Build the column dict, defaulting buff fields to 0 and
        # is_dynamic to False so the model defaults agree with the
        # seed snapshot's shape (which omits keys at their default).
        fields: dict = {
            "buff_speed": 0, "buff_stamina": 0, "buff_power": 0,
            "buff_guts": 0, "buff_wisdom": 0,
            "is_dynamic": False,
        }
        for col in _WRITABLE_COLUMNS:
            if col in row:
                fields[col] = row[col]

        condition = existing.get(skill_id)
        if condition is None:
            condition = SkillCondition(skill_id=skill_id, **fields)
            db.session.add(condition)
            inserted += 1
        else:
            changed = False
            for col, val in fields.items():
                if getattr(condition, col) != val:
                    setattr(condition, col, val)
                    changed = True
            if changed:
                updated += 1

    db.session.commit()
    return SeedReport(
        inserted=inserted,
        updated=updated,
        skipped_no_skill=skipped_no_skill,
        skipped_invalid=skipped_invalid,
    )
