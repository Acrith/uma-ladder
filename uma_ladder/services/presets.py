from __future__ import annotations

from sqlalchemy import select

from ..extensions import db
from ..models import RacePreset


def list_presets(*, source: str | None = None) -> list[RacePreset]:
    stmt = select(RacePreset)
    if source is not None:
        stmt = stmt.where(RacePreset.source == source)
    stmt = stmt.order_by(
        RacePreset.surface,
        RacePreset.venue,
        RacePreset.distance_meters,
    )
    return list(db.session.scalars(stmt))


def set_enabled(preset_id: int, enabled: bool) -> RacePreset:
    preset = db.session.get(RacePreset, preset_id)
    if preset is None:
        raise LookupError(f"preset {preset_id} not found")
    preset.enabled = enabled
    db.session.commit()
    return preset
