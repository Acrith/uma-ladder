"""Parse the §22 appendix custom-race format into structured rows.

Each non-empty, non-section-header line looks like::

    Sapporo Turf 2600m (Long) Right Max Runners: 14
    Niigata Turf 2400m (Medium) Left / Inner Max Runners: 18
    Hanshin Turf 3200m (Long) Right / Outer→Inner Max Runners: 18

Section headers ("Turf Races", "Dirt Races") are recognised but do not
override the per-line surface — every line names its own surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models.enums import VENUES, Direction, DistanceCategory, Surface

_SECTION_HEADERS = {"turf races", "dirt races"}

_LINE_RE = re.compile(
    r"""
    ^\s*
    (?P<venue>[A-Za-z]+)\s+
    (?P<surface>Turf|Dirt)\s+
    (?P<distance>\d{3,4})m\s*
    \(\s*(?P<category>Sprint|Mile|Medium|Long)\s*\)\s+
    (?P<direction>Left|Right|Straight|Stretch)
    (?:\s*/\s*(?P<variant>[^M]+?))?
    \s*Max\s+Runners:\s*(?P<max_runners>\d+)
    \s*$
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedPreset:
    name: str
    venue: str
    surface: str
    distance_meters: int
    distance_category: str
    direction: str
    course_variant: str | None
    max_runners: int


class PresetParseError(ValueError):
    def __init__(self, line_number: int, line: str, reason: str) -> None:
        super().__init__(f"line {line_number}: {reason}: {line!r}")
        self.line_number = line_number
        self.line = line
        self.reason = reason


_VARIANT_FIXUPS = {
    "innermax": "Inner",
    "outermax": "Outer",
    "outer→innermax": "Outer→Inner",
}


def _normalize_variant(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    # Repair a known appendix glitch where "InnerMax"/"OuterMax" appears
    # without a space before "Max Runners".
    key = cleaned.lower().replace(" ", "")
    if key in _VARIANT_FIXUPS:
        return _VARIANT_FIXUPS[key]
    return cleaned


def parse_lines(text: str) -> list[ParsedPreset]:
    parsed: list[ParsedPreset] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.lower() in _SECTION_HEADERS:
            continue

        m = _LINE_RE.match(stripped)
        if not m:
            raise PresetParseError(idx, raw, "unrecognised line format")

        venue = m["venue"]
        if venue not in VENUES:
            raise PresetParseError(idx, raw, f"unknown venue {venue!r}")

        surface = m["surface"]
        if surface not in (s.value for s in Surface):
            raise PresetParseError(idx, raw, f"unknown surface {surface!r}")

        category = m["category"]
        if category not in (c.value for c in DistanceCategory):
            raise PresetParseError(idx, raw, f"unknown distance category {category!r}")

        direction = m["direction"]
        if direction not in (d.value for d in Direction):
            raise PresetParseError(idx, raw, f"unknown direction {direction!r}")

        variant = _normalize_variant(m["variant"])
        distance = int(m["distance"])
        max_runners = int(m["max_runners"])
        if max_runners <= 0:
            raise PresetParseError(idx, raw, "max_runners must be positive")

        name_parts = [venue, surface, f"{distance}m", f"({category})", direction]
        if variant:
            name_parts.append(f"/ {variant}")
        name = " ".join(name_parts)

        parsed.append(
            ParsedPreset(
                name=name,
                venue=venue,
                surface=surface,
                distance_meters=distance,
                distance_category=category,
                direction=direction,
                course_variant=variant,
                max_runners=max_runners,
            )
        )
    return parsed
