"""PR-SK1 — skill catalog scaffold.

Covers the model's storage shape + the predicate / buff helpers
that items 5 and 6 will consume. The catalog table itself is
empty in prod after this PR — these tests exercise the helpers
against synthetic catalog rows.
"""

from __future__ import annotations

from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import SkillCondition, UmaSkill
from uma_ladder.services.skill_catalog import (
    Buff,
    RaceContext,
    buff_for,
    condition_matches,
    conditions_for,
)


def _seed_skill(app: Flask, name: str, **condition_fields) -> int:
    """Insert a UmaSkill + paired SkillCondition. Returns skill_id."""
    with app.app_context():
        skill = UmaSkill(
            gametora_id=hash(name) & 0xFFFFFFFF,
            name_en=name,
            is_unique=False,
            is_inherited=False,
            enabled=True,
        )
        db.session.add(skill)
        db.session.commit()
        cond = SkillCondition(skill_id=skill.id, **condition_fields)
        db.session.add(cond)
        db.session.commit()
        return skill.id


# ─── condition_matches: predicate semantics ──────────────────────


def test_null_predicate_matches_any_context(app: Flask) -> None:
    """A SkillCondition row with all-NULL predicates is a skill
    that applies in every race — like a passive uma boost. Item 5
    should never gray it out; item 6 always applies its buff."""
    skill_id = _seed_skill(app, "Universal Boost")
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert condition_matches(cond, RaceContext()) is True
        assert condition_matches(
            cond,
            RaceContext(direction="Right", surface="Turf", weather="Sunny"),
        ) is True


def test_predicate_matches_when_context_equals(app: Flask) -> None:
    skill_id = _seed_skill(app, "Right-Handed Test", direction="Right")
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert condition_matches(cond, RaceContext(direction="Right")) is True
        assert condition_matches(cond, RaceContext(direction="Left")) is False


def test_predicate_does_not_match_when_context_missing(app: Flask) -> None:
    """A skill that REQUIRES (direction=Right) can't confirm a match
    when the race's direction is unknown. Default-conservative: do
    NOT match. Otherwise item 6 would apply buffs for races whose
    direction we forgot to populate."""
    skill_id = _seed_skill(app, "Picky Test", direction="Right")
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert condition_matches(cond, RaceContext(direction=None)) is False


def test_multiple_predicates_all_must_match(app: Flask) -> None:
    """A skill that constrains BOTH direction and surface needs
    both to match — partial matches don't count."""
    skill_id = _seed_skill(
        app,
        "Turf Right",
        direction="Right",
        surface="Turf",
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert condition_matches(
            cond, RaceContext(direction="Right", surface="Turf")
        ) is True
        # Direction matches but surface doesn't.
        assert condition_matches(
            cond, RaceContext(direction="Right", surface="Dirt")
        ) is False
        # Surface matches but direction doesn't.
        assert condition_matches(
            cond, RaceContext(direction="Left", surface="Turf")
        ) is False


def test_is_dynamic_skills_match_when_statics_align(app: Flask) -> None:
    """PR-SK5 — dynamic-condition skills CAN match condition_matches
    when their static predicates line up. Example: Speed Star ◎
    requires strategy=Pace (static) AND a runtime position trigger
    (dynamic). On a Pace Chaser uma, the static check passes —
    the skill might fire during the race, so item 5 doesn't gray
    it out. Item 6 still won't apply the buff (uses the stricter
    `applies_passively` check), but display-time visibility is
    permissive."""
    skill_id = _seed_skill(
        app,
        "Speed Star ◎ Test",
        strategy="Pace",
        is_dynamic=True,
        buff_speed=60,
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        # Pace strategy → static matches → could fire → not grayed.
        assert condition_matches(cond, RaceContext(strategy="Pace")) is True
        # Late strategy → static fails → definitely can't fire → grayed.
        assert condition_matches(cond, RaceContext(strategy="Late")) is False


def test_is_dynamic_skill_with_no_static_predicates_matches(
    app: Flask,
) -> None:
    """Pure-runtime skills (e.g. an ultimate with only race-state
    triggers like 'first 1/3 of the race') have no static
    predicates to fail. They're "could fire" against any race
    context — display-time visibility is permissive."""
    skill_id = _seed_skill(
        app, "Pure Runtime Skill", is_dynamic=True, buff_speed=100,
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert condition_matches(cond, RaceContext()) is True
        assert condition_matches(
            cond,
            RaceContext(strategy="Front", direction="Right", surface="Turf"),
        ) is True


def test_applies_passively_stricter_than_condition_matches(
    app: Flask,
) -> None:
    """PR-SK5 — item 6 uses `applies_passively` to decide whether
    to apply a green skill's buff to the displayed stat. Stricter:
    requires statics to match AND `is_dynamic=False`. A Pace
    Chaser running Speed Star ◎ on a Pace race: condition_matches
    says yes (could fire), applies_passively says no (we can't
    guarantee, runtime trigger remains)."""
    from uma_ladder.services.skill_catalog import applies_passively

    dyn_id = _seed_skill(
        app, "Conditional Buff", strategy="Pace", is_dynamic=True,
    )
    static_id = _seed_skill(
        app, "Passive Buff", strategy="Pace", is_dynamic=False,
    )
    with app.app_context():
        dyn = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == dyn_id
            )
        ).one()
        static = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == static_id
            )
        ).one()
        ctx = RaceContext(strategy="Pace")
        # Both pass the "could fire" gate.
        assert condition_matches(dyn, ctx) is True
        assert condition_matches(static, ctx) is True
        # Only the static one passes the stricter "definitely fires" gate.
        assert applies_passively(dyn, ctx) is False
        assert applies_passively(static, ctx) is True


# ─── is_standard_distance ────────────────────────────────────────


def test_passive_buffs_sums_matching_static_skills(app: Flask) -> None:
    """PR-SK10 — `passive_buffs_by_result` sums the buffs of
    every skill on a result whose catalog row says
    is_dynamic=False AND static predicates align. Used by item 6
    to bake green-skill flat buffs into the displayed stat."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        Buff,
        passive_buffs_by_result,
    )

    rh_id = _seed_skill(
        app, "Right-Handed ◎ Buff Test",
        direction="Right", buff_speed=60,
    )
    style_id = _seed_skill(
        app, "Front Runner Savvy Test",
        strategy="Front", buff_wisdom=60,
    )
    result = SimpleNamespace(
        id=1, strategy="Front", gate=None,
        skills=[
            SimpleNamespace(skill_id=rh_id),
            SimpleNamespace(skill_id=style_id),
        ],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction="Right", surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=None,
    )

    with app.app_context():
        out = passive_buffs_by_result([result], race)
    assert out[1] == Buff(speed=60, wisdom=60)


def test_passive_buffs_skips_dynamic_skills(app: Flask) -> None:
    """Dynamic skills don't contribute their buff because we
    can't guarantee they fire — same logic as `applies_passively`
    but with the holder/bracket bounds layered on top."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        Buff,
        passive_buffs_by_result,
    )

    dyn_id = _seed_skill(
        app, "Speed Star Buff Test",
        strategy="Pace", is_dynamic=True, buff_speed=60,
    )
    result = SimpleNamespace(
        id=1, strategy="Pace", gate=None,
        skills=[SimpleNamespace(skill_id=dyn_id)],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=None,
    )

    with app.app_context():
        out = passive_buffs_by_result([result], race)
    assert out[1] == Buff()  # Empty — dynamic excluded.


def test_passive_buffs_respects_holder_count_bounds(app: Flask) -> None:
    """Sympathy (min_holders=5): with only 1 holder, buff is NOT
    applied. Lone Wolf (max_holders=1): with 2 holders, buff is
    NOT applied. Same gates as the gray-out check."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        Buff,
        passive_buffs_by_result,
    )

    sympathy_id = _seed_skill(
        app, "Sympathy Buff Test",
        min_holders=5, buff_speed=40,
    )
    wolf_id = _seed_skill(
        app, "Lone Wolf Buff Test",
        min_holders=1, max_holders=1, buff_speed=40,
    )

    # Build a 2-uma race where both have both skills.
    skills = [
        SimpleNamespace(skill_id=sympathy_id),
        SimpleNamespace(skill_id=wolf_id),
    ]
    r1 = SimpleNamespace(id=1, strategy=None, gate=None, skills=skills)
    r2 = SimpleNamespace(id=2, strategy=None, gate=None, skills=skills)
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=None,
    )

    with app.app_context():
        out = passive_buffs_by_result([r1, r2], race)
    # Sympathy needs 5+ holders (only 2 → no buff).
    # Lone Wolf needs ≤1 holder (2 holders → no buff).
    # Neither skill fires → both umas get empty buff.
    assert out[1] == Buff()
    assert out[2] == Buff()


def test_passive_buffs_skips_bracket_skills_without_data(app: Flask) -> None:
    """Inner / Outer Post buffs need a computable bracket. When
    gate or participant_count is missing, the buff is NOT applied
    — opposite default from the gray-out check (which stays
    permissive on missing data). Don't lie about a buff we can't
    confirm."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        Buff,
        passive_buffs_by_result,
    )

    inner_id = _seed_skill(
        app, "Inner Post Buff Test",
        max_post_number=3, buff_wisdom=60,
    )
    # Uma has the skill but gate is None.
    result = SimpleNamespace(
        id=1, strategy=None, gate=None,
        skills=[SimpleNamespace(skill_id=inner_id)],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=12,
    )

    with app.app_context():
        out = passive_buffs_by_result([result], race)
    assert out[1] == Buff()


def test_passive_buffs_applies_bracket_skill_in_range(app: Flask) -> None:
    """Same test inverted: with gate=1 + participant_count=12,
    bracket is 1 (inner). Inner Post buff fires → +60 Wisdom."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        Buff,
        passive_buffs_by_result,
    )

    inner_id = _seed_skill(
        app, "Inner Post Buff Apply Test",
        max_post_number=3, buff_wisdom=60,
    )
    result = SimpleNamespace(
        id=1, strategy=None, gate=1,
        skills=[SimpleNamespace(skill_id=inner_id)],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=12,
    )

    with app.app_context():
        out = passive_buffs_by_result([result], race)
    assert out[1] == Buff(wisdom=60)


def test_gate_bracket_one_to_one_at_eight_participants() -> None:
    """PR-SK9 — with exactly 8 participants, gate G maps to
    bracket G. Baseline of the bracket algorithm."""
    from uma_ladder.services.skill_catalog import gate_bracket

    for g in range(1, 9):
        assert gate_bracket(g, 8) == g, f"gate {g} in 8-uma race"


def test_gate_bracket_extras_fill_from_bracket_8_backward() -> None:
    """PR-SK9 — community wiki rule: with > 8 participants,
    extras land in the LAST brackets first. The algorithm: first
    (8 - N % 8) brackets get N // 8 gates each (filled from
    gate 1 going up); last (N % 8) brackets each get one
    additional gate (so N // 8 + 1 apiece)."""
    from uma_ladder.services.skill_catalog import gate_bracket

    # N=9 → brackets 1..7 hold one gate each (1..7); bracket 8
    # holds gates 8 and 9.
    assert gate_bracket(7, 9) == 7
    assert gate_bracket(8, 9) == 8
    assert gate_bracket(9, 9) == 8

    # N=10 → brackets 1..6 hold one gate each (1..6); brackets
    # 7 and 8 hold two gates each (7-8 and 9-10).
    assert gate_bracket(6, 10) == 6
    assert gate_bracket(7, 10) == 7
    assert gate_bracket(8, 10) == 7
    assert gate_bracket(9, 10) == 8
    assert gate_bracket(10, 10) == 8

    # N=16 → 2 gates per bracket all the way.
    for g in range(1, 17):
        assert gate_bracket(g, 16) == ((g - 1) // 2) + 1, f"gate {g}"

    # N=18 → first 6 brackets get 2 gates, last 2 get 3.
    # gates 1..12 in brackets 1..6 (2 each), 13..15 in bracket 7,
    # 16..18 in bracket 8.
    assert gate_bracket(12, 18) == 6
    assert gate_bracket(13, 18) == 7
    assert gate_bracket(15, 18) == 7
    assert gate_bracket(16, 18) == 8
    assert gate_bracket(18, 18) == 8


def test_gate_bracket_returns_none_for_missing_or_invalid() -> None:
    """When gate or participant_count is missing / out-of-range,
    the bracket is None — callers default to permissive (don't
    gray a post-number-conditional skill)."""
    from uma_ladder.services.skill_catalog import gate_bracket

    assert gate_bracket(None, 12) is None
    assert gate_bracket(5, None) is None
    assert gate_bracket(0, 12) is None
    assert gate_bracket(13, 12) is None  # gate exceeds participants
    assert gate_bracket(5, 0) is None
    assert gate_bracket(5, 19) is None   # too many participants


def test_inapplicable_inner_post_grays_on_outer_brackets(app: Flask) -> None:
    """PR-SK9 — Inner Post Proficiency (`max_post_number=3`)
    grays on bracket 4 or higher. Driven against a 12-uma race
    where gate 1 maps to bracket 1 (inner, stays bright) and gate
    11 maps to bracket 7 (outer, grays)."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    inner_id = _seed_skill(
        app, "Inner Post Proficiency Test",
        max_post_number=3, buff_wisdom=60,
    )

    # 12-uma race: brackets 1-4 hold 1 gate each (1..4), brackets
    # 5-8 hold 2 each (5-6, 7-8, 9-10, 11-12).
    inner_uma = SimpleNamespace(
        id=1, strategy=None, gate=1,
        skills=[SimpleNamespace(skill_id=inner_id)],
    )
    outer_uma = SimpleNamespace(
        id=2, strategy=None, gate=11,
        skills=[SimpleNamespace(skill_id=inner_id)],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=12,
    )

    with app.app_context():
        out = inapplicable_skill_ids_by_result([inner_uma, outer_uma], race)
    # Inner uma (bracket 1) keeps the skill bright.
    assert inner_id not in out[1]
    # Outer uma (bracket 7) grays — 7 > max_post_number=3.
    assert inner_id in out[2]


def test_inapplicable_post_skill_stays_bright_when_data_missing(
    app: Flask,
) -> None:
    """PR-SK9 — if `result.gate` is None (OCR didn't catch it) or
    `race.participant_count` is None (organizer didn't fill it
    in), the bracket can't be computed and post-number skills
    stay bright. Defensive default per the user's requirement
    ('If race wasn't parsed correctly, inner post/outer post
    must be disabled by default')."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    inner_id = _seed_skill(
        app, "Inner Post No-Data Test",
        max_post_number=3, buff_wisdom=60,
    )
    # Scenario A: result has no gate.
    no_gate = SimpleNamespace(
        id=1, strategy=None, gate=None,
        skills=[SimpleNamespace(skill_id=inner_id)],
    )
    race_full = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=12,
    )
    with app.app_context():
        out = inapplicable_skill_ids_by_result([no_gate], race_full)
    assert inner_id not in out[1]

    # Scenario B: race has no participant_count.
    has_gate = SimpleNamespace(
        id=2, strategy=None, gate=15,  # would be outer in 18-uma race
        skills=[SimpleNamespace(skill_id=inner_id)],
    )
    race_no_pc = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
        participant_count=None,
    )
    with app.app_context():
        out = inapplicable_skill_ids_by_result([has_gate], race_no_pc)
    assert inner_id not in out[2]


def test_ground_condition_exclude_grays_on_excluded_value(app: Flask) -> None:
    """PR-SK8 — `ground_condition_exclude="Firm"` is the Wet
    Conditions shape: skill applies UNLESS race ground is Firm.
    Verified against the four enum values."""
    skill_id = _seed_skill(
        app,
        "Wet Conditions ◎ Test",
        ground_condition_exclude="Firm",
        buff_power=60,
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        # Firm → excluded → doesn't apply.
        assert condition_matches(
            cond, RaceContext(ground_condition="Firm")
        ) is False
        # Anything else → applies.
        for non_firm in ("Good", "Soft", "Heavy"):
            assert condition_matches(
                cond, RaceContext(ground_condition=non_firm)
            ) is True, f"falsely grayed on {non_firm}"
        # Context unknown → can't confirm exclusion, default
        # permissive (don't gray the chip when data's missing).
        assert condition_matches(
            cond, RaceContext(ground_condition=None)
        ) is True


def test_inapplicable_sympathy_grays_below_threshold(app: Flask) -> None:
    """PR-SK8 — Sympathy needs ≥5 holders. With only 3 results
    having it, all 3 chips gray. Add a 4th, still gray. Add a 5th,
    all 5 light up."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    sympathy_id = _seed_skill(
        app, "Sympathy Test", min_holders=5, buff_speed=40,
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
    )

    def _build(n: int) -> list:
        return [
            SimpleNamespace(
                id=i, strategy=None,
                skills=[SimpleNamespace(skill_id=sympathy_id)],
            )
            for i in range(n)
        ]

    with app.app_context():
        for n in (1, 2, 3, 4):
            out = inapplicable_skill_ids_by_result(_build(n), race)
            # Every result should have Sympathy in its inapplicable
            # set (count < 5).
            assert all(sympathy_id in v for v in out.values()), \
                f"sympathy should gray at count={n}"
        # At 5 holders, threshold met → no one grays.
        out = inapplicable_skill_ids_by_result(_build(5), race)
        assert all(sympathy_id not in v for v in out.values())


def test_inapplicable_lone_wolf_grays_when_company_arrives(
    app: Flask,
) -> None:
    """PR-SK8 — Lone Wolf needs exactly 1 holder (just the uma
    itself). With one uma having it, no gray. The moment a second
    uma also has it, both gray."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    wolf_id = _seed_skill(
        app, "Lone Wolf Test",
        min_holders=1, max_holders=1, buff_speed=40,
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None, ground_condition=None,
    )

    def _build(n: int) -> list:
        return [
            SimpleNamespace(
                id=i, strategy=None,
                skills=[SimpleNamespace(skill_id=wolf_id)],
            )
            for i in range(n)
        ]

    with app.app_context():
        # One holder → applies (no gray).
        out = inapplicable_skill_ids_by_result(_build(1), race)
        assert wolf_id not in out[0]
        # Two holders → both gray (count=2 > max_holders=1).
        out = inapplicable_skill_ids_by_result(_build(2), race)
        assert wolf_id in out[0]
        assert wolf_id in out[1]


def test_ground_condition_predicate_matches(app: Flask) -> None:
    """PR-SK7 — a SkillCondition with ground_condition="Firm"
    matches only on a Firm track. Firm Conditions / Firm Course
    Menace go gray on Good/Soft/Heavy via this predicate."""
    skill_id = _seed_skill(
        app,
        "Firm Conditions ◎ Test",
        ground_condition="Firm",
        buff_power=60,
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert condition_matches(
            cond, RaceContext(ground_condition="Firm")
        ) is True
        for non_firm in ("Good", "Soft", "Heavy"):
            assert condition_matches(
                cond, RaceContext(ground_condition=non_firm)
            ) is False, f"matched on {non_firm}"
        # Context unknown (race didn't record a ground condition)
        # → don't pretend the predicate matches.
        assert condition_matches(
            cond, RaceContext(ground_condition=None)
        ) is False


def test_is_standard_distance_true_predicate(app: Flask) -> None:
    """`is_standard_distance=True` matches races at 1600/2000/2400/3200m
    and nothing else."""
    skill_id = _seed_skill(
        app, "Standard Test", is_standard_distance=True
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        for d in (1600, 2000, 2400, 3200):
            assert condition_matches(
                cond, RaceContext(distance_meters=d)
            ) is True, f"failed at {d}"
        for d in (1200, 1800, 2500):
            assert condition_matches(
                cond, RaceContext(distance_meters=d)
            ) is False, f"falsely matched at {d}"


def test_is_standard_distance_false_predicate(app: Flask) -> None:
    """`is_standard_distance=False` mirrors the 'Non-Standard
    Distance' skill — matches everything EXCEPT the four canonical
    lengths."""
    skill_id = _seed_skill(
        app, "Non-Standard Test", is_standard_distance=False
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert condition_matches(
            cond, RaceContext(distance_meters=2200)
        ) is True
        assert condition_matches(
            cond, RaceContext(distance_meters=2000)
        ) is False


# ─── buff_for ────────────────────────────────────────────────────


def test_buff_for_extracts_stat_fields(app: Flask) -> None:
    """A ◎ tier variant of a skill stores positive buff values."""
    skill_id = _seed_skill(
        app,
        "Right-Handed ◎ Test",
        direction="Right",
        buff_speed=60,
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        buff = buff_for(cond)
        assert buff == Buff(speed=60)
        assert buff.any_nonzero is True


def test_buff_for_x_tier_is_negative(app: Flask) -> None:
    """× tier variants are debuffs — stored as negative integers
    so item 6 can sum them naturally with the positive variants."""
    skill_id = _seed_skill(
        app,
        "Right-Handed x Test",
        direction="Right",
        buff_speed=-20,
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert buff_for(cond) == Buff(speed=-20)


def test_buff_sums_across_skills() -> None:
    """Item 6 sums buffs across all of an uma's applicable skills.
    Pinned here as a documented algebra — `Buff.__add__` is the
    intended sum operator."""
    total = Buff(speed=60) + Buff(stamina=20, power=10) + Buff(speed=-20)
    assert total == Buff(speed=40, stamina=20, power=10)


def test_buff_no_nonzero_is_falsy_for_grayout_only() -> None:
    """Some catalog entries are 'gray-out only' — they constrain
    when the skill applies but don't grant any stat buff. Item 5
    consumes those (gray out the row); item 6 skips them via
    `any_nonzero` check."""
    # Synthesise the Buff directly — the model defaults all fields to 0.
    empty = Buff()
    assert empty.any_nonzero is False


# ─── conditions_for batch query ──────────────────────────────────


def test_conditions_for_returns_keyed_by_skill_id(app: Flask) -> None:
    """The query helper batch-fetches catalog rows for an uma's
    skill list and keys the result by skill_id so the caller can
    do `conditions[s].direction == ...` cheaply."""
    s1 = _seed_skill(app, "S1", direction="Right")
    s2 = _seed_skill(app, "S2", surface="Turf")
    with app.app_context():
        # Mix in a third skill_id that has no catalog entry.
        s3 = 9999
        result = conditions_for([s1, s2, s3])
        assert s1 in result
        assert s2 in result
        assert s3 not in result
        assert result[s1].direction == "Right"
        assert result[s2].surface == "Turf"


def test_conditions_for_empty_list_returns_empty_dict() -> None:
    assert conditions_for([]) == {}


# ─── RaceContext.is_standard_distance ────────────────────────────


def test_race_context_is_standard_distance_property() -> None:
    """The property is a computed view over distance_meters — None
    when unknown, True for the four canonical lengths, False
    otherwise."""
    assert RaceContext(distance_meters=None).is_standard_distance is None
    assert RaceContext(distance_meters=2000).is_standard_distance is True
    assert RaceContext(distance_meters=1600).is_standard_distance is True
    assert RaceContext(distance_meters=2200).is_standard_distance is False


# ─── Model defaults pinned ───────────────────────────────────────


def test_seed_skill_conditions_round_trips_via_json(app: Flask, tmp_path) -> None:
    """End-to-end: write a synthetic snapshot, run the seeder,
    assert the SkillCondition rows are present + correct + idempotent
    on re-run."""
    import json

    from uma_ladder.services.seed_skill_conditions import seed_skill_conditions

    with app.app_context():
        # Seed a couple of UmaSkills so the seeder has targets to bind to.
        s1 = UmaSkill(
            gametora_id=200011, name_en="Right-Handed Test ◎",
            is_unique=False, is_inherited=False, enabled=True,
        )
        s2 = UmaSkill(
            gametora_id=200012, name_en="Right-Handed Test ○",
            is_unique=False, is_inherited=False, enabled=True,
        )
        # Third gametora_id deliberately missing — seeder should
        # skip rows whose UmaSkill doesn't exist (yet) rather than
        # 500 on FK failure.
        db.session.add_all([s1, s2])
        db.session.commit()

    snapshot = tmp_path / "skill_conditions.json"
    snapshot.write_text(json.dumps({
        "source": "test",
        "conditions": [
            {
                "gametora_id": 200011,
                "is_dynamic": False,
                "direction": "Right",
                "buff_speed": 60,
            },
            {
                "gametora_id": 200012,
                "is_dynamic": False,
                "direction": "Right",
                "buff_speed": 40,
            },
            {
                "gametora_id": 999999,  # no matching UmaSkill
                "is_dynamic": False,
                "direction": "Left",
            },
        ],
    }))

    with app.app_context():
        report = seed_skill_conditions(snapshot)
        assert report.inserted == 2
        assert report.updated == 0
        assert report.skipped_no_skill == 1

        # Pin the data.
        rows = db.session.scalars(db.select(SkillCondition)).all()
        rows.sort(key=lambda r: r.skill_id)
        # Use a lookup since other tests in this module create rows.
        by_gid = {
            db.session.get(UmaSkill, r.skill_id).gametora_id: r
            for r in rows
        }
        assert by_gid[200011].direction == "Right"
        assert by_gid[200011].buff_speed == 60
        assert by_gid[200012].buff_speed == 40

        # Idempotency: re-running with the same snapshot is a no-op.
        report2 = seed_skill_conditions(snapshot)
        assert report2.inserted == 0
        assert report2.updated == 0
        assert report2.skipped_no_skill == 1


def test_seed_skill_conditions_updates_changed_fields(
    app: Flask, tmp_path
) -> None:
    """When the upstream snapshot changes (e.g. a balance patch
    adjusts the buff amount), the seeder updates the row in place
    rather than inserting a duplicate."""
    import json

    from uma_ladder.services.seed_skill_conditions import seed_skill_conditions

    with app.app_context():
        s = UmaSkill(
            gametora_id=200011, name_en="Right-Handed Patch Test",
            is_unique=False, is_inherited=False, enabled=True,
        )
        db.session.add(s)
        db.session.commit()

    snapshot = tmp_path / "v1.json"
    snapshot.write_text(json.dumps({
        "conditions": [{
            "gametora_id": 200011, "direction": "Right", "buff_speed": 60,
        }],
    }))
    with app.app_context():
        seed_skill_conditions(snapshot)

    # Re-write the snapshot with a buff change.
    snapshot.write_text(json.dumps({
        "conditions": [{
            "gametora_id": 200011, "direction": "Right", "buff_speed": 80,
        }],
    }))
    with app.app_context():
        report = seed_skill_conditions(snapshot)
        assert report.inserted == 0
        assert report.updated == 1
        cond = db.session.scalars(
            db.select(SkillCondition).join(
                UmaSkill, UmaSkill.id == SkillCondition.skill_id
            ).where(UmaSkill.gametora_id == 200011)
        ).one()
        assert cond.buff_speed == 80


def test_seed_writes_app_setting_with_refresh_timestamp(
    app: Flask, tmp_path
) -> None:
    """PR-SK3 — every successful seed stamps the
    `skill_conditions_refreshed_at` AppSetting so the admin
    dashboard can show "last refreshed N hours ago" without
    inspecting file mtime."""
    import json
    from datetime import UTC, datetime

    from uma_ladder.models import AppSetting
    from uma_ladder.services.seed_skill_conditions import (
        LAST_REFRESHED_SETTING_KEY,
        seed_skill_conditions,
    )

    snapshot = tmp_path / "conditions.json"
    snapshot.write_text(json.dumps({"conditions": []}))

    before = datetime.now(UTC)
    with app.app_context():
        seed_skill_conditions(snapshot)
        setting = db.session.get(AppSetting, LAST_REFRESHED_SETTING_KEY)
        assert setting is not None
        parsed = datetime.fromisoformat(setting.value)
        assert parsed >= before


def test_seed_updates_existing_refresh_timestamp(
    app: Flask, tmp_path
) -> None:
    """Re-seeding overwrites the stamp rather than inserting a
    second row — AppSetting is keyed by `key`, one row per key."""
    import json
    import time

    from uma_ladder.models import AppSetting
    from uma_ladder.services.seed_skill_conditions import (
        LAST_REFRESHED_SETTING_KEY,
        seed_skill_conditions,
    )

    snapshot = tmp_path / "conditions.json"
    snapshot.write_text(json.dumps({"conditions": []}))

    with app.app_context():
        seed_skill_conditions(snapshot)
        first = db.session.get(AppSetting, LAST_REFRESHED_SETTING_KEY).value

    time.sleep(0.01)

    with app.app_context():
        seed_skill_conditions(snapshot)
        rows = db.session.scalars(db.select(AppSetting).where(
            AppSetting.key == LAST_REFRESHED_SETTING_KEY
        )).all()
        assert len(rows) == 1
        assert rows[0].value > first


def test_race_context_for_pulls_fields_from_race_and_result() -> None:
    """PR-SK4 — `race_context_for` reads surface/direction/etc.
    off `race.preset` and weather/season off `race`, plus
    strategy off the result if given. Duck-typed so the test
    can synthesise objects without touching the DB."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import race_context_for

    preset = SimpleNamespace(
        direction="Right", surface="Turf",
        distance_category="Medium", distance_meters=2400,
        venue="Tokyo",
    )
    race = SimpleNamespace(
        preset=preset, weather="Sunny", race_season="Spring",
    )
    result = SimpleNamespace(strategy="End")

    ctx = race_context_for(race, result)
    assert ctx.direction == "Right"
    assert ctx.surface == "Turf"
    assert ctx.weather == "Sunny"
    assert ctx.season == "Spring"
    assert ctx.distance_category == "Medium"
    assert ctx.distance_meters == 2400
    assert ctx.strategy == "End"
    assert ctx.venue == "Tokyo"
    assert ctx.is_standard_distance is True  # 2400 ∈ {1600,2000,2400,3200}


def test_race_context_for_strategy_none_without_result() -> None:
    """Without a result, strategy is None and any
    style-conditional skill registers as non-applying."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import race_context_for

    race = SimpleNamespace(preset=SimpleNamespace(direction="Right"),
                           weather=None, race_season=None)
    ctx = race_context_for(race, None)
    assert ctx.direction == "Right"
    assert ctx.strategy is None


def test_inapplicable_skill_ids_batched_per_result(app: Flask) -> None:
    """PR-SK4 — `inapplicable_skill_ids_by_result` returns a
    per-result-id mapping of skill ids whose catalog predicate
    doesn't match the race context. Strategy axis is per-result
    so the same skill can apply to one uma and gray out for
    another in the same race.

    Skill setup:
      - "Right-Handed" (direction=Right, no other constraint)
      - "Front Runner Savvy" (strategy=Front)
      - Both seeded as catalog rows.

    Race: Right direction. Two results: Alice (strategy=Front),
    Bob (strategy=End). Alice has both skills; Bob has both.

    Expected:
      - Alice: both skills apply (direction matches, strategy=Front
        matches) → empty inapplicable set.
      - Bob: Right-Handed still applies (direction matches),
        Front Runner Savvy does NOT (strategy=End != Front) →
        {front_runner_id}.
    """
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    right_handed_id = _seed_skill(app, "Right-Handed RB", direction="Right")
    front_runner_id = _seed_skill(app, "Front Runner Savvy RB", strategy="Front")

    # Fake the joined associations the route normally builds via
    # `r.skills`. Each assoc has `skill_id`. result.id is used as
    # the dict key; result.strategy drives the per-result context.
    alice_skills = [
        SimpleNamespace(skill_id=right_handed_id),
        SimpleNamespace(skill_id=front_runner_id),
    ]
    bob_skills = [
        SimpleNamespace(skill_id=right_handed_id),
        SimpleNamespace(skill_id=front_runner_id),
    ]
    alice = SimpleNamespace(id=1, strategy="Front", skills=alice_skills)
    bob = SimpleNamespace(id=2, strategy="End", skills=bob_skills)
    race = SimpleNamespace(
        preset=SimpleNamespace(direction="Right", surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None,
    )

    with app.app_context():
        result = inapplicable_skill_ids_by_result([alice, bob], race)

    assert result[alice.id] == set()
    assert result[bob.id] == {front_runner_id}


def test_inapplicable_skill_unknown_in_catalog_is_permissive(
    app: Flask,
) -> None:
    """A skill the uma has but whose catalog entry doesn't exist
    yet (e.g. brand-new from a gametora refresh that hasn't been
    seeded) is treated as "applies" — we don't gray out what we
    don't have data for."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    with app.app_context():
        # Seed a UmaSkill with NO matching SkillCondition.
        skill = UmaSkill(
            gametora_id=987654, name_en="Brand New Skill",
            is_unique=False, is_inherited=False, enabled=True,
        )
        db.session.add(skill)
        db.session.commit()
        sid = skill.id

    result = SimpleNamespace(
        id=1, strategy="Front",
        skills=[SimpleNamespace(skill_id=sid)],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction="Right", surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None,
    )

    with app.app_context():
        out = inapplicable_skill_ids_by_result([result], race)
    assert out[result.id] == set()


def test_inapplicable_dynamic_skill_stays_bright_when_statics_match(
    app: Flask,
) -> None:
    """PR-SK5 — a dynamic skill whose static predicates match the
    race context is NOT inapplicable. Example: a skill with
    direction=Right + a runtime corner trigger, on a Right race.
    The runtime trigger might or might not fire during the race
    — display-side we can't tell — but the skill's not
    *definitely* off-condition, so we don't gray it."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    dyn_id = _seed_skill(
        app, "Dynamic But Static Matches",
        direction="Right", is_dynamic=True,
    )
    result = SimpleNamespace(
        id=1, strategy="Front",
        skills=[SimpleNamespace(skill_id=dyn_id)],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction="Right", surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None,
    )

    with app.app_context():
        out = inapplicable_skill_ids_by_result([result], race)
    assert out[result.id] == set()


def test_inapplicable_dynamic_skill_grays_when_static_fails(
    app: Flask,
) -> None:
    """The other side of PR-SK5: when a dynamic skill ALSO has a
    static predicate that doesn't match the race context, it IS
    inapplicable. Example: Speed Star ◎ (strategy=Pace + runtime
    position) on a Late Surger uma — static fails regardless of
    what the race phase does, so the skill definitely can't fire."""
    from types import SimpleNamespace

    from uma_ladder.services.skill_catalog import (
        inapplicable_skill_ids_by_result,
    )

    speed_star_id = _seed_skill(
        app, "Speed Star ◎ Late Test",
        strategy="Pace", is_dynamic=True, buff_speed=60,
    )
    result = SimpleNamespace(
        id=1, strategy="Late",  # uma running Late, not Pace
        skills=[SimpleNamespace(skill_id=speed_star_id)],
    )
    race = SimpleNamespace(
        preset=SimpleNamespace(direction=None, surface=None,
                               distance_category=None, distance_meters=None,
                               venue=None),
        weather=None, race_season=None,
    )

    with app.app_context():
        out = inapplicable_skill_ids_by_result([result], race)
    assert out[result.id] == {speed_star_id}


def test_model_buff_columns_default_to_zero(app: Flask) -> None:
    """A SkillCondition row inserted with only the predicate fields
    set must come back with all five buff columns at 0 — items 5/6
    rely on this to render 'gray-out only' rows cleanly."""
    skill_id = _seed_skill(app, "Defaults Test", direction="Right")
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        assert (cond.buff_speed, cond.buff_stamina, cond.buff_power,
                cond.buff_guts, cond.buff_wisdom) == (0, 0, 0, 0, 0)
        assert cond.is_dynamic is False
