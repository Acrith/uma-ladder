"""Race-day condition helpers (PR-G3).

Three independent enums — `RaceSeason`, `Weather`, `GroundCondition` —
attached to ChampionsMeeting (admin-set), OfficialRace (organizer-set),
and DraftMatch (auto-rolled at preset selection time).

Game-logic constraint: **Snowy weather requires Winter season.** All
other (season, weather) combinations are valid. Ground condition is
independent of both.
"""

from __future__ import annotations

import random
from collections.abc import Iterable

from ..models.enums import GroundCondition, RaceSeason, Weather


class TrackConditionError(ValueError):
    pass


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
        # Snowy is winter-only; drop it from the pool when off-season.
        weather_pool = [w for w in weather_pool if w != Weather.SNOWY]
    if not weather_pool:
        raise TrackConditionError("no valid weather after bans")
    weather = rng.choice(weather_pool)

    ground_pool = [g.value for g in GroundCondition if g.value not in forbidden_grounds]
    if not ground_pool:
        raise TrackConditionError("no valid ground condition after bans")
    ground = rng.choice(ground_pool)

    return season, weather, ground
