"""PR-OCR21 — apply published aptitude → stat modifiers to displayed
race results.

The in-game uma profile sheet shows raw stats. Race performance,
though, depends on those stats AS MODIFIED by the uma's aptitude
in the relevant slot for that specific race:

- Track aptitude for the race's surface (Turf or Dirt) → Power
  (the doc says surface aptitude affects acceleration, which
  converts to a Power stat modifier).
- Distance aptitude for the race's distance category → Speed.
- Style aptitude for the strategy the uma actually used → Wisdom.

Stamina and Guts have no aptitude row that modifies them.

Modifiers come from the published doc::

    https://docs.google.com/document/d/1gNcV7XLmxx0OI2DEAR8gmKb8P9BBhcwGhlJOVbYaXeo/preview?tab=t.0

(page 73 onwards). Surface + Distance modifiers are listed as
"raw speed/accel" percentages and need a non-linear conversion to
become stat modifiers — the in-race formula uses ``sqrt(stat) ×
aptitude`` so the equivalent stat modifier is
``(1 + raw_mod)² − 1``. That matches the doc's two concrete
calibration points: S (+5% raw) → +10.25% stat, B (-10% raw) →
-19% stat. Style is listed as a direct stat % already, no
conversion needed.

What this module DOES NOT do (deferred):

- Green-skill flat buffs (e.g. Right-Handed ◎ giving +60 Speed
  when track is right). Item 6 in the user's QoL list. When that
  lands, the input to ``effective_stats`` will switch from raw
  ``result.speed`` to a green-adjusted value computed upstream.
- Diminishing returns above 1200. The doc says ``anything over
  1200 is only worth half`` for race-performance math; that's a
  derived-quantity concern, not a displayed-stat concern, so it
  stays out of this layer.
"""

from __future__ import annotations

from dataclasses import dataclass

# Raw % modifiers from the published aptitude table. Surface and
# Distance are "raw speed/accel" modifiers that need the squared
# conversion; Style is a direct stat %.
_SURFACE_RAW: dict[str, float] = {
    "S": +0.05, "A": 0.0, "B": -0.10, "C": -0.20,
    "D": -0.30, "E": -0.50, "F": -0.70, "G": -0.90,
}
_DISTANCE_RAW: dict[str, float] = {
    "S": +0.05, "A": 0.0, "B": -0.10, "C": -0.20,
    "D": -0.40, "E": -0.60, "F": -0.80, "G": -0.90,
}
_STYLE_DIRECT: dict[str, float] = {
    "S": +0.10, "A": 0.0, "B": -0.15, "C": -0.25,
    "D": -0.40, "E": -0.60, "F": -0.80, "G": -0.90,
}


def _surface_stat_mod(grade: str) -> float:
    """Surface grade → Power stat % modifier (squared conversion)."""
    raw = _SURFACE_RAW.get(grade.upper())
    if raw is None:
        return 0.0
    return (1.0 + raw) ** 2 - 1.0


def _distance_stat_mod(grade: str) -> float:
    """Distance grade → Speed stat % modifier (squared conversion)."""
    raw = _DISTANCE_RAW.get(grade.upper())
    if raw is None:
        return 0.0
    return (1.0 + raw) ** 2 - 1.0


def _style_stat_mod(grade: str) -> float:
    """Style grade → Wisdom stat % modifier (direct from table)."""
    return _STYLE_DIRECT.get(grade.upper(), 0.0)


# Look-up: race-preset / result fields → aptitudes-JSON slot key.
# The aptitudes dict stored on OfficialRaceResult uses lowercased
# slot names (per PR-A1's confirm form); the race preset and
# strategy fields use Title Case.
_DIST_CATEGORY_TO_SLOT: dict[str, str] = {
    "Sprint": "sprint",
    "Mile": "mile",
    "Medium": "medium",
    "Long": "long",
}
_SURFACE_TO_SLOT: dict[str, str] = {
    "Turf": "turf",
    "Dirt": "dirt",
}
_STRATEGY_TO_SLOT: dict[str, str] = {
    "Front": "front",
    "Pace": "pace",
    "Late": "late",
    "End": "end",
}


@dataclass(frozen=True)
class EffectiveStats:
    """Aptitude-adjusted displayed stat values. None for any field
    means: raw is missing, race context is missing, aptitude data
    is missing, OR the modifier rounds to zero net effect — the
    template renders raw alone in that case."""

    speed: int | None = None
    power: int | None = None
    wisdom: int | None = None

    @property
    def any_set(self) -> bool:
        return any(v is not None for v in (self.speed, self.power, self.wisdom))


def effective_stats(
    *,
    raw_speed: int | None,
    raw_power: int | None,
    raw_wisdom: int | None,
    aptitudes: dict | None,
    surface: str | None,
    distance_category: str | None,
    strategy: str | None,
) -> EffectiveStats:
    """Compute aptitude-modified Speed / Power / Wisdom for one
    race result. Returns an empty struct when no modifiers apply
    (no aptitude data, no race context, all-A grades, etc.).

    Note: when the modifier is exactly zero (grade A) the field is
    left None — same display path as the no-modifier case, less
    UI noise."""
    if not aptitudes:
        return EffectiveStats()

    def _apply(
        raw: int | None,
        ctx_value: str | None,
        ctx_lookup: dict[str, str],
        apt_key: str,
        modifier_fn,
    ) -> int | None:
        if raw is None or ctx_value is None:
            return None
        slot = ctx_lookup.get(ctx_value)
        if slot is None:
            return None
        grade = (aptitudes.get(apt_key) or {}).get(slot)
        if not grade:
            return None
        mod = modifier_fn(grade)
        if mod == 0.0:
            return None
        return round(raw * (1.0 + mod))

    return EffectiveStats(
        speed=_apply(
            raw_speed, distance_category, _DIST_CATEGORY_TO_SLOT,
            "distance", _distance_stat_mod,
        ),
        power=_apply(
            raw_power, surface, _SURFACE_TO_SLOT,
            "track", _surface_stat_mod,
        ),
        wisdom=_apply(
            raw_wisdom, strategy, _STRATEGY_TO_SLOT,
            "style", _style_stat_mod,
        ),
    )


def effective_stats_for_result(result, race) -> EffectiveStats:
    """Convenience wrapper for Jinja: pulls fields off the
    ``OfficialRaceResult`` + ``OfficialRace`` instances directly."""
    preset = getattr(race, "preset", None) if race is not None else None
    return effective_stats(
        raw_speed=getattr(result, "speed", None),
        raw_power=getattr(result, "power", None),
        raw_wisdom=getattr(result, "wisdom", None),
        aptitudes=getattr(result, "aptitudes", None),
        surface=getattr(preset, "surface", None),
        distance_category=getattr(preset, "distance_category", None),
        strategy=getattr(result, "strategy", None),
    )
