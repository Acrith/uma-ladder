from __future__ import annotations

import random
from collections.abc import Sequence

import pytest

from uma_ladder.models import RacePreset
from uma_ladder.models.enums import PresetSource
from uma_ladder.services.randomizer import (
    Bans,
    PresetPool,
    RandomizerError,
    filter_presets,
    pick_preset,
)


def _make(
    *,
    id: int,
    venue: str = "Tokyo",
    surface: str = "Turf",
    distance: int = 2000,
    category: str = "Medium",
    direction: str = "Left",
    source: str = PresetSource.CUSTOM_BUILTIN,
    enabled: bool = True,
) -> RacePreset:
    p = RacePreset(
        source=source,
        name=f"P{id}",
        venue=venue,
        surface=surface,
        distance_meters=distance,
        distance_category=category,
        direction=direction,
        course_variant=None,
        max_runners=16,
        enabled=enabled,
    )
    p.id = id
    return p


def _pool() -> Sequence[RacePreset]:
    return [
        _make(id=1, venue="Tokyo", direction="Left", category="Medium", surface="Turf"),
        _make(id=2, venue="Sapporo", direction="Right", category="Long", surface="Turf"),
        _make(
            id=3,
            venue="Tokyo",
            direction="Left",
            category="Sprint",
            surface="Dirt",
        ),
        _make(
            id=4,
            venue="Hanshin",
            direction="Right",
            category="Long",
            surface="Turf",
            source=PresetSource.G1_IMPORT,
        ),
        _make(id=5, venue="Kyoto", direction="Right", category="Mile", enabled=False),
    ]


def test_disabled_presets_excluded() -> None:
    out = filter_presets(_pool(), PresetPool.G1_CUSTOM, Bans())
    assert all(p.enabled for p in out)
    assert 5 not in [p.id for p in out]


def test_pool_g1_only_filters_source() -> None:
    out = filter_presets(_pool(), PresetPool.G1, Bans())
    assert [p.id for p in out] == [4]


def test_pool_custom_only_filters_source() -> None:
    out = filter_presets(_pool(), PresetPool.CUSTOM, Bans())
    assert {p.id for p in out} == {1, 2, 3}


def test_venue_ban_excludes_tokyo() -> None:
    out = filter_presets(_pool(), PresetPool.G1_CUSTOM, Bans(venues=frozenset({"Tokyo"})))
    assert all(p.venue != "Tokyo" for p in out)


def test_direction_ban_excludes_left() -> None:
    out = filter_presets(_pool(), PresetPool.G1_CUSTOM, Bans(directions=frozenset({"Left"})))
    assert all(p.direction != "Left" for p in out)


def test_distance_category_ban() -> None:
    out = filter_presets(
        _pool(), PresetPool.G1_CUSTOM, Bans(distance_categories=frozenset({"Long"}))
    )
    assert all(p.distance_category != "Long" for p in out)


def test_surface_ban() -> None:
    out = filter_presets(_pool(), PresetPool.G1_CUSTOM, Bans(surfaces=frozenset({"Dirt"})))
    assert all(p.surface != "Dirt" for p in out)


def test_pick_returns_only_candidate_when_one_left() -> None:
    bans = Bans(
        directions=frozenset({"Right"}),
        surfaces=frozenset({"Dirt"}),
    )
    chosen = pick_preset(_pool(), PresetPool.G1_CUSTOM, bans, rng=random.Random(0))
    assert chosen.id == 1


def test_pick_is_deterministic_with_seeded_rng() -> None:
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    a = pick_preset(_pool(), PresetPool.G1_CUSTOM, Bans(), rng=rng_a)
    b = pick_preset(_pool(), PresetPool.G1_CUSTOM, Bans(), rng=rng_b)
    assert a.id == b.id


def test_pick_raises_when_pool_empty() -> None:
    bans = Bans(
        venues=frozenset({"Tokyo", "Sapporo", "Hanshin", "Kyoto"}),
    )
    with pytest.raises(RandomizerError) as exc:
        pick_preset(_pool(), PresetPool.G1_CUSTOM, bans, rng=random.Random(0))
    assert "no preset" in exc.value.reason


def test_unknown_pool_raises() -> None:
    with pytest.raises(ValueError):
        filter_presets(_pool(), "bogus", Bans())
