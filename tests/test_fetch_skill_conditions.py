"""PR-SK2 — parser for GameTora's skill condition_groups.

The parser projects each upstream skill row to a `FetchedSkillCondition`
that the seeder writes into the `skill_conditions` table. Tests
exercise:

- The enum mappings (rotation → direction, ground_type → surface,
  weather, season, distance_type, running_style, is_basis_distance,
  track_id → venue).
- The DSL operators (`&` = AND across atomic predicates collapse
  into multiple typed columns; `@` = OR triggers a dynamic fallback
  unless the alternative codes collapse to a single meaningful
  value — e.g. season==1@season==5 → "Spring" since season==5 is
  Sakura, which our enum doesn't model).
- Effect type → stat column projection (1=Speed, 2=Stamina,
  3=Power, 4=Guts, 5=Wisdom; other effect types skipped).
- The value/10000 division.
- × tier debuffs storing as signed negative ints.
- Dynamic-condition fallback: any race-state atom (phase,
  order_rate, straight_random, etc.) forces is_dynamic=True.
"""

from __future__ import annotations

from uma_ladder.services.fetch_gametora import (
    FetchedSkillCondition,
    _coerce_skill_condition,
)


def _skill(
    skill_id: int,
    condition: str,
    *effects: tuple[int, int],
    name: str = "Test Skill",
) -> dict:
    return {
        "id": skill_id,
        "name_en": name,
        "condition_groups": [
            {
                "base_time": -1,
                "condition": condition,
                "effects": [{"type": t, "value": v} for t, v in effects],
            }
        ],
    }


# ─── enum mapping per atomic key ─────────────────────────────────


def test_rotation_maps_to_direction() -> None:
    """`rotation==1` → direction="Right"; `rotation==2` → "Left"."""
    cond = _coerce_skill_condition(_skill(1, "rotation==1", (1, 600000)))
    assert cond is not None
    assert cond.direction == "Right"
    assert cond.is_dynamic is False
    assert cond.buff_speed == 60

    cond2 = _coerce_skill_condition(_skill(2, "rotation==2"))
    assert cond2 is not None
    assert cond2.direction == "Left"


def test_ground_type_maps_to_surface() -> None:
    cond = _coerce_skill_condition(_skill(1, "ground_type==1"))
    assert cond.surface == "Turf"
    assert _coerce_skill_condition(_skill(2, "ground_type==2")).surface == "Dirt"


def test_weather_maps_to_weather() -> None:
    cases = {1: "Sunny", 2: "Cloudy", 3: "Rainy", 4: "Snowy"}
    for code, expected in cases.items():
        cond = _coerce_skill_condition(_skill(code, f"weather=={code}"))
        assert cond.weather == expected, f"weather=={code}"


def test_season_maps_to_season() -> None:
    cases = {1: "Spring", 2: "Summer", 3: "Autumn", 4: "Winter"}
    for code, expected in cases.items():
        cond = _coerce_skill_condition(_skill(code, f"season=={code}"))
        assert cond.season == expected, f"season=={code}"


def test_distance_type_maps_to_category() -> None:
    cases = {1: "Sprint", 2: "Mile", 3: "Medium", 4: "Long"}
    for code, expected in cases.items():
        cond = _coerce_skill_condition(_skill(code, f"distance_type=={code}"))
        assert cond.distance_category == expected, f"distance_type=={code}"


def test_running_style_maps_to_strategy() -> None:
    cases = {1: "Front", 2: "Pace", 3: "Late", 4: "End"}
    for code, expected in cases.items():
        cond = _coerce_skill_condition(_skill(code, f"running_style=={code}"))
        assert cond.strategy == expected, f"running_style=={code}"


def test_is_basis_distance_maps_to_is_standard() -> None:
    cond_t = _coerce_skill_condition(_skill(1, "is_basis_distance==1"))
    assert cond_t.is_standard_distance is True
    cond_f = _coerce_skill_condition(_skill(2, "is_basis_distance==0"))
    assert cond_f.is_standard_distance is False


def test_ground_condition_maps_to_typed_value() -> None:
    """PR-SK7 — `ground_condition==N` maps to the corresponding
    track condition name. 1=Firm, 2=Good, 3=Soft, 4=Heavy."""
    cases = {1: "Firm", 2: "Good", 3: "Soft", 4: "Heavy"}
    for code, expected in cases.items():
        cond = _coerce_skill_condition(
            _skill(code, f"ground_condition=={code}")
        )
        assert cond.ground_condition == expected, f"code={code}"
        assert cond.is_dynamic is False


def test_wet_conditions_or_collapses_to_exclude_firm() -> None:
    """PR-SK8 — `ground_condition==2@==3@==4` (Wet Conditions
    skill family, "anything but Firm") now resolves to
    `ground_condition_exclude="Firm"` instead of falling back to
    dynamic. The OR-over-(n-1)-codes shape leaves exactly one of
    the four enum values missing; that's the excluded value."""
    cond = _coerce_skill_condition(
        _skill(
            1,
            "ground_condition==2@ground_condition==3@ground_condition==4",
            (3, 600000),  # +60 Power
        )
    )
    assert cond is not None
    assert cond.is_dynamic is False
    assert cond.ground_condition is None
    assert cond.ground_condition_exclude == "Firm"
    assert cond.buff_power == 60


def test_ground_condition_ne_operator_maps_to_exclude() -> None:
    """PR-SK8 — `ground_condition!=N` (explicit negation, seen in
    a handful of skills) maps to `ground_condition_exclude=N`
    directly. Same semantic as the OR-over-(n-1)-codes shape,
    just terser source data."""
    cond = _coerce_skill_condition(_skill(1, "ground_condition!=1"))
    assert cond is not None
    assert cond.is_dynamic is False
    assert cond.ground_condition_exclude == "Firm"
    assert cond.ground_condition is None


def test_ground_condition_partial_or_still_dynamic() -> None:
    """`ground_condition==2@==3` (two of four codes, leaving two
    missing) can't collapse to a single exclude value. Stays
    dynamic — the skill chip won't gray out aggressively."""
    cond = _coerce_skill_condition(_skill(1, "ground_condition==2@ground_condition==3"))
    assert cond.is_dynamic is True
    assert cond.ground_condition is None
    assert cond.ground_condition_exclude is None


def test_same_skill_horse_count_ge_maps_to_min_holders() -> None:
    """PR-SK8 — Sympathy (`same_skill_horse_count>=5`) projects to
    `min_holders=5`. Display-time check compares to the count of
    results in the race carrying the same skill_id."""
    cond = _coerce_skill_condition(
        _skill(1, "same_skill_horse_count>=5", (1, 400000))
    )
    assert cond is not None
    assert cond.is_dynamic is False
    assert cond.min_holders == 5
    assert cond.max_holders is None
    assert cond.buff_speed == 40


def test_same_skill_horse_count_eq_maps_to_min_and_max() -> None:
    """Lone Wolf (`same_skill_horse_count==1`) needs `min_holders=
    max_holders=1` — exactly one holder, which is the uma itself."""
    cond = _coerce_skill_condition(
        _skill(1, "same_skill_horse_count==1", (1, 400000))
    )
    assert cond is not None
    assert cond.is_dynamic is False
    assert cond.min_holders == 1
    assert cond.max_holders == 1


def test_same_skill_horse_count_with_dynamic_companion_stays_dynamic() -> None:
    """When `same_skill_horse_count` ANDs with a dynamic predicate
    (e.g. `phase_random==1&same_skill_horse_count>=2`), the
    dynamic companion forces the whole condition to dynamic; the
    holder bound still records on the row so item 6 has the data
    if it ever decides to apply buffs to dynamic-but-bounded
    skills."""
    cond = _coerce_skill_condition(
        _skill(1, "phase_random==1&same_skill_horse_count>=2", (1, 200000))
    )
    assert cond is not None
    assert cond.is_dynamic is True
    assert cond.min_holders == 2


def test_track_id_maps_to_venue() -> None:
    """track_id reuses the G1_TRACK_ID_TO_VENUE map. 10006 = Tokyo."""
    cond = _coerce_skill_condition(_skill(1, "track_id==10006"))
    assert cond.venue == "Tokyo"
    # Unknown track id → dynamic (we can't map foreign venues
    # like Longchamp since our VENUES enum doesn't cover them).
    cond_foreign = _coerce_skill_condition(_skill(2, "track_id==99999"))
    assert cond_foreign.is_dynamic is True


# ─── operators ───────────────────────────────────────────────────


def test_and_combines_static_predicates() -> None:
    """`a&b` ANDs two static atoms into both columns. Pinned via
    a real-world example: Medium Race Enthusiast (Turf + Medium)."""
    cond = _coerce_skill_condition(
        _skill(1, "distance_type==3&ground_type==1", (5, 600000))
    )
    assert cond is not None
    assert cond.distance_category == "Medium"
    assert cond.surface == "Turf"
    assert cond.buff_wisdom == 60
    assert cond.is_dynamic is False


def test_or_collapses_when_codes_map_to_one_value() -> None:
    """`season==1@season==5` is "Spring OR Sakura". Sakura is a
    game-internal sub-season our RaceSeason enum doesn't model,
    so the OR collapses to just "Spring" — Spring Runner skills
    still apply correctly to Spring races."""
    cond = _coerce_skill_condition(_skill(1, "season==1@season==5"))
    assert cond is not None
    assert cond.season == "Spring"
    assert cond.is_dynamic is False


def test_or_falls_back_to_dynamic_when_codes_differ() -> None:
    """A genuine OR across distinct meaningful values can't be
    expressed in our typed schema (one column per axis). Mark
    dynamic so the consumer doesn't apply the wrong predicate."""
    cond = _coerce_skill_condition(_skill(1, "weather==1@weather==2"))
    assert cond.is_dynamic is True


def test_dynamic_atom_forces_dynamic() -> None:
    """Any predicate the parser doesn't know how to evaluate from
    static race context — phase, order_rate, straight_random,
    accumulatetime, etc. — flips the whole skill to dynamic."""
    cond = _coerce_skill_condition(
        _skill(1, "running_style==4&straight_random==1", (1, 250000))
    )
    assert cond is not None
    assert cond.is_dynamic is True


def test_always_predicate_is_no_op() -> None:
    """`always==1` is upstream's "unconditional truth". No
    predicate columns set, no is_dynamic flag — pure stat buff."""
    cond = _coerce_skill_condition(_skill(1, "always==1", (1, 400000)))
    assert cond is not None
    assert cond.direction is None
    assert cond.surface is None
    assert cond.is_dynamic is False
    assert cond.buff_speed == 40


# ─── effect types ────────────────────────────────────────────────


def test_effect_types_map_to_correct_stat_columns() -> None:
    """Type codes 1-5 map to speed/stamina/power/guts/wisdom."""
    cases = [
        (1, "buff_speed"),
        (2, "buff_stamina"),
        (3, "buff_power"),
        (4, "buff_guts"),
        (5, "buff_wisdom"),
    ]
    for code, col in cases:
        cond = _coerce_skill_condition(
            _skill(code, "always==1", (code, 600000))
        )
        for c in ("speed", "stamina", "power", "guts", "wisdom"):
            actual = getattr(cond, f"buff_{c}")
            expected = 60 if f"buff_{c}" == col else 0
            assert actual == expected, f"type={code} stat={c}"


def test_non_stat_effect_types_ignored() -> None:
    """Effect types we don't map (8=FoV, 9=panic, etc.) don't
    contribute to any stat column. Skill still gets a catalog row
    so item 5 can gray it out, but item 6 has nothing to buff."""
    cond = _coerce_skill_condition(_skill(1, "rotation==1", (8, 100000)))
    assert cond is not None
    assert cond.direction == "Right"
    assert all(
        getattr(cond, f"buff_{s}") == 0
        for s in ("speed", "stamina", "power", "guts", "wisdom")
    )


def test_effect_value_divided_by_10000() -> None:
    """Upstream value is 10000ths of a stat point. 600000 → +60,
    400000 → +40, etc."""
    assert _coerce_skill_condition(
        _skill(1, "always==1", (1, 600000))
    ).buff_speed == 60
    assert _coerce_skill_condition(
        _skill(1, "always==1", (1, 400000))
    ).buff_speed == 40


def test_x_tier_debuff_stores_as_negative() -> None:
    """× tier variants of green skills are debuffs — value is a
    negative int in the upstream payload. Schema stores them
    signed so item 6 can sum across positive + negative naturally."""
    cond = _coerce_skill_condition(
        _skill(1, "rotation==1", (1, -400000), name="Right-Handed ×")
    )
    assert cond.buff_speed == -40


def test_multiple_effects_accumulate_across_stats() -> None:
    """Right-Handed Demon: +60 Speed AND +60 Power on right-handed
    tracks. Two stat effects on the same condition_group."""
    cond = _coerce_skill_condition(
        _skill(
            1,
            "rotation==1",
            (1, 600000),  # Speed
            (3, 600000),  # Power
        )
    )
    assert cond.buff_speed == 60
    assert cond.buff_power == 60


# ─── degenerate inputs ───────────────────────────────────────────


def test_no_condition_groups_returns_none() -> None:
    """Skills with an empty / missing condition_groups list don't
    get a catalog row — there's nothing to seed."""
    assert _coerce_skill_condition({"id": 1, "name_en": "X"}) is None
    assert (
        _coerce_skill_condition(
            {"id": 1, "name_en": "X", "condition_groups": []}
        )
        is None
    )


def test_missing_id_returns_none() -> None:
    assert _coerce_skill_condition({"condition_groups": [{}]}) is None


def test_multiple_condition_groups_marks_dynamic() -> None:
    """If a skill has two condition_groups (an OR across whole
    alternative predicate sets), schema can't express the union;
    fall back to dynamic."""
    row = {
        "id": 1,
        "name_en": "X",
        "condition_groups": [
            {"condition": "rotation==1", "effects": []},
            {"condition": "ground_type==1", "effects": []},
        ],
    }
    cond = _coerce_skill_condition(row)
    assert cond is not None
    assert cond.is_dynamic is True


# ─── notes ───────────────────────────────────────────────────────


def test_notes_carries_endesc_when_present() -> None:
    """The catalog row's `notes` column captures upstream's curated
    English description so editors can see what they're looking at
    in an admin UI later."""
    row = {
        "id": 200011,
        "name_en": "Right-Handed ◎",
        "endesc": "Your Speed stat is increased by 60 on clockwise tracks",
        "condition_groups": [
            {"condition": "rotation==1", "effects": [{"type": 1, "value": 600000}]}
        ],
    }
    cond = _coerce_skill_condition(row)
    assert cond is not None
    assert cond.notes is not None
    assert "Speed" in cond.notes
    assert "clockwise" in cond.notes


def test_to_seed_row_omits_default_values() -> None:
    """The JSON snapshot stays compact by omitting null predicates
    + zero buffs. The seeder fills defaults when reading the row."""
    cond = FetchedSkillCondition(
        gametora_id=200011,
        direction="Right",
        buff_speed=60,
    )
    seed = cond.to_seed_row()
    assert seed == {
        "gametora_id": 200011,
        "is_dynamic": False,
        "direction": "Right",
        "buff_speed": 60,
    }
