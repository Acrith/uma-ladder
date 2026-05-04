"""Random preset selection with ban filtering.

Pure-ish: takes the candidate pool as a list, plus bans and an injectable
RNG so tests can be deterministic.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..models import RacePreset
from ..models.enums import PresetSource


class PresetPool:
    G1 = "g1"
    CUSTOM = "custom"
    G1_CUSTOM = "g1+custom"


_POOL_SOURCES: dict[str, frozenset[str]] = {
    PresetPool.G1: frozenset({PresetSource.G1_IMPORT}),
    PresetPool.CUSTOM: frozenset({PresetSource.CUSTOM_BUILTIN}),
    PresetPool.G1_CUSTOM: frozenset({PresetSource.G1_IMPORT, PresetSource.CUSTOM_BUILTIN}),
}


@dataclass(frozen=True)
class Bans:
    venues: frozenset[str] = field(default_factory=frozenset)
    directions: frozenset[str] = field(default_factory=frozenset)
    distance_categories: frozenset[str] = field(default_factory=frozenset)
    surfaces: frozenset[str] = field(default_factory=frozenset)


class RandomizerError(Exception):
    """Raised when no preset survives the filter pipeline.

    `reason` is a short, user-facing string. `eliminated_by` lists which
    bans removed candidates (best-effort, useful for debugging).
    """

    def __init__(self, reason: str, eliminated_by: Sequence[str] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.eliminated_by = tuple(eliminated_by)


def filter_presets(
    presets: Iterable[RacePreset], pool: str, bans: Bans
) -> list[RacePreset]:
    sources = _POOL_SOURCES.get(pool)
    if sources is None:
        raise ValueError(f"unknown preset pool: {pool!r}")
    out: list[RacePreset] = []
    for p in presets:
        if not p.enabled:
            continue
        if p.source not in sources:
            continue
        if p.venue in bans.venues:
            continue
        if p.direction in bans.directions:
            continue
        if p.distance_category in bans.distance_categories:
            continue
        if p.surface in bans.surfaces:
            continue
        out.append(p)
    return out


def pick_preset(
    presets: Iterable[RacePreset],
    pool: str,
    bans: Bans,
    rng: random.Random | None = None,
) -> RacePreset:
    candidates = filter_presets(presets, pool, bans)
    if not candidates:
        eliminated = []
        if bans.venues:
            eliminated.append(f"venues={sorted(bans.venues)}")
        if bans.directions:
            eliminated.append(f"directions={sorted(bans.directions)}")
        if bans.distance_categories:
            eliminated.append(f"distance_categories={sorted(bans.distance_categories)}")
        if bans.surfaces:
            eliminated.append(f"surfaces={sorted(bans.surfaces)}")
        raise RandomizerError(
            "no preset matches the configured pool and bans",
            eliminated,
        )
    chooser = rng or random.Random()
    return chooser.choice(candidates)
