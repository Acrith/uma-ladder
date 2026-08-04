"""Ingest and normalize race captures from the desktop extractor.

A capture is evidence, not a result: it lands here untouched, and only
reaches the ladder once a human confirms it (PROJECT_INTENTIONS §13).
This module owns the payload contract and the game→ladder vocabulary
mapping; it deliberately knows nothing about *how* the data was
captured, so a new capture method only adds a `RaceCaptureSource`.

Payload contract (schema 1) — see docs/room-match-extraction.md:

    {
      "schema": 1,
      "source": "memory_scan",
      "captured_at": "2026-08-04T13:26:00Z",
      "room": {"saved_room_id": .., "race_instance_id": .., "room_name": ..,
               "start_time": "YYYY-MM-DD HH:MM:SS", "entry_num": ..,
               "season": .., "weather": .., "ground_condition": ..},
      "runners": [{"gate": .., "trainer_name": .., "finish_position": ..,
                   "finish_time_seconds": .., "running_style": .., ...}]
    }

Anything beyond that is preserved verbatim in `payload_json` rather
than rejected — the extractor's shape will churn faster than this
schema, and a capture we cannot fully parse today is still worth
keeping for when we can.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..extensions import db
from ..models import (
    GroundCondition,
    RaceCapture,
    RaceCaptureSource,
    RaceCaptureStatus,
    RaceSeason,
    Weather,
)

# ─── Game integer → the vocabulary the rest of the app already uses ───
# Corroborated against a live capture whose room conditions matched the
# race as independently recorded in the ladder (season 2 / weather 1 /
# ground 1 == Summer / Sunny / Firm).
GAME_SEASON: dict[int, str] = {
    1: RaceSeason.SPRING,
    2: RaceSeason.SUMMER,
    3: RaceSeason.AUTUMN,
    4: RaceSeason.WINTER,
}
GAME_WEATHER: dict[int, str] = {
    1: Weather.SUNNY,
    2: Weather.CLOUDY,
    3: Weather.RAINY,
    4: Weather.SNOWY,
}
GAME_GROUND: dict[int, str] = {
    1: GroundCondition.FIRM,
    2: GroundCondition.GOOD,
    3: GroundCondition.SOFT,
    4: GroundCondition.HEAVY,
}
# 1 nige / 2 senko / 3 sashi / 4 oikomi, in the app's English wording.
GAME_RUNNING_STYLE: dict[int, str] = {1: "Front", 2: "Pace", 3: "Late", 4: "End"}
# Aptitude grades are 1..8 = G..S in the game's own ordering.
GAME_APTITUDE: dict[int, str] = {
    1: "G", 2: "F", 3: "E", 4: "D", 5: "C", 6: "B", 7: "A", 8: "S",
}

VALID_SOURCES = {s.value for s in RaceCaptureSource}


class CaptureError(Exception):
    """Payload the ingest endpoint should reject with a 4xx."""


@dataclass(frozen=True)
class IngestResult:
    capture: RaceCapture
    created: bool  # False when an identical room was already uploaded


def _parse_start_time(raw: Any) -> datetime | None:
    """The game hands us naive local-ish strings like
    '2026-07-26 23:30:45'. Store as UTC — the rest of the app treats
    naive timestamps as UTC throughout."""
    if not raw or not isinstance(raw, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def validate(payload: Any) -> dict:
    """Reject payloads we cannot key or attribute. Everything else is
    accepted and preserved — being permissive here is deliberate."""
    if not isinstance(payload, dict):
        raise CaptureError("payload must be a JSON object")
    source = payload.get("source") or RaceCaptureSource.MEMORY_SCAN.value
    if source not in VALID_SOURCES:
        raise CaptureError(
            f"unknown source {source!r}; expected one of {sorted(VALID_SOURCES)}"
        )
    room = payload.get("room")
    if not isinstance(room, dict):
        raise CaptureError("payload.room is required")
    if _as_int(room.get("saved_room_id")) is None:
        raise CaptureError("room.saved_room_id is required — it is the dedupe key")
    runners = payload.get("runners")
    if not isinstance(runners, list) or not runners:
        raise CaptureError("payload.runners must be a non-empty list")
    return payload


def ingest(payload: Any, *, submitted_by_user_id: int | None) -> IngestResult:
    """Store a capture. Idempotent on ``room.saved_room_id``.

    Every participant in a room can run the extractor, so the same
    result legitimately arrives several times. Rather than 409 on the
    second upload, return the existing pending capture — the uploader
    did nothing wrong. A capture that has already been confirmed is
    never silently overwritten.
    """
    payload = validate(payload)
    room = payload["room"]
    saved_room_id = _as_int(room.get("saved_room_id"))

    existing = db.session.scalars(
        select(RaceCapture).where(RaceCapture.saved_room_id == saved_room_id)
    ).first()
    if existing is not None:
        if existing.status == RaceCaptureStatus.PENDING:
            # Refresh the blob — a later capture may carry replay data
            # the first one lacked (RaceSimulateData is only live while
            # a replay is open).
            existing.payload_json = payload
            db.session.commit()
        return IngestResult(capture=existing, created=False)

    capture = RaceCapture(
        source=payload.get("source") or RaceCaptureSource.MEMORY_SCAN.value,
        submitted_by_user_id=submitted_by_user_id,
        payload_json=payload,
        saved_room_id=saved_room_id,
        race_instance_id=_as_int(room.get("race_instance_id")),
        room_name=(room.get("room_name") or None),
        started_at=_parse_start_time(room.get("start_time")),
        participant_count=_as_int(room.get("entry_num")) or len(payload["runners"]),
        status=RaceCaptureStatus.PENDING,
    )
    db.session.add(capture)
    db.session.commit()
    return IngestResult(capture=capture, created=True)


def summarize(capture: RaceCapture) -> dict:
    """Human-facing view of a capture, for the review page and the API
    response. Translates the game's integers into the words used
    everywhere else in the app."""
    payload = capture.payload_json or {}
    room = payload.get("room") or {}
    runners = []
    for r in payload.get("runners") or []:
        style = _as_int(r.get("running_style"))
        runners.append(
            {
                "gate": _as_int(r.get("gate")),
                "trainer_name": r.get("trainer_name"),
                "finish_position": _as_int(r.get("finish_position")),
                "finish_time_seconds": r.get("finish_time_seconds"),
                "gap_to_ahead_seconds": r.get("gap_to_ahead_seconds"),
                "strategy": GAME_RUNNING_STYLE.get(style) if style else None,
                "uma_name": r.get("uma_name"),
                "chara_id": _as_int(r.get("chara_id")),
                "stats": r.get("stats") or {},
                "skills": r.get("skills") or [],
            }
        )
    runners.sort(
        key=lambda x: (x["finish_position"] is None, x["finish_position"] or 0)
    )
    season = _as_int(room.get("season"))
    weather = _as_int(room.get("weather"))
    ground = _as_int(room.get("ground_condition"))
    return {
        "id": capture.id,
        "source": capture.source,
        "status": capture.status,
        "room_name": capture.room_name,
        "saved_room_id": capture.saved_room_id,
        "race_instance_id": capture.race_instance_id,
        "started_at": capture.started_at.isoformat() if capture.started_at else None,
        "participant_count": capture.participant_count,
        "race_season": GAME_SEASON.get(season) if season else None,
        "weather": GAME_WEATHER.get(weather) if weather else None,
        "ground_condition": GAME_GROUND.get(ground) if ground else None,
        "runners": runners,
    }


def list_pending_for_user(user_id: int, *, limit: int = 25) -> list[RaceCapture]:
    return list(
        db.session.scalars(
            select(RaceCapture)
            .where(RaceCapture.submitted_by_user_id == user_id)
            .where(RaceCapture.status == RaceCaptureStatus.PENDING)
            .order_by(RaceCapture.created_at.desc())
            .limit(limit)
        )
    )
