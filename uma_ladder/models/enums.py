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
