"""Seed UmaSkill rows from a checked-in JSON snapshot.

Source of truth: ``data/seeds/uma_skills.json``, written by
``flask uma fetch-gametora-skills``. Idempotent upsert by gametora_id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from ..extensions import db
from ..models import UmaSkill

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "seeds" / "uma_skills.json"
)


@dataclass(frozen=True)
class SeedReport:
    inserted: int
    updated: int
    skipped: int
    pruned: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


def _load_seed(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("skills")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected top-level 'skills' list")
    return rows


def seed_skills(
    path: Path | None = None,
    *,
    prune_missing: bool = False,
) -> SeedReport:
    """Idempotent upsert of UmaSkill rows by gametora_id."""
    seed_path = path or DEFAULT_SEED_PATH
    rows = _load_seed(seed_path)
    now = datetime.now(UTC)

    existing = {
        s.gametora_id: s
        for s in db.session.scalars(select(UmaSkill)).all()
    }

    inserted = 0
    updated = 0
    skipped = 0
    seen_ids: set[int] = set()

    for row in rows:
        gid = row.get("gametora_id")
        name_en = row.get("name_en")
        if not isinstance(gid, int) or not name_en:
            skipped += 1
            continue
        seen_ids.add(gid)

        fields = {
            "name_en": name_en,
            "name_jp": row.get("name_jp"),
            "description_en": row.get("description_en"),
            "description_jp": row.get("description_jp"),
            "icon_id": row.get("icon_id"),
            "rarity": row.get("rarity"),
            "is_unique": bool(row.get("is_unique", False)),
            "is_inherited": bool(row.get("is_inherited", False)),
            "parent_gametora_id": row.get("parent_gametora_id"),
        }

        skill = existing.get(gid)
        if skill is None:
            skill = UmaSkill(
                gametora_id=gid,
                imported_at=now,
                enabled=True,
                **fields,
            )
            db.session.add(skill)
            inserted += 1
        else:
            changed = False
            for key, val in fields.items():
                if getattr(skill, key) != val:
                    setattr(skill, key, val)
                    changed = True
            # Re-enable a skill that had been pruned previously.
            if not skill.enabled:
                skill.enabled = True
                changed = True
            skill.imported_at = now
            if changed:
                updated += 1
            else:
                skipped += 1

    pruned = 0
    if prune_missing:
        for gid, skill in existing.items():
            if gid not in seen_ids and skill.enabled:
                skill.enabled = False
                pruned += 1

    db.session.commit()
    return SeedReport(
        inserted=inserted, updated=updated, skipped=skipped, pruned=pruned
    )
