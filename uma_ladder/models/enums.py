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


class RaceSeason(StrEnum):
    """Game-side racing season — Spring/Summer/Autumn/Winter. Distinct
    from `Season` (the SQLAlchemy model used for ladder windows)."""

    SPRING = "Spring"
    SUMMER = "Summer"
    AUTUMN = "Autumn"
    WINTER = "Winter"


class Weather(StrEnum):
    SUNNY = "Sunny"
    CLOUDY = "Cloudy"
    RAINY = "Rainy"
    SNOWY = "Snowy"


class GroundCondition(StrEnum):
    """Track surface condition — drier → wetter."""

    FIRM = "Firm"
    GOOD = "Good"
    SOFT = "Soft"
    HEAVY = "Heavy"


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
    READY_CHECK = "ready_check"
    TRACK_BAN_PHASE = "track_ban_phase"
    RANDOMIZATION_FAILED = "randomization_failed"
    UMA_BAN_PHASE = "uma_ban_phase"
    ROOM_CODE_PENDING = "room_code_pending"
    ROOM_CODE_AVAILABLE = "room_code_available"
    ROOM_CODE_EXPIRED = "room_code_expired"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DraftBanType(StrEnum):
    UMA = "uma"
    DIRECTION = "direction"
    DISTANCE_CATEGORY = "distance_category"
    VENUE = "venue"
    SURFACE = "surface"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"  # webhook URL missing for this target


class NotificationEvent(StrEnum):
    OFFICIAL_RACE_PUBLISHED = "official_race_published"
    OFFICIAL_ROOM_CODE = "official_room_code"
    OFFICIAL_RESULTS = "official_results"
    OFFICIAL_RACE_CANCELLED = "official_race_cancelled"
    OFFICIAL_REGISTRATION_REMOVED = "official_registration_removed"
    DRAFT_ROOM_CODE = "draft_room_code"
    DRAFT_RESULTS = "draft_results"
    DRAFT_MATCH_CANCELLED = "draft_match_cancelled"
    ADMIN_ACTION = "admin_action"


class NotificationTarget(StrEnum):
    """Logical target name. Resolved to a webhook URL via env var."""

    RACE_REGISTRATION = "race_registration"
    OFFICIAL_RESULTS = "official_results"
    DRAFT_RESULTS = "draft_results"
    ADMIN_AUDIT = "admin_audit"
    FALLBACK = "fallback"


class UploadPurpose(StrEnum):
    OCR_RESULT = "ocr_result"
    AVATAR = "avatar"
    OTHER = "other"


class OcrParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"
    CONFIRMED = "confirmed"


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
