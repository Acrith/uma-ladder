"""PR-OCR21 — aptitude → stat modifier math.

Calibration points come from the published doc the user linked
(see services.aptitude_stats docstring). The two concrete examples
the doc gives — `+5% raw → +10.25% stat` and `-10% raw → -19%
stat` — are the load-bearing tests; everything else exercises
the same formula applied to other ranks.
"""

from __future__ import annotations

import pytest

from uma_ladder.services.aptitude_stats import (
    EffectiveStats,
    effective_stats,
)

# ─── Math: squared conversion for Surface (Power) ────────────────


@pytest.mark.parametrize(
    "grade,expected_pct",
    [
        # Doc calibration: S = +5% raw → +10.25% stat.
        ("S", +0.1025),
        ("A", 0.0),
        # Doc calibration: B = -10% raw → -19% stat.
        ("B", -0.19),
        ("C", -0.36),  # (1 - 0.20)^2 - 1
        ("D", -0.51),  # (1 - 0.30)^2 - 1
        ("E", -0.75),  # (1 - 0.50)^2 - 1
        ("F", -0.91),  # (1 - 0.70)^2 - 1
        ("G", -0.99),  # (1 - 0.90)^2 - 1
    ],
)
def test_surface_aptitude_squared_conversion(grade: str, expected_pct: float) -> None:
    """Surface aptitude S..G → Power %-modifier. Squared conversion
    from raw speed/accel modifier per the in-race formula's
    sqrt(stat) dependence."""
    eff = effective_stats(
        raw_speed=None, raw_power=1000, raw_wisdom=None,
        aptitudes={"track": {"turf": grade}},
        surface="Turf",
        distance_category=None,
        strategy=None,
    )
    if expected_pct == 0.0:
        # A is a no-op; we leave the field None rather than render
        # a redundant `eff 1000`.
        assert eff.power is None
    else:
        expected = round(1000 * (1.0 + expected_pct))
        assert eff.power == expected


# ─── Math: distance row has different D..G values from surface ───


@pytest.mark.parametrize(
    "grade,expected_pct",
    [
        ("S", +0.1025),
        ("B", -0.19),
        ("D", -0.64),  # (1 - 0.40)^2 - 1 — steeper than surface's -0.51
        ("E", -0.84),  # (1 - 0.60)^2 - 1
        ("F", -0.96),  # (1 - 0.80)^2 - 1
        ("G", -0.99),  # (1 - 0.90)^2 - 1
    ],
)
def test_distance_aptitude_squared_conversion(
    grade: str, expected_pct: float
) -> None:
    eff = effective_stats(
        raw_speed=1000, raw_power=None, raw_wisdom=None,
        aptitudes={"distance": {"medium": grade}},
        surface=None,
        distance_category="Medium",
        strategy=None,
    )
    expected = round(1000 * (1.0 + expected_pct))
    assert eff.speed == expected


# ─── Math: style is a direct % (no squared) ──────────────────────


@pytest.mark.parametrize(
    "grade,expected_pct",
    [
        ("S", +0.10),
        ("B", -0.15),
        ("C", -0.25),
        ("D", -0.40),
        ("G", -0.90),
    ],
)
def test_style_aptitude_direct_percentage(
    grade: str, expected_pct: float
) -> None:
    """Style aptitude doesn't go through the squared conversion —
    the doc lists Wisdom modifiers directly (S=+10%, B=-15%, etc.)."""
    eff = effective_stats(
        raw_speed=None, raw_power=None, raw_wisdom=1000,
        aptitudes={"style": {"pace": grade}},
        surface=None,
        distance_category=None,
        strategy="Pace",
    )
    expected = round(1000 * (1.0 + expected_pct))
    assert eff.wisdom == expected


# ─── User-supplied calibration: Tamamo Cross-shaped run ──────────


def test_user_calibration_example() -> None:
    """The doc gave two specific numbers:
        +5% raw → +10.25% stat (about +120 at 1200 Speed/Power)
        -10% raw → -19% stat (about -230 at 1200)
    Pin them exactly so a future change to the conversion table
    has to consciously break this assertion."""
    eff_s = effective_stats(
        raw_speed=None, raw_power=1200, raw_wisdom=None,
        aptitudes={"track": {"turf": "S"}},
        surface="Turf",
        distance_category=None,
        strategy=None,
    )
    # 1200 * 1.1025 = 1323 → "about 120 over 1200"
    assert eff_s.power == 1323
    eff_b = effective_stats(
        raw_speed=None, raw_power=1200, raw_wisdom=None,
        aptitudes={"track": {"turf": "B"}},
        surface="Turf",
        distance_category=None,
        strategy=None,
    )
    # 1200 * 0.81 = 972 → "about 230 under 1200"
    assert eff_b.power == 972


# ─── Routing: which aptitude slot drives which stat ──────────────


def test_distance_category_routes_to_correct_slot() -> None:
    """Distance category is Title-cased on the race preset; the
    aptitude JSON is lowercased. The routing table maps between
    them."""
    apts = {"distance": {
        "sprint": "S", "mile": "A", "medium": "C", "long": "G",
    }}
    # Mile race → mile slot → A → no modifier.
    eff_mile = effective_stats(
        raw_speed=1000, raw_power=None, raw_wisdom=None,
        aptitudes=apts, surface=None, distance_category="Mile",
        strategy=None,
    )
    assert eff_mile.speed is None  # A grade = no-op

    # Long race → long slot → G → catastrophic penalty.
    eff_long = effective_stats(
        raw_speed=1000, raw_power=None, raw_wisdom=None,
        aptitudes=apts, surface=None, distance_category="Long",
        strategy=None,
    )
    assert eff_long.speed == round(1000 * 0.01)  # 10


def test_surface_routes_to_correct_slot() -> None:
    """Turf race reads the turf grade; Dirt race reads the dirt
    grade. Both happen at "track" in the aptitudes JSON."""
    apts = {"track": {"turf": "A", "dirt": "G"}}
    eff_turf = effective_stats(
        raw_speed=None, raw_power=1000, raw_wisdom=None,
        aptitudes=apts, surface="Turf",
        distance_category=None, strategy=None,
    )
    assert eff_turf.power is None  # A on turf = no-op
    eff_dirt = effective_stats(
        raw_speed=None, raw_power=1000, raw_wisdom=None,
        aptitudes=apts, surface="Dirt",
        distance_category=None, strategy=None,
    )
    assert eff_dirt.power == round(1000 * 0.01)  # G on dirt = -99%


def test_strategy_routes_to_correct_slot() -> None:
    """The uma's strategy (Front/Pace/Late/End) picks which style
    aptitude slot drives the Wisdom modifier — they don't all
    apply at once."""
    apts = {"style": {"front": "S", "pace": "A", "late": "B", "end": "G"}}
    for strategy, expected in [
        ("Front", round(1000 * 1.10)),
        ("Pace", None),  # A = no-op
        ("Late", round(1000 * 0.85)),
        ("End", round(1000 * 0.10)),
    ]:
        eff = effective_stats(
            raw_speed=None, raw_power=None, raw_wisdom=1000,
            aptitudes=apts, surface=None, distance_category=None,
            strategy=strategy,
        )
        assert eff.wisdom == expected, f"strategy={strategy}"


# ─── Defensive cases ─────────────────────────────────────────────


def test_missing_aptitudes_returns_empty() -> None:
    eff = effective_stats(
        raw_speed=1200, raw_power=1200, raw_wisdom=1200,
        aptitudes=None, surface="Turf",
        distance_category="Medium", strategy="Pace",
    )
    assert eff == EffectiveStats()


def test_missing_race_context_returns_empty() -> None:
    """Without surface / distance_category / strategy, none of the
    three modifiers can fire."""
    apts = {
        "track": {"turf": "S"},
        "distance": {"medium": "S"},
        "style": {"pace": "S"},
    }
    eff = effective_stats(
        raw_speed=1000, raw_power=1000, raw_wisdom=1000,
        aptitudes=apts,
        surface=None, distance_category=None, strategy=None,
    )
    assert eff == EffectiveStats()


def test_partial_race_context_partial_modifiers() -> None:
    """If only the strategy is missing (e.g. a result imported
    before PR-OCR13 wired strategy through), Speed + Power still
    modify but Wisdom doesn't."""
    apts = {
        "track": {"turf": "S"},
        "distance": {"medium": "S"},
        "style": {"pace": "S"},
    }
    eff = effective_stats(
        raw_speed=1000, raw_power=1000, raw_wisdom=1000,
        aptitudes=apts,
        surface="Turf", distance_category="Medium", strategy=None,
    )
    assert eff.speed == round(1000 * 1.1025)
    assert eff.power == round(1000 * 1.1025)
    assert eff.wisdom is None


def test_missing_aptitude_slot_returns_none_for_that_stat() -> None:
    """If the result has aptitudes for distance but the user didn't
    fill in the relevant slot (e.g. a Mile race but the uma's
    aptitude JSON only has medium/long set), no modifier applies
    — fall back to raw display."""
    apts = {"distance": {"medium": "S", "long": "A"}}  # no mile
    eff = effective_stats(
        raw_speed=1000, raw_power=None, raw_wisdom=None,
        aptitudes=apts,
        surface=None, distance_category="Mile", strategy=None,
    )
    assert eff.speed is None


def test_raw_stat_none_returns_none_for_that_stat() -> None:
    """A result without a stat value can't be modified."""
    apts = {"distance": {"medium": "S"}}
    eff = effective_stats(
        raw_speed=None, raw_power=None, raw_wisdom=None,
        aptitudes=apts,
        surface=None, distance_category="Medium", strategy=None,
    )
    assert eff == EffectiveStats()


def test_grade_case_insensitive() -> None:
    """Grades stored on the row could be lowercase if a future
    flow normalises differently — the lookup should still hit."""
    apts = {"distance": {"medium": "s"}}  # lowercase
    eff = effective_stats(
        raw_speed=1000, raw_power=None, raw_wisdom=None,
        aptitudes=apts,
        surface=None, distance_category="Medium", strategy=None,
    )
    assert eff.speed == round(1000 * 1.1025)


def test_green_buff_adds_to_raw_for_stats_without_aptitude() -> None:
    """PR-SK10 — Stamina and Guts get no aptitude treatment but
    can receive flat green-skill buffs (Sunny Days ◎ = +60 Guts,
    etc.). With apt missing for these axes, the eff value is just
    raw + buff."""
    eff = effective_stats(
        raw_speed=None, raw_stamina=1070, raw_power=None,
        raw_guts=553, raw_wisdom=None,
        aptitudes={},
        surface=None, distance_category=None, strategy=None,
        buff_stamina=60, buff_guts=40,
    )
    assert eff.stamina == 1130
    assert eff.guts == 593


def test_green_buff_compounds_with_aptitude() -> None:
    """Game canon order: effective = (raw + green) × (1 + apt).
    With distance apt S (+10.25%) AND a +60 green buff on Speed,
    a 1200-Speed uma displays 1389 — apt math operates on the
    green-adjusted base, not on raw."""
    eff = effective_stats(
        raw_speed=1200, raw_stamina=None, raw_power=None,
        raw_guts=None, raw_wisdom=None,
        aptitudes={"distance": {"medium": "S"}},
        surface=None, distance_category="Medium", strategy=None,
        buff_speed=60,
    )
    # (1200 + 60) * 1.1025 = 1389.15 → 1389
    assert eff.speed == 1389


def test_green_buff_negative_value_reduces_eff() -> None:
    """× tier green skills store negative buff values. With a
    -40 Speed buff and no aptitude, the eff value is raw - 40."""
    eff = effective_stats(
        raw_speed=1200, raw_stamina=None, raw_power=None,
        raw_guts=None, raw_wisdom=None,
        aptitudes=None,
        surface=None, distance_category=None, strategy=None,
        buff_speed=-40,
    )
    assert eff.speed == 1160


def test_no_buff_no_apt_returns_none() -> None:
    """When neither a green buff nor an aptitude touches a stat,
    its eff is None so the template falls back to displaying raw
    alone (no redundant `eff 1197` line)."""
    eff = effective_stats(
        raw_speed=1200, raw_stamina=1000, raw_power=1100,
        raw_guts=500, raw_wisdom=600,
        aptitudes={},
        surface=None, distance_category=None, strategy=None,
    )
    assert eff.speed is None
    assert eff.stamina is None
    assert eff.power is None
    assert eff.guts is None
    assert eff.wisdom is None


def test_effective_stats_for_result_passes_through_buff(app) -> None:
    """PR-SK10 — the Jinja wrapper accepts a `Buff`-shaped object
    (anything with .speed/.stamina/etc. attributes) and threads
    the components into the calculation. Driving via the actual
    `Buff` dataclass from skill_catalog so the integration is
    pinned in CI."""
    from types import SimpleNamespace

    from uma_ladder.services.aptitude_stats import effective_stats_for_result
    from uma_ladder.services.skill_catalog import Buff

    result = SimpleNamespace(
        speed=1200, stamina=1000, power=1100, guts=500, wisdom=600,
        aptitudes=None, strategy=None,
    )
    race = SimpleNamespace(preset=SimpleNamespace(
        direction=None, surface=None,
        distance_category=None, distance_meters=None, venue=None,
    ))
    buff = Buff(speed=60, stamina=20, guts=-15)
    eff = effective_stats_for_result(result, race, buff)
    assert eff.speed == 1260
    assert eff.stamina == 1020
    assert eff.guts == 485
    # Power and Wisdom untouched.
    assert eff.power is None
    assert eff.wisdom is None


def test_jinja_global_registered(app) -> None:
    """The Jinja global ``effective_stats`` is what the race detail
    template calls per result. Pinned here so a refactor of the
    registration site can't silently break the template."""
    assert "effective_stats" in app.jinja_env.globals
