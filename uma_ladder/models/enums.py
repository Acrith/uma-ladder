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


class DraftInviteStatus(StrEnum):
    """Layer-A invite states. PENDING is the only active one — the
    rest are terminal. EXPIRED is reserved for a future TTL sweeper
    (not yet implemented; pending invites just linger)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


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


class OfficialRaceVisibility(StrEnum):
    """PR-J13 — race targeting.

    PUBLIC  — visible on the index, anyone can register.
    PRIVATE — visible only to the organizer + invitees; an explicit
              invitee list (`official_race_invitees`) gates both
              viewing and registration.
    CLUB    — reserved for the future Club-only follow-up. Not
              accepted yet by the create form; the column allows it
              so the schema doesn't have to migrate twice.
    """

    PUBLIC = "public"
    PRIVATE = "private"
    CLUB = "club"


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
    # PR-OCR1 — sandbox uploads of the in-game Uma profile / character
    # sheet. Tagged distinctly from OCR_RESULT so the admin sandbox can
    # filter to its own attempts and future Uma-sheet-specific parsing
    # logic knows what input shape to expect.
    UMA_SHEET = "uma_sheet"
    OTHER = "other"


class OcrParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"
    CONFIRMED = "confirmed"


class RaceCaptureSource(StrEnum):
    """How a race capture was obtained from the game client.

    The ladder is deliberately agnostic here: an ingested payload is
    normalized to the same result lines whichever tool produced it, so
    a new community capture method only adds a value to this enum.

    PACKET_CAPTURE — the msgpack response the server sent the client,
      dumped at the wire by a CarrotJuicer-family tool (the method the
      wider Umamusume tooling community standardized on).
    MEMORY_SCAN — live IL2CPP objects read out of the game process by
      a Frida helper (see docs/room-match-extraction.md).
    OCR — the legacy screenshot pipeline; kept as the fallback for
      players who won't run a helper app.
    """

    PACKET_CAPTURE = "packet_capture"
    MEMORY_SCAN = "memory_scan"
    OCR = "ocr"
    MANUAL = "manual"


class RaceCaptureStatus(StrEnum):
    """A capture never writes to the ladder on its own — a human
    confirms it first (PROJECT_INTENTIONS §13)."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


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
