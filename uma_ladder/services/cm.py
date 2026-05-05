"""Champions Meeting service — admin-managed upcoming-event entries.

Backed by RacePreset for track conditions; the model exposes
`effective_*` properties that resolve override columns. The service
layer adds CRUD + a small audit-log breadcrumb when admins mutate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from ..extensions import db
from ..models import ChampionsMeeting, RacePreset
from . import track_conditions as track_conditions_service


class CmError(Exception):
    pass


class UnknownPresetError(CmError):
    pass


@dataclass
class CmInput:
    """Form/service payload. Override fields are nullable — empty string
    coerces to None at the route layer so the resolution logic only ever
    sees None vs. real values."""

    name: str
    starts_on: date
    ends_on: date | None
    preset_id: int
    override_venue: str | None = None
    override_surface: str | None = None
    override_distance_meters: int | None = None
    override_distance_category: str | None = None
    override_direction: str | None = None
    race_season: str | None = None
    weather: str | None = None
    ground_condition: str | None = None
    notes: str | None = None
    source_url: str | None = None


def _validate_preset(preset_id: int) -> RacePreset:
    preset = db.session.get(RacePreset, preset_id)
    if preset is None or not preset.enabled:
        raise UnknownPresetError(str(preset_id))
    return preset


def create_cm(payload: CmInput, *, by_user_id: int | None) -> ChampionsMeeting:
    _validate_preset(payload.preset_id)
    season, weather, ground = track_conditions_service.normalize(
        race_season=payload.race_season,
        weather=payload.weather,
        ground_condition=payload.ground_condition,
    )
    cm = ChampionsMeeting(
        name=payload.name.strip(),
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        preset_id=payload.preset_id,
        override_venue=payload.override_venue,
        override_surface=payload.override_surface,
        override_distance_meters=payload.override_distance_meters,
        override_distance_category=payload.override_distance_category,
        override_direction=payload.override_direction,
        race_season=season,
        weather=weather,
        ground_condition=ground,
        notes=payload.notes,
        source_url=payload.source_url,
        created_by_user_id=by_user_id,
    )
    db.session.add(cm)
    db.session.commit()
    _audit("cm_create", by_user_id, cm)
    return cm


def update_cm(
    cm_id: int, payload: CmInput, *, by_user_id: int | None
) -> ChampionsMeeting:
    cm = db.session.get(ChampionsMeeting, cm_id)
    if cm is None:
        raise CmError(f"unknown cm id {cm_id}")
    _validate_preset(payload.preset_id)
    season, weather, ground = track_conditions_service.normalize(
        race_season=payload.race_season,
        weather=payload.weather,
        ground_condition=payload.ground_condition,
    )
    cm.name = payload.name.strip()
    cm.starts_on = payload.starts_on
    cm.ends_on = payload.ends_on
    cm.preset_id = payload.preset_id
    cm.override_venue = payload.override_venue
    cm.override_surface = payload.override_surface
    cm.override_distance_meters = payload.override_distance_meters
    cm.override_distance_category = payload.override_distance_category
    cm.override_direction = payload.override_direction
    cm.race_season = season
    cm.weather = weather
    cm.ground_condition = ground
    cm.notes = payload.notes
    cm.source_url = payload.source_url
    db.session.commit()
    _audit("cm_update", by_user_id, cm)
    return cm


def delete_cm(cm_id: int, *, by_user_id: int | None) -> None:
    cm = db.session.get(ChampionsMeeting, cm_id)
    if cm is None:
        return
    snapshot = {"id": cm.id, "name": cm.name, "starts_on": cm.starts_on.isoformat()}
    db.session.delete(cm)
    db.session.commit()
    _audit("cm_delete", by_user_id, snapshot=snapshot)


def get_cm(cm_id: int) -> ChampionsMeeting | None:
    return db.session.get(ChampionsMeeting, cm_id)


def list_upcoming(*, limit: int = 3, today: date | None = None) -> Sequence[ChampionsMeeting]:
    """CMs with starts_on >= today (or ends_on >= today when present —
    a CM that's mid-window is still "upcoming/active" for dashboard
    purposes). Ordered chronologically."""
    today = today or _today()
    stmt = (
        select(ChampionsMeeting)
        .where(
            (ChampionsMeeting.starts_on >= today)
            | (
                (ChampionsMeeting.ends_on.is_not(None))
                & (ChampionsMeeting.ends_on >= today)
            )
        )
        .order_by(ChampionsMeeting.starts_on.asc())
        .limit(limit)
    )
    return list(db.session.scalars(stmt))


def list_all(*, include_past: bool = True) -> Sequence[ChampionsMeeting]:
    stmt = select(ChampionsMeeting).order_by(ChampionsMeeting.starts_on.desc())
    if not include_past:
        stmt = stmt.where(ChampionsMeeting.starts_on >= _today())
    return list(db.session.scalars(stmt))


def is_active_now(cm: ChampionsMeeting, *, today: date | None = None) -> bool:
    """True when today is between starts_on and ends_on (inclusive). If
    `ends_on` is None, falls back to a 5-day window from `starts_on` —
    that's the canonical CM window length when admins haven't entered
    an explicit end date."""
    today = today or _today()
    end = cm.ends_on or (cm.starts_on + timedelta(days=4))
    return cm.starts_on <= today <= end


# ---- internals ----


def _today() -> date:
    return datetime.now(UTC).date()


def _audit(
    action: str,
    by_user_id: int | None,
    cm: ChampionsMeeting | None = None,
    *,
    snapshot: dict | None = None,
) -> None:
    """Best-effort audit breadcrumb. Failures here never propagate —
    admin_audit.log_action already swallows its own exceptions."""
    try:
        from . import admin_audit

        details = None
        if cm is not None:
            details = f"name={cm.name!r} starts_on={cm.starts_on.isoformat()}"
        elif snapshot is not None:
            details = ", ".join(f"{k}={v!r}" for k, v in snapshot.items())
        admin_audit.log_action(
            actor_user_id=by_user_id,
            action=action,
            target_kind="champions_meeting",
            target_id=cm.id if cm is not None else None,
            details=details,
        )
    except Exception:  # noqa: BLE001
        pass
