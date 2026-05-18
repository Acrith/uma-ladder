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


def test_is_dynamic_never_matches(app: Flask) -> None:
    """Dynamic-condition skills (runtime-only triggers like 'when
    1L behind') don't apply for display purposes regardless of
    race context — items 5/6 conservatively treat them as
    non-applying."""
    skill_id = _seed_skill(
        app,
        "Dynamic Skill",
        direction="Right",
        is_dynamic=True,
    )
    with app.app_context():
        cond = db.session.scalars(
            db.select(SkillCondition).where(
                SkillCondition.skill_id == skill_id
            )
        ).one()
        # Direction matches the predicate but is_dynamic still wins.
        assert condition_matches(cond, RaceContext(direction="Right")) is False


# ─── is_standard_distance ────────────────────────────────────────


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
