from __future__ import annotations

from enum import StrEnum


class Surface(StrEnum):
    TURF = "Turf"
    DIRT = "Dirt"


class DistanceCategory(StrEnum):
    SPRINT = "Sprint"
    MILE = "Mile"
    MEDIUM = "Medium"
    LONG = "Long"


class Direction(StrEnum):
    LEFT = "Left"
    RIGHT = "Right"
    STRAIGHT = "Straight"
    STRETCH = "Stretch"


class PresetSource(StrEnum):
    G1_IMPORT = "g1_import"
    CUSTOM_BUILTIN = "custom_builtin"
    MANUAL = "manual"


class SeasonStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class OfficialRaceStatus(StrEnum):
    DRAFT = "draft"
    REGISTRATION_OPEN = "registration_open"
    REGISTRATION_CLOSED = "registration_closed"
    ROOM_CODE_PENDING = "room_code_pending"
    ROOM_CODE_AVAILABLE = "room_code_available"
    ROOM_CODE_EXPIRED = "room_code_expired"
    RESULTS_PENDING = "results_pending"
    RESULTS_SUBMITTED = "results_submitted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RegistrationStatus(StrEnum):
    REGISTERED = "registered"
    CANCELLED = "cancelled"
    WAITLISTED = "waitlisted"


class DraftMatchStatus(StrEnum):
    WAITING_FOR_OPPONENT = "waiting_for_opponent"
    SUBMITTING_UMAS = "submitting_umas"
    READY_CHECK = "ready_check"
    BAN_PHASE = "ban_phase"
    RANDOMIZING_RACE = "randomizing_race"
    RANDOMIZATION_FAILED = "randomization_failed"
    ROOM_CODE_PENDING = "room_code_pending"
    ROOM_CODE_AVAILABLE = "room_code_available"
    ROOM_CODE_EXPIRED = "room_code_expired"
    RESULTS_PENDING = "results_pending"
    RESULTS_SUBMITTED = "results_submitted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DraftBanType(StrEnum):
    UMA = "uma"
    DIRECTION = "direction"
    DISTANCE_CATEGORY = "distance_category"
    VENUE = "venue"
    SURFACE = "surface"


# Canonical venues mentioned in the appendix. Used to validate ban inputs.
VENUES: tuple[str, ...] = (
    "Sapporo",
    "Hakodate",
    "Fukushima",
    "Niigata",
    "Tokyo",
    "Nakayama",
    "Chukyo",
    "Kyoto",
    "Hanshin",
    "Kokura",
    "Oi",
)
