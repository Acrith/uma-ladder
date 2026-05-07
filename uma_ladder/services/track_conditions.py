"""Race-day condition helpers (PR-G3 + PR-J2).

Three enums — `RaceSeason`, `Weather`, `GroundCondition` — attached
to ChampionsMeeting, OfficialRace, and DraftMatch.

Game-logic constraints (per Uma Musume mechanics):

1. **Snowy weather requires Winter season.** No other weather has a
   season constraint.
2. **Weather × Ground pairing** isn't free — the game only allows
   specific combinations:

       Sunny  → Firm or Good
       Cloudy → Firm or Good
       Rainy  → Soft or Heavy
       Snowy  → Good or Soft  (and Snowy is Winter-only, see #1)

Both constraints are enforced in `normalize()` and respected by
`roll_random()`.
"""

from __future__ import annotations

import random
from collections.abc import Iterable

from ..models.enums import GroundCondition, RaceSeason, Weather


class TrackConditionError(ValueError):
    pass


# Allowed ground conditions per weather. The game UI accepts only
# these eight combinations across all four weathers.
_GROUND_BY_WEATHER: dict[str, frozenset[str]] = {
    Weather.SUNNY:  frozenset({GroundCondition.FIRM, GroundCondition.GOOD}),
    Weather.CLOUDY: frozenset({GroundCondition.FIRM, GroundCondition.GOOD}),
    Weather.RAINY:  frozenset({GroundCondition.SOFT, GroundCondition.HEAVY}),
    Weather.SNOWY:  frozenset({GroundCondition.GOOD, GroundCondition.SOFT}),
}


def is_valid_weather_ground(weather: str | None, ground: str | None) -> bool:
    """True when the weather/ground pair is one of the eight game-
    canonical combinations (or when either side is unset — a partial
    form isn't a constraint violation, just incomplete)."""
    if weather is None or ground is None:
        return True
    allowed = _GROUND_BY_WEATHER.get(weather)
    return allowed is not None and ground in allowed


def _normalize(
    value: str | None, enum_cls: type, *, label: str
) -> str | None:
    """Coerce blank / unknown to None; reject malformed strings."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        return enum_cls(v).value
    except ValueError as exc:
        raise TrackConditionError(f"unknown {label}: {value!r}") from exc


def normalize(
    *,
    race_season: str | None,
    weather: str | None,
    ground_condition: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Validate + normalise a (season, weather, ground) triple.

    Empty strings become None (so blank form fields stay blank rather
    than failing). Unknown enum values raise `TrackConditionError`. The
    Snowy/Winter constraint is enforced as the last step so a partially
    filled form (e.g. weather chosen but season still blank) doesn't
    block the user from completing the form.
    """
    season = _normalize(race_season, RaceSeason, label="race_season")
    w = _normalize(weather, Weather, label="weather")
    g = _normalize(ground_condition, GroundCondition, label="ground_condition")
    if w == Weather.SNOWY and season is not None and season != RaceSeason.WINTER:
        raise TrackConditionError(
            "Snowy weather only happens in Winter races."
        )
    # PR-J2 — game-mechanic combo check. Only fires when both
    # fields are set; partially-filled forms still pass through.
    if w is not None and g is not None and not is_valid_weather_ground(w, g):
        allowed = sorted(_GROUND_BY_WEATHER.get(w, ()))
        raise TrackConditionError(
            f"{w} weather only allows ground: {', '.join(allowed)}."
        )
    return season, w, g


def roll_random(
    *,
    rng: random.Random | None = None,
    forbidden_seasons: Iterable[str] | None = None,
    forbidden_weathers: Iterable[str] | None = None,
    forbidden_grounds: Iterable[str] | None = None,
) -> tuple[str, str, str]:
    """Random (season, weather, ground) triple respecting Snowy/Winter.

    `forbidden_*` arguments let callers narrow the pool (future hook —
    e.g. a draft match that bans a season). When the pool empties for
    any axis, raises `TrackConditionError` so the caller can decide
    whether to retry with looser bans or surface the error.
    """
    rng = rng or random.Random()
    forbidden_seasons = set(forbidden_seasons or ())
    forbidden_weathers = set(forbidden_weathers or ())
    forbidden_grounds = set(forbidden_grounds or ())

    season_pool = [s.value for s in RaceSeason if s.value not in forbidden_seasons]
    if not season_pool:
        raise TrackConditionError("no valid race season after bans")
    season = rng.choice(season_pool)

    weather_pool = [w.value for w in Weather if w.value not in forbidden_weathers]
    if season != RaceSeason.WINTER:
        weather_pool = [w for w in weather_pool if w != Weather.SNOWY]
    if not weather_pool:
        raise TrackConditionError("no valid weather after bans")
    weather = rng.choice(weather_pool)

    # PR-J2 — pick ground from the pair-table for this weather, not
    # from the full GroundCondition enum. Each weather has exactly
    # two valid ground conditions in-game.
    allowed_grounds = _GROUND_BY_WEATHER.get(weather, frozenset())
    ground_pool = [
        g for g in allowed_grounds if g not in forbidden_grounds
    ]
    if not ground_pool:
        raise TrackConditionError(
            f"no valid ground condition for {weather} after bans"
        )
    ground = rng.choice(ground_pool)

    return season, weather, ground
