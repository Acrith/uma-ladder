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


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
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
            existing.payload_json = _merge_payloads(existing.payload_json, payload)
            db.session.commit()
        elif existing.status == RaceCaptureStatus.CONFIRMED:
            # Already on the ladder, so results are settled — but a
            # later capture taken with the replay open can still add
            # telemetry. Accept only that, never anything that could
            # move a placement.
            stored = dict(existing.payload_json or {})
            gained = False
            for key in ("sim", "scenario"):
                if not stored.get(key) and payload.get(key):
                    stored[key] = payload[key]
                    gained = True
            if gained:
                existing.payload_json = stored
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


def _scored(payload: dict | None) -> int:
    """How much usable outcome a payload carries. Used to decide which
    of two captures of the same room to keep."""
    if not payload:
        return 0
    runners = _runner_rows(payload)
    return sum(1 for r in runners if r.get("finish_position") is not None)


def _merge_payloads(old: dict | None, new: dict) -> dict:
    """Union of two captures of the same room.

    A second upload can be richer (a replay was open, so it carries
    telemetry) or poorer (the scenario failed to decode, so it has no
    finishing order). Keep whichever runner list actually resolved the
    outcome, and take replay data from whichever has it.
    """
    if not old:
        return new
    merged = dict(new)
    if _scored(old) > _scored(new):
        merged["runners"] = old.get("runners") or new.get("runners")
        if old.get("results"):
            merged["results"] = old["results"]
    for key in ("sim", "scenario"):
        if not merged.get(key) and old.get(key):
            merged[key] = old[key]
    return merged


# Raw game field -> aptitude slot. The archive keeps these at the top
# level of each runner; the upload payload nests them under
# `aptitudes`. Normalizing here means every reader sees one shape.
_RAW_APTITUDE_FIELDS = {
    "proper_ground_turf": "turf",
    "proper_ground_dirt": "dirt",
    "proper_distance_short": "sprint",
    "proper_distance_mile": "mile",
    "proper_distance_middle": "medium",
    "proper_distance_long": "long",
    "proper_running_style_nige": "front",
    "proper_running_style_senko": "pace",
    "proper_running_style_sashi": "late",
    "proper_running_style_oikomi": "end",
}


def _runner_rows(payload: dict) -> list[dict]:
    """Runner rows with the outcome folded in.

    The extractor's upload payload already merges finishing order into
    each runner, but the on-disk archive keeps `results` separate. Accept
    both so a capture is never silently shown as having no result.
    """
    by_gate = {r.get("gate"): r for r in payload.get("results") or []}
    out = []
    for r in payload.get("runners") or []:
        gate = r.get("gate", r.get("frame_order"))
        res = by_gate.get(gate) or {}
        row = dict(r)
        row.setdefault("gate", gate)
        for key in ("finish_position", "finish_time_seconds", "gap_to_ahead_seconds"):
            if row.get(key) is None and res.get(key) is not None:
                row[key] = res[key]
        # The archive keeps the game's own field names at the top level
        # (pow / wiz); the upload payload nests them under `stats`.
        # Normalize to `stats` so every reader sees one shape.
        if not row.get("stats"):
            raw = {
                "speed": r.get("speed"),
                "stamina": r.get("stamina"),
                "power": r.get("power", r.get("pow")),
                "guts": r.get("guts"),
                "wit": r.get("wit", r.get("wiz")),
            }
            if any(v is not None for v in raw.values()):
                row["stats"] = raw
        if not row.get("aptitudes"):
            apts = {
                slot: r.get(field)
                for field, slot in _RAW_APTITUDE_FIELDS.items()
                if r.get(field) is not None
            }
            if apts:
                row["aptitudes"] = apts
        out.append(row)
    return out


def summarize(capture: RaceCapture) -> dict:
    """Human-facing view of a capture, for the review page and the API
    response. Translates the game's integers into the words used
    everywhere else in the app."""
    payload = capture.payload_json or {}
    room = payload.get("room") or {}
    runners = []
    for r in _runner_rows(payload):
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


# ─── review: turning a capture into ladder results ───────────────────

@dataclass(frozen=True)
class RunnerMatch:
    """One capture row, resolved as far as we can against the ladder."""

    gate: int | None
    trainer_name: str | None
    finish_position: int | None
    finish_time_seconds: float | None
    strategy: str | None
    user_id: int | None          # suggested ladder account, None if unknown
    user_label: str | None
    match_confidence: str        # "exact" | "display" | "none"
    uma_character_id: int | None
    uma_name: str | None
    stats: dict
    skills: list


def _index_users() -> tuple[dict, dict]:
    """(username → user, display_name → user), both lowercased."""
    from ..models import User, UserProfile

    users = list(db.session.scalars(select(User).where(User.disabled_at.is_(None))))
    by_username = {u.username.lower(): u for u in users}
    by_display: dict[str, object] = {}
    profiles = db.session.scalars(
        select(UserProfile).where(
            UserProfile.user_id.in_([u.id for u in users] or [0])
        )
    )
    by_id = {u.id: u for u in users}
    for p in profiles:
        if p.display_name:
            by_display.setdefault(p.display_name.strip().lower(), by_id.get(p.user_id))
    return by_username, by_display


def _resolve_uma(card_id: int | None, chara_id: int | None) -> tuple[int | None, str | None]:
    """Game card_id → the ladder's UmaCharacter.

    `UmaOutfit.costume_id` *is* the game's card_id, so the outfit table
    is the reliable bridge. Falls back to chara_id (= card_id // 100)
    when that particular costume isn't in the catalog yet.
    """
    from ..models import UmaCharacter, UmaOutfit

    if card_id:
        outfit = db.session.scalars(
            select(UmaOutfit).where(UmaOutfit.costume_id == card_id)
        ).first()
        if outfit is not None and outfit.character is not None:
            return outfit.character.id, outfit.character.name_en
    if chara_id:
        outfit = db.session.scalars(
            select(UmaOutfit).where(UmaOutfit.costume_id.between(chara_id * 100, chara_id * 100 + 99))
        ).first()
        if outfit is not None and outfit.character is not None:
            return outfit.character.id, outfit.character.name_en
        char = db.session.get(UmaCharacter, chara_id)
        if char is not None:
            return char.id, char.name_en
    return None, None


def _uma_image(card_id: int | None, chara_id: int | None) -> str | None:
    """Portrait for a runner. Prefers the exact costume, falls back to
    any costume of the same character — same bridge the podium art
    uses (UmaOutfit.costume_id is the game's card_id)."""
    from ..models import UmaCharacter, UmaOutfit

    if card_id:
        outfit = db.session.scalars(
            select(UmaOutfit).where(UmaOutfit.costume_id == card_id)
        ).first()
        if outfit is not None and outfit.image_url:
            return outfit.image_url
    if chara_id:
        outfit = db.session.scalars(
            select(UmaOutfit)
            .where(UmaOutfit.costume_id.between(chara_id * 100, chara_id * 100 + 99))
            .where(UmaOutfit.image_url.is_not(None))
        ).first()
        if outfit is not None:
            return outfit.image_url
        char = db.session.get(UmaCharacter, chara_id)
        if char is not None and char.image_url:
            return char.image_url
    return None


def match_runners(capture: RaceCapture) -> list[RunnerMatch]:
    """Resolve each captured runner against ladder accounts + the uma
    catalog. Suggestions only — the reviewer confirms or corrects."""
    by_username, by_display = _index_users()
    payload = capture.payload_json or {}
    out: list[RunnerMatch] = []
    for r in _runner_rows(payload):
        name = (r.get("trainer_name") or "").strip()
        key = name.lower()
        user = by_username.get(key)
        confidence = "exact" if user else "none"
        if user is None and key in by_display:
            user = by_display[key]
            confidence = "display"
        style = _as_int(r.get("running_style"))
        char_id, uma_name = _resolve_uma(
            _as_int(r.get("card_id")), _as_int(r.get("chara_id"))
        )
        out.append(
            RunnerMatch(
                gate=_as_int(r.get("gate")),
                trainer_name=name or None,
                finish_position=_as_int(r.get("finish_position")),
                finish_time_seconds=r.get("finish_time_seconds"),
                strategy=GAME_RUNNING_STYLE.get(style) if style else None,
                user_id=user.id if user else None,
                user_label=(user.username if user else None),
                match_confidence=confidence,
                uma_character_id=char_id,
                uma_name=uma_name,
                stats=r.get("stats") or {},
                skills=r.get("skills") or [],
            )
        )
    out.sort(key=lambda m: (m.finish_position is None, m.finish_position or 0))
    return out


def _skill_names(skill_entries: list) -> tuple[str, ...]:
    """Capture gives us exact skill_ids; the detail path takes names.

    Going id -> name is a lookup rather than a guess, so unlike the OCR
    path there is no fuzzy matching here. Ids missing from the catalog
    are dropped rather than invented.
    """
    from ..models import UmaSkill

    ids = [
        _as_int(s.get("skill_id"))
        for s in skill_entries or []
        if _as_int(s.get("skill_id"))
    ]
    if not ids:
        return ()
    rows = db.session.scalars(
        select(UmaSkill).where(UmaSkill.gametora_id.in_(ids))
    ).all()
    by_id = {r.gametora_id: r.name_en for r in rows}
    return tuple(by_id[i] for i in ids if i in by_id)


def _aptitude_block(raw: dict | None) -> dict | None:
    """Game aptitudes (1..8 per slot) -> the {track,distance,style}
    letter-grade shape the race page already renders."""
    if not raw:
        return None
    def grade(key: str) -> str | None:
        return GAME_APTITUDE.get(_as_int(raw.get(key)) or 0)

    block = {
        "track": {"turf": grade("turf"), "dirt": grade("dirt")},
        "distance": {
            "sprint": grade("sprint"), "mile": grade("mile"),
            "medium": grade("medium"), "long": grade("long"),
        },
        "style": {
            "front": grade("front"), "pace": grade("pace"),
            "late": grade("late"), "end": grade("end"),
        },
    }
    # Drop categories the capture didn't carry rather than writing nulls.
    block = {k: v for k, v in block.items() if any(v.values())}
    return block or None


def _format_finish(seconds: float | None) -> str | None:
    """Race times read as M:SS.s on the result page, matching how the
    OCR path stores them."""
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:04.1f}" if minutes else f"{rest:.1f}"


def confirm(
    capture_id: int,
    *,
    race_id: int,
    user_by_gate: dict[int, int],
    actor_user_id: int,
) -> RaceCapture:
    """Write a reviewed capture through to the ladder.

    Deliberately reuses the same `official_service.submit_results` path
    the screenshot flow uses, so a capture-sourced result is
    indistinguishable downstream — points, Elo, achievements and the
    results table all behave exactly as before.
    """
    from . import official as official_service

    capture = db.session.get(RaceCapture, capture_id)
    if capture is None:
        raise CaptureError("capture not found")
    if capture.status == RaceCaptureStatus.CONFIRMED:
        raise CaptureError("this capture has already been confirmed")

    payload = capture.payload_json or {}
    by_gate = {_as_int(r.get("gate")): r for r in _runner_rows(payload)}

    lines: list = []
    for gate, user_id in sorted(user_by_gate.items()):
        runner = by_gate.get(gate)
        if runner is None or not user_id:
            continue
        placement = _as_int(runner.get("finish_position"))
        if placement is None:
            continue
        stats = runner.get("stats") or {}
        style = _as_int(runner.get("running_style"))
        char_id, uma_name = _resolve_uma(
            _as_int(runner.get("card_id")), _as_int(runner.get("chara_id"))
        )
        lines.append(
            official_service.ResultLine(
                user_id=user_id,
                placement=placement,
                uma_character_id=char_id,
                uma_name=uma_name,
                strategy=GAME_RUNNING_STYLE.get(style) if style else None,
                speed=_as_int(stats.get("speed")),
                stamina=_as_int(stats.get("stamina")),
                power=_as_int(stats.get("power")),
                guts=_as_int(stats.get("guts")),
                wisdom=_as_int(stats.get("wit")),
                finish_time_or_lengths=_format_finish(runner.get("finish_time_seconds")),
                gate=gate,
                fav_rank=_as_int(runner.get("popularity")),
            )
        )

    if not lines:
        raise CaptureError("no runners were matched to ladder accounts")

    # Placements must be dense for the race: if only some runners are
    # ladder members, re-rank the ones we keep so the ladder sees
    # 1..N without gaps.
    lines.sort(key=lambda x: x.placement)
    lines = [
        official_service.ResultLine(**{**line.__dict__, "placement": i + 1})
        for i, line in enumerate(lines)
    ]

    saved = official_service.submit_results(
        race_id, lines, confirmed_by_user_id=actor_user_id, notify=True
    )

    # The capture carries far more than a placement: every runner's
    # full skill list and all ten aptitude grades. Push those through
    # the same enrichment path the stat-screen OCR uses, so the race
    # page renders them exactly as it always has.
    runner_by_user = {
        uid: by_gate.get(gate)
        for gate, uid in user_by_gate.items()
        if by_gate.get(gate)
    }
    for result in saved:
        runner = runner_by_user.get(result.user_id)
        if not runner:
            continue
        names = _skill_names(runner.get("skills") or [])
        apts = _aptitude_block(runner.get("aptitudes"))
        if not names and not apts:
            continue
        try:
            official_service.submit_result_details(
                result.id,
                official_service.ResultDetailsUpdate(
                    skill_names=names, aptitudes=apts
                ),
                by_user_id=actor_user_id,
            )
            _stamp_activations(result.id, runner.get("skills") or [])
        except Exception:  # noqa: BLE001
            # Enrichment is a bonus; never lose a confirmed result to it.
            db.session.rollback()

    capture.status = RaceCaptureStatus.CONFIRMED
    capture.official_race_id = race_id
    capture.confirmed_by_user_id = actor_user_id
    capture.confirmed_at = datetime.now(UTC)
    db.session.commit()
    return capture


def _stamp_activations(result_id: int, skill_entries: list) -> None:
    """Record which of a result's skills actually fired.

    Only a memory capture knows this, so it is applied after the shared
    enrichment path rather than by widening its contract. Skills the
    scenario doesn't track keep `activated = None`, which the UI shows
    as "unknown" rather than "didn't fire".
    """
    from ..models import OfficialRaceResultSkill, UmaSkill

    verdicts = {
        _as_int(e.get("skill_id")): e.get("activated")
        for e in skill_entries
        if e.get("activated") is not None
    }
    if not verdicts:
        return
    rows = db.session.scalars(
        select(OfficialRaceResultSkill).where(
            OfficialRaceResultSkill.official_race_result_id == result_id
        )
    ).all()
    catalog_ids = {
        r.id: r.gametora_id
        for r in db.session.scalars(
            select(UmaSkill).where(
                UmaSkill.id.in_([row.skill_id for row in rows if row.skill_id] or [0])
            )
        )
    }
    for row in rows:
        game_id = catalog_ids.get(row.skill_id)
        if game_id in verdicts:
            row.activated = bool(verdicts[game_id])
    db.session.commit()


def reject(capture_id: int, *, actor_user_id: int, reason: str | None = None) -> RaceCapture:
    capture = db.session.get(RaceCapture, capture_id)
    if capture is None:
        raise CaptureError("capture not found")
    capture.status = RaceCaptureStatus.REJECTED
    capture.rejected_reason = (reason or "").strip()[:500] or None
    capture.confirmed_by_user_id = actor_user_id
    capture.confirmed_at = datetime.now(UTC)
    db.session.commit()
    return capture


# ─── replay ──────────────────────────────────────────────────────────

# Enough distinct hues for a full field; the winner is drawn last and
# highlighted, so ordering here only needs to be stable.
_REPLAY_COLORS = (
    "#22d3ee", "#e879f9", "#fbbf24", "#34d399", "#fb7185", "#a78bfa",
    "#60a5fa", "#f97316", "#4ade80", "#f472b6", "#2dd4bf", "#facc15",
    "#818cf8", "#fb923c", "#38bdf8", "#c084fc", "#f87171", "#a3e635",
)


def replay_series(capture: RaceCapture) -> dict | None:
    """Chart-ready per-runner traces from a captured replay.

    Returns None unless the capture carries `sim` — telemetry only
    exists when a replay was open at capture time, so most captures
    won't have it and the page simply omits the chart.

    Produces three views over the same x-axis (race progress in
    metres): gap to the leader, stamina remaining, and speed.
    """
    payload = capture.payload_json or {}
    sim = payload.get("sim") or {}
    frames = sim.get("frames") or []
    if not frames:
        # No live telemetry captured — but the scenario blob every
        # capture carries contains the full replay (frames, results,
        # events). Parsing it here means result-screen captures get a
        # replay, and older uploads gain one retroactively.
        from . import race_blob

        blob_sim = race_blob.sim_from_payload(payload)
        if not blob_sim:
            return None
        # Course geometry only exists in live captures; carry it over
        # if a frameless sim happened to record it.
        if sim.get("course"):
            blob_sim["course"] = sim["course"]
        sim = blob_sim
        frames = sim["frames"]

    names_by_index: dict[int, str] = {}
    for idx, r in enumerate(_runner_rows(payload)):
        gate = _as_int(r.get("gate"))
        names_by_index[gate - 1 if gate else idx] = (
            r.get("trainer_name") or f"gate {gate or idx + 1}"
        )
    finish_by_index = {
        (_as_int(r.get("gate")) or 0) - 1: _as_int(r.get("finish_position"))
        for r in _runner_rows(payload)
    }

    n = min(len(frames[0].get("h") or []), len(names_by_index) or 99)
    if not n:
        return None

    runners: list[dict] = []
    for k in range(n):
        runners.append(
            {
                "index": k,
                "name": names_by_index.get(k, f"runner {k + 1}"),
                "finish": finish_by_index.get(k),
                "color": _REPLAY_COLORS[k % len(_REPLAY_COLORS)],
                "gap": [],
                "hp": [],
                "speed": [],
                # Own distance + lane drive the bird's-eye replay strip.
                "dist": [],
                "lane": [],
                # Running position 1..N at each sample. A bump chart of
                # this reads far better than N overlapping distance
                # curves: an overtake is a line crossing.
                "rank": [],
            }
        )
    x_axis: list[float] = []

    max_dist = 0.0
    max_hp = 0.0
    t_axis: list[float] = []
    for frame in frames:
        row = frame.get("h") or []
        if len(row) < n:
            continue
        dists = [row[k].get("Distance") or 0.0 for k in range(n)]
        leader = max(dists)
        max_dist = max(max_dist, leader)
        x_axis.append(round(leader, 1))
        t_axis.append(round(frame.get("t") or 0.0, 3))
        # Rank at this instant: furthest along is 1st.
        order = sorted(range(n), key=lambda i: -dists[i])
        rank_of = {idx: pos + 1 for pos, idx in enumerate(order)}
        for k in range(n):
            runners[k]["rank"].append(rank_of[k])
            hp = row[k].get("Hp") or 0.0
            max_hp = max(max_hp, hp)
            runners[k]["dist"].append(round(dists[k], 2))
            runners[k]["lane"].append(round(row[k].get("LanePosition") or 0.0, 4))
            runners[k]["gap"].append((round(leader, 1), round(leader - dists[k], 2)))
            runners[k]["hp"].append((round(leader, 1), round(hp, 1)))
            runners[k]["speed"].append(
                (round(leader, 1), round(row[k].get("Speed") or 0.0, 2))
            )

    if max_dist <= 0:
        return None

    # Two corrections that everything downstream depends on:
    #   * the finish line is where the course says it is, not where the
    #     last telemetry sample happens to fall (runners overrun it);
    #   * the replay clock is the clock the results table quotes, so a
    #     race that "took 90.5s" plays for 90.5s.
    course = _course_segments(sim)
    race_distance = _course_distance(sim) or _preset_distance(capture) or max_dist
    if course:
        course["profile"] = _elevation_profile(course["slopes"], race_distance)
    scale = _time_scale(sim, frames, race_distance)
    if scale != 1.0:
        t_axis = [round(t * scale, 3) for t in t_axis]

    events = _replay_events(sim, scale, n)
    if events:
        _place_events(events, t_axis, x_axis, runners)
        events["feed"] = _event_feed(events, runners)

    max_gap = max(
        (p[1] for r in runners for p in r["gap"]), default=0.0
    )
    # The game hands us each runner's real last-spurt point, so this is
    # measured rather than inferred from a formula.
    rows_by_gate = {
        (_as_int(r.get("gate")) or 0) - 1: r for r in _runner_rows(payload)
    }
    for k, r in enumerate(runners):
        horse = (sim.get("horses") or [{}] * n)[k] if k < len(sim.get("horses") or []) else {}
        r["spurt"] = horse.get("LastSpurtStartDistance")
        src = rows_by_gate.get(k) or {}
        r["image"] = _uma_image(
            _as_int(src.get("card_id")), _as_int(src.get("chara_id"))
        )

    return {
        "x_axis": x_axis,
        "t_axis": t_axis,
        "duration": t_axis[-1] if t_axis else 0.0,
        "runner_count": n,
        "phases": _phase_bands(race_distance),
        "course": course,
        "events": events,
        "race_distance": round(race_distance, 1),
        "time_scale": round(scale, 5),
        "sectionals": _sectionals(runners, race_distance),
        "max_distance": max_dist,
        "max_gap": max_gap or 1.0,
        "max_hp": max_hp or 1.0,
        "max_speed": max(
            (p[1] for r in runners for p in r["speed"]), default=1.0
        ),
        "frame_count": len(frames),
        "runners": sorted(
            runners, key=lambda r: (r["finish"] is None, r["finish"] or 99)
        ),
    }


# The game's four race phases, split at 1/6, 2/3 and 5/6 of the course
# — the same bands umalator and the other community simulators draw, so
# players read them on sight. The 2/3 edge is corroborated by game
# data (every runner's game-supplied LastSpurtStartDistance on a
# 1600 m course landed at ~1069 m = 0.668); 1/6 and 5/6 are the
# engine's phase convention.
_PHASE_EDGES = (
    (0.0, 1 / 6, "Opening leg"),
    (1 / 6, 2 / 3, "Middle leg"),
    (2 / 3, 5 / 6, "Final leg"),
    (5 / 6, 1.0, "Last spurt"),
)
SECTIONAL_M = 200


def _phase_bands(max_distance: float) -> list[dict]:
    return [
        {
            "label": label,
            "start": round(max_distance * a, 1),
            "end": round(max_distance * b, 1),
        }
        for a, b, label in _PHASE_EDGES
    ]


def _sectionals(runners: list[dict], max_distance: float) -> dict | None:
    """Average speed per 200 m segment, per runner.

    How racing analysts actually read a race: it shows *where* it was
    won rather than only who won. Each cell is scored against the
    field's mean for that same segment, so colour means "faster than
    everyone else here", not "fast in absolute terms".
    """
    if max_distance <= 0 or not runners:
        return None
    # Clip to the finish: runners keep going past the line, and binning
    # that in would credit them for metres that were never part of the
    # race. The last segment is whatever remains, not a full 200 m.
    finish = int(max_distance)
    edges = list(range(0, finish, SECTIONAL_M)) + [finish]
    if len(edges) < 2:
        return None

    rows = []
    for r in runners:
        cells = []
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            speeds = [
                sp for d, (_x, sp) in zip(r["dist"], r["speed"], strict=False)
                if lo <= d < hi
            ]
            cells.append(round(sum(speeds) / len(speeds), 2) if speeds else None)
        rows.append({"index": r["index"], "name": r["name"],
                     "finish": r["finish"], "color": r["color"], "cells": cells})
    # Read top-down as the race finished, not in gate order.
    rows.sort(key=lambda row: (row["finish"] is None, row["finish"] or 99))

    # Per-segment field mean, for the relative colour scale.
    means = []
    for i in range(len(edges) - 1):
        vals = [row["cells"][i] for row in rows if row["cells"][i] is not None]
        means.append(sum(vals) / len(vals) if vals else None)
    spread = 0.0
    for row in rows:
        for i, v in enumerate(row["cells"]):
            if v is not None and means[i]:
                spread = max(spread, abs(v - means[i]))
    return {
        "edges": edges,
        "labels": [
            ("finish" if i == len(edges) - 2 else f"{edges[i + 1]}m")
            for i in range(len(edges) - 1)
        ],
        "rows": rows,
        "means": [round(m, 2) if m else None for m in means],
        "spread": round(spread, 3) or 1.0,
    }


# ─── course geometry, event stream, and the two clocks ───────────────

def _course_segments(sim: dict) -> dict | None:
    """The track itself: straights, corners and hills, in metres.

    Captured per race from the game's own course tables, so it needs no
    external course database and is correct for whatever venue ran.
    """
    course = sim.get("course") or {}
    out: dict = {"straights": [], "corners": [], "slopes": []}
    for seg in course.get("straights") or []:
        out["straights"].append(
            {
                "start": _as_float(seg.get("start")),
                "end": _as_float(seg.get("end")),
                # Front = the finishing straight, AcrossFront = the back
                # stretch — real racing names, straight from the game.
                "front_type": seg.get("front_type"),
            }
        )
    for seg in course.get("corners") or []:
        out["corners"].append(
            {
                "start": _as_float(seg.get("start")),
                "end": _as_float(seg.get("end")),
                "number": _as_int(seg.get("number")),
                "is_final": bool(seg.get("is_final")),
            }
        )
    for seg in course.get("slopes") or []:
        kind = str(seg.get("slope_type") or "").lower()
        out["slopes"].append(
            {
                "start": _as_float(seg.get("start")),
                "end": _as_float(seg.get("end")),
                "kind": "up" if kind == "up" else "down" if kind == "down" else kind,
            }
        )
    if not any(out.values()):
        return None
    return out


def _elevation_profile(slopes: list[dict], distance: float) -> list | None:
    """A schematic side-view of the course, as (metre, height 0..1).

    Integrated from the slope segments assuming constant grade — the
    game gives positions but not steepness, so the silhouette's shape
    (where the hills are) is real while its heights are indicative.
    """
    if not slopes or not distance:
        return None
    points: list[tuple[float, float]] = [(0.0, 0.0)]
    height = 0.0
    cursor = 0.0
    for seg in sorted(slopes, key=lambda s: s["start"] or 0):
        start, end = seg["start"] or 0.0, seg["end"] or 0.0
        if end <= start:
            continue
        if start > cursor:
            points.append((start, height))
        height += (end - start) * (1 if seg["kind"] == "up" else -1)
        points.append((end, height))
        cursor = end
    if cursor < distance:
        points.append((distance, height))
    lo = min(h for _, h in points)
    hi = max(h for _, h in points)
    if hi - lo < 1e-6:
        return None
    return [[round(d, 1), round((h - lo) / (hi - lo), 3)] for d, h in points]


def _preset_distance(capture: RaceCapture) -> float | None:
    """The race's official distance, once the capture is attached.

    Blob-parsed replays have no course geometry, and the telemetry
    overruns the line — but a confirmed capture's race preset states
    the distance outright, which keeps the two-clock rescale exact.
    """
    try:
        race = capture.official_race
        distance = getattr(getattr(race, "preset", None), "distance_meters", None)
        return float(distance) if distance else None
    except Exception:  # noqa: BLE001 — detached instance, missing preset
        return None


def _course_distance(sim: dict) -> float | None:
    """Where the finish line actually is.

    The telemetry runs past it — runners decelerate after the line — so
    binning or scaling against the last sampled distance would stretch
    the whole race by however far the field overran.
    """
    course = sim.get("course") or {}
    ends = [
        _as_float(seg.get("end"))
        for key in ("straights", "corners")
        for seg in course.get(key) or []
    ]
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def _time_scale(sim: dict, frames: list, distance: float | None) -> float:
    """Frame time -> race time.

    The simulation's clock and the clock the game reports finishing
    times on are not the same; they differ by a fixed factor per race.
    Rather than hard-code it, measure it: interpolate when each runner
    crossed the line in frame time and compare against the finish time
    the game recorded. Median, so one bad trace can't skew it.
    """
    horses = sim.get("horses") or []
    if not distance or not frames or not horses:
        return 1.0
    ratios: list[float] = []
    for idx, horse in enumerate(horses):
        finish = _as_float(horse.get("FinishTime"))
        if not finish:
            continue
        prev: tuple[float, float] | None = None
        for frame in frames:
            row = frame.get("h") or []
            if idx >= len(row):
                break
            t = _as_float(frame.get("t")) or 0.0
            d = _as_float(row[idx].get("Distance")) or 0.0
            if prev and prev[1] < distance <= d and d > prev[1]:
                a = (distance - prev[1]) / (d - prev[1])
                crossed = prev[0] + a * (t - prev[0])
                if crossed > 0:
                    ratios.append(finish / crossed)
                break
            prev = (t, d)
    if not ratios:
        return 1.0
    ratios.sort()
    return ratios[len(ratios) // 2]


# param[2] of a Skill event is the effect duration in ten-thousandths of
# a second, already scaled for course length by the game — verified
# across 1600/2200/3600 m captures, where one skill reads
# 48000/66000/107999 for a single 3.0 s base. Do not "correct" for
# distance here; the game has already done it. -1 marks a skill that was
# equipped but never fired.
_SKILL_DURATION_UNIT = 10_000.0
_EVENT_NEVER_FIRED = -1


def _catalog_names_by_game_id(skill_ids: set[int]) -> dict[int, str]:
    """Catalog names for the game's skill ids, where we have them."""
    if not skill_ids:
        return {}
    from ..models import UmaSkill

    try:
        rows = db.session.scalars(
            select(UmaSkill).where(UmaSkill.gametora_id.in_(skill_ids))
        ).all()
    except Exception:  # noqa: BLE001
        return {}
    return {r.gametora_id: r.name_en for r in rows if r.gametora_id}


def _replay_events(sim: dict, scale: float, runner_count: int) -> dict | None:
    """The race's typed event stream, on the same clock as the replay.

    Skill events carry who cast it, what fired, how long it lasted and —
    via a bitmask — everyone it landed on, which is what separates a
    self-buff from a debuff thrown at the field.
    """
    events = sim.get("events") or []
    if not events:
        return None

    skills: list[dict] = []
    moments: list[dict] = []
    for ev in events:
        kind = str(ev.get("type") or "")
        param = ev.get("param") or []
        t = (_as_float(ev.get("t")) or 0.0) * scale
        if kind == "Skill":
            if len(param) < 3 or param[2] == _EVENT_NEVER_FIRED:
                continue  # equipped but never activated
            caster = _as_int(param[0])
            if caster is None or not (0 <= caster < runner_count):
                continue
            mask = _as_int(param[4]) if len(param) > 4 else 0
            targets = [
                i for i in range(runner_count) if (mask or 0) & (1 << i)
            ]
            skills.append(
                {
                    "t": round(t, 3),
                    "runner": caster,
                    "skill_id": _as_int(param[1]),
                    "duration": round(
                        (_as_float(param[2]) or 0.0) / _SKILL_DURATION_UNIT * scale, 3
                    ),
                    # A skill that lands on someone other than its caster
                    # is acting on them, not buffing self.
                    "targets": [i for i in targets if i != caster],
                }
            )
        elif kind in ("CompeteTop", "CompeteFight", "ReleaseConservePower"):
            who = _as_int(param[0]) if param else None
            if who is None or not (0 <= who < runner_count):
                continue
            moments.append({"t": round(t, 3), "kind": kind, "runner": who})

    if not skills and not moments:
        return None

    names = _catalog_names_by_game_id(
        {s["skill_id"] for s in skills if s["skill_id"]}
    )
    for s in skills:
        s["name"] = names.get(s["skill_id"]) or f"skill {s['skill_id']}"
    skills.sort(key=lambda s: s["t"])
    moments.sort(key=lambda m: m["t"])
    return {"skills": skills, "moments": moments}


def _place_events(events: dict, t_axis: list, x_axis: list, runners: list) -> None:
    """Give every event a place on the chart as well as a time.

    The timeline is drawn against distance, so an event that only knows
    *when* it happened has nowhere to sit. Interpolating the leader's
    distance at that moment puts it on the same x-axis as the traces,
    and the caster's rank puts it on their own line.
    """
    if not t_axis or not x_axis:
        return
    by_index = {r["index"]: r for r in runners}

    def at(t: float) -> tuple[float, float]:
        """(leader distance, blend position) at race time t."""
        if t <= t_axis[0]:
            return x_axis[0], 0.0
        if t >= t_axis[-1]:
            return x_axis[-1], float(len(t_axis) - 1)
        lo, hi = 0, len(t_axis) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if t_axis[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        span = t_axis[lo + 1] - t_axis[lo] if lo + 1 < len(t_axis) else 0
        a = (t - t_axis[lo]) / span if span else 0.0
        x = x_axis[lo] + (x_axis[min(lo + 1, len(x_axis) - 1)] - x_axis[lo]) * a
        return x, lo + a

    for item in list(events.get("skills") or []) + list(events.get("moments") or []):
        x, pos = at(item["t"])
        item["x"] = round(x, 1)
        who = by_index.get(item["runner"])
        if who and who["rank"]:
            item["rank"] = who["rank"][min(int(pos), len(who["rank"]) - 1)]
        if "duration" in item:
            item["x_end"] = round(at(item["t"] + item["duration"])[0], 1)


# What the non-skill events mean, in racing language rather than the
# engine's. Anything not listed is dropped rather than shown raw.
_MOMENT_TEXT = {
    "CompeteTop": "contests the lead",
    "CompeteFight": "fights for position",
    "ReleaseConservePower": "kicks for home",
}


def _event_feed(events: dict, runners: list) -> list[dict]:
    """One chronological commentary track over the whole race.

    Skills and race moments interleaved, so playback can read out what
    is happening rather than leaving it to be inferred from the lines.
    """
    names = {r["index"]: r["name"] for r in runners}
    colors = {r["index"]: r["color"] for r in runners}
    feed: list[dict] = []
    for s in events.get("skills") or []:
        feed.append(
            {
                "t": s["t"],
                "runner": s["runner"],
                "who": names.get(s["runner"], "?"),
                "color": colors.get(s["runner"], "#94a3b8"),
                "text": s["name"],
                "detail": (
                    f"hits {len(s['targets'])} rival"
                    f"{'s' if len(s['targets']) != 1 else ''}"
                    if s["targets"]
                    else f"{s['duration']:.1f}s"
                ),
                "kind": "debuff" if s["targets"] else "skill",
            }
        )
    for m in events.get("moments") or []:
        text = _MOMENT_TEXT.get(m["kind"])
        if not text:
            continue
        feed.append(
            {
                "t": m["t"],
                "runner": m["runner"],
                "who": names.get(m["runner"], "?"),
                "color": colors.get(m["runner"], "#94a3b8"),
                "text": text,
                "detail": "",
                "kind": "moment",
            }
        )
    feed.sort(key=lambda e: (e["t"], e["kind"] != "moment"))
    return feed


def replay_for_race(race_id: int) -> dict | None:
    """Replay traces for a race, if any confirmed capture carries them."""
    capture = db.session.scalars(
        select(RaceCapture)
        .where(RaceCapture.official_race_id == race_id)
        .where(RaceCapture.status == RaceCaptureStatus.CONFIRMED)
        .order_by(RaceCapture.confirmed_at.desc())
    ).first()
    return replay_series(capture) if capture else None
