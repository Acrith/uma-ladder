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
    """Aptitude- and green-skill-adjusted displayed stat values.
    None for any field means: raw is missing, no modifier applies
    (no aptitude, no green buff, all-A grades), OR the modifiers
    round to zero net effect — the template renders raw alone in
    that case.

    PR-SK10 — expanded from three to five fields. Green skills
    can buff Stamina and Guts (which no aptitude row touches),
    so those fields are now part of the struct."""

    speed: int | None = None
    stamina: int | None = None
    power: int | None = None
    guts: int | None = None
    wisdom: int | None = None

    @property
    def any_set(self) -> bool:
        return any(
            v is not None
            for v in (self.speed, self.stamina, self.power, self.guts, self.wisdom)
        )


def effective_stats(
    *,
    raw_speed: int | None,
    raw_stamina: int | None = None,
    raw_power: int | None,
    raw_guts: int | None = None,
    raw_wisdom: int | None,
    aptitudes: dict | None,
    surface: str | None,
    distance_category: str | None,
    strategy: str | None,
    buff_speed: int = 0,
    buff_stamina: int = 0,
    buff_power: int = 0,
    buff_guts: int = 0,
    buff_wisdom: int = 0,
) -> EffectiveStats:
    """Compute the displayed stat after both green-skill flat
    buffs (item 6) and aptitude modifiers (item 7). Game order:

        effective = (raw + green_buff) * (1 + aptitude_modifier)

    A field is None when:
      - raw is missing entirely (we can't modify what we don't have),
      - no buff AND no aptitude touches this stat (no change to
        show; template falls back to displaying raw alone).

    Stamina and Guts only get effective values when a green buff
    touches them — no aptitude row affects either."""
    apts = aptitudes or {}

    def _apt_mod(
        ctx_value: str | None,
        ctx_lookup: dict[str, str],
        apt_key: str,
        modifier_fn,
    ) -> float:
        """Return the aptitude % modifier for this stat. 0.0 if
        the relevant slot isn't filled or context is missing —
        keeps the buff+apt combination math working when one
        side is absent."""
        if ctx_value is None:
            return 0.0
        slot = ctx_lookup.get(ctx_value)
        if slot is None:
            return 0.0
        grade = (apts.get(apt_key) or {}).get(slot)
        if not grade:
            return 0.0
        return modifier_fn(grade)

    def _eff(
        raw: int | None,
        buff: int,
        apt_mod: float,
    ) -> int | None:
        if raw is None:
            return None
        if buff == 0 and apt_mod == 0.0:
            # No change to display.
            return None
        return round((raw + buff) * (1.0 + apt_mod))

    return EffectiveStats(
        speed=_eff(
            raw_speed,
            buff_speed,
            _apt_mod(
                distance_category, _DIST_CATEGORY_TO_SLOT,
                "distance", _distance_stat_mod,
            ),
        ),
        stamina=_eff(raw_stamina, buff_stamina, 0.0),
        power=_eff(
            raw_power,
            buff_power,
            _apt_mod(
                surface, _SURFACE_TO_SLOT, "track", _surface_stat_mod,
            ),
        ),
        guts=_eff(raw_guts, buff_guts, 0.0),
        wisdom=_eff(
            raw_wisdom,
            buff_wisdom,
            _apt_mod(
                strategy, _STRATEGY_TO_SLOT, "style", _style_stat_mod,
            ),
        ),
    )


def effective_stats_for_result(
    result, race, green_buffs=None
) -> EffectiveStats:
    """Convenience wrapper for Jinja: pulls raw stats off the
    ``OfficialRaceResult`` + race context off ``OfficialRace``.

    PR-SK10 — accepts an optional `green_buffs` arg (a ``Buff``
    instance or dict with .speed/.stamina/.power/.guts/.wisdom).
    The route pre-computes this via
    ``skill_catalog.passive_buffs_by_result`` to keep template
    logic simple."""
    preset = getattr(race, "preset", None) if race is not None else None
    return effective_stats(
        raw_speed=getattr(result, "speed", None),
        raw_stamina=getattr(result, "stamina", None),
        raw_power=getattr(result, "power", None),
        raw_guts=getattr(result, "guts", None),
        raw_wisdom=getattr(result, "wisdom", None),
        aptitudes=getattr(result, "aptitudes", None),
        surface=getattr(preset, "surface", None),
        distance_category=getattr(preset, "distance_category", None),
        strategy=getattr(result, "strategy", None),
        buff_speed=getattr(green_buffs, "speed", 0) if green_buffs else 0,
        buff_stamina=getattr(green_buffs, "stamina", 0) if green_buffs else 0,
        buff_power=getattr(green_buffs, "power", 0) if green_buffs else 0,
        buff_guts=getattr(green_buffs, "guts", 0) if green_buffs else 0,
        buff_wisdom=getattr(green_buffs, "wisdom", 0) if green_buffs else 0,
    )
