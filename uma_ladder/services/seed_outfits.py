"""Seed UmaOutfit rows from a checked-in JSON snapshot.

Source of truth: ``data/seeds/uma_outfits.json``, written by
``flask uma fetch-gametora-outfits``. Each row carries ``char_id`` (from
GameTora) which we map to the local UmaCharacter via that character's
``profile_url`` (which contains the same ``char_id`` slug). Rows whose
character isn't seeded locally are silently skipped — re-seed
characters first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from ..extensions import db
from ..models import UmaCharacter, UmaOutfit

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "seeds" / "uma_outfits.json"
)

_PROFILE_URL_CHAR_ID_RE = re.compile(r"/characters/(\d+)-")


def _profile_url_to_char_id(url: str) -> int | None:
    """Recover the 4-digit GameTora char_id from a /characters/<6digit>-<slug>
    URL. The 6-digit id is `char_id*100 + outfit_suffix`."""
    m = _PROFILE_URL_CHAR_ID_RE.search(url)
    if not m:
        return None
    raw = int(m.group(1))
    # Old URLs stored just the 4-digit char_id; new URLs use 6-digit
    # `<char_id>01`. Both fit if we divide by 100 only when ≥ 6 digits.
    if raw >= 100000:
        return raw // 100
    return raw


@dataclass(frozen=True)
class SeedReport:
    inserted: int
    updated: int
    skipped: int
    pruned: int = 0
    orphaned: int = 0  # outfits whose char_id isn't seeded locally

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


def _load_seed(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("outfits")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected top-level 'outfits' list")
    return rows


def _build_char_id_to_local_id() -> dict[int, int]:
    """Map GameTora char_id → local UmaCharacter.id by parsing profile_url."""
    out: dict[int, int] = {}
    for c in db.session.scalars(select(UmaCharacter)).all():
        if not c.profile_url:
            continue
        char_id = _profile_url_to_char_id(c.profile_url)
        if char_id is not None:
            out[char_id] = c.id
    return out


def seed_outfits(
    path: Path | None = None,
    *,
    prune_missing: bool = False,
) -> SeedReport:
    """Idempotent upsert of outfits by (uma_character_id, costume_id)."""
    src = path or DEFAULT_SEED_PATH
    rows = _load_seed(src)
    now = datetime.now(UTC)
    char_lookup = _build_char_id_to_local_id()

    inserted = updated = skipped = pruned = orphaned = 0
    seen_keys: set[tuple[int, int]] = set()

    for raw in rows:
        char_id = raw.get("char_id")
        costume_id = raw.get("costume_id")
        if not isinstance(char_id, int) or not isinstance(costume_id, int):
            orphaned += 1
            continue
        local_id = char_lookup.get(char_id)
        if local_id is None:
            orphaned += 1
            continue

        seen_keys.add((local_id, costume_id))
        title_en = raw.get("title_en")
        title_jp = raw.get("title_jp")
        image_url = raw.get("image_url")
        rarity = raw.get("rarity") if isinstance(raw.get("rarity"), int) else None
        released = bool(raw.get("released_globally", True))

        existing = db.session.scalars(
            select(UmaOutfit).where(
                UmaOutfit.uma_character_id == local_id,
                UmaOutfit.costume_id == costume_id,
            )
        ).first()

        if existing is None:
            db.session.add(
                UmaOutfit(
                    uma_character_id=local_id,
                    costume_id=costume_id,
                    title_en=title_en,
                    title_jp=title_jp,
                    image_url=image_url,
                    rarity=rarity,
                    released_globally=released,
                    enabled=True,
                    imported_at=now,
                )
            )
            inserted += 1
            continue

        changed = (
            existing.title_en != title_en
            or existing.title_jp != title_jp
            or existing.image_url != image_url
            or existing.rarity != rarity
            or existing.released_globally != released
            or not existing.enabled
        )
        if changed:
            existing.title_en = title_en
            existing.title_jp = title_jp
            existing.image_url = image_url
            existing.rarity = rarity
            existing.released_globally = released
            existing.enabled = True
            existing.imported_at = now
            updated += 1
        else:
            skipped += 1

    if prune_missing and seen_keys:
        all_outfits = db.session.scalars(
            select(UmaOutfit).where(UmaOutfit.enabled.is_(True))
        ).all()
        for o in all_outfits:
            if (o.uma_character_id, o.costume_id) not in seen_keys:
                o.enabled = False
                pruned += 1

    db.session.commit()
    return SeedReport(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        pruned=pruned,
        orphaned=orphaned,
    )
