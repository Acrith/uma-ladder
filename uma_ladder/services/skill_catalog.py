"""PR-SK1 — skill catalog query helpers.

Consumed by items 5 (gray-out skills that don't apply to a race's
conditions) and 6 (apply green-skill stat buffs to the displayed
result). Both consumers operate over the *uma's actual skills* +
the *race context*; this module exposes the primitives they need
without baking in the display-side decisions.

The catalog itself is `SkillCondition` rows. Each row has nullable
predicate columns (direction, surface, weather, season,
distance_category, strategy, venue, is_standard_distance) and
signed buff columns (buff_speed, ..., buff_wisdom). A predicate
that is NULL means "skill doesn't care about this axis"; for the
skill to apply, every non-NULL predicate must match the race
context.

`is_dynamic` skills don't apply for display purposes — their
trigger is runtime-only. Items 5/6 treat them as "always grayed,
never buff".
"""

from __future__ import annotations

from dataclasses import dataclass

from ..extensions import db
from ..models import SkillCondition

# In-game "standard distances" — the four canonical race lengths
# the "Standard Distance" skill family matches. Anything else is
# "non-standard" for the inverse skill.
_STANDARD_DISTANCES: frozenset[int] = frozenset({1600, 2000, 2400, 3200})


def gate_bracket(gate: int | None, participants: int | None) -> int | None:
    """PR-SK9 — compute the post-number / gate bracket (1..8)
    from a 1-indexed `gate` and the total `participants` count.

    Rule (from the community wiki user supplied): there are
    always 8 brackets. With N participants:

    - First `(8 - N % 8)` brackets get `N // 8` gates each
      (filled left-to-right from gate 1).
    - Last `N % 8` brackets each get one additional gate (so
      `N // 8 + 1` gates apiece).

    Examples:
      N=8  → 1:1 (gate G → bracket G)
      N=9  → gates 1..7 in brackets 1..7, gates 8 and 9 share bracket 8
      N=10 → gates 7-8 in bracket 7, gates 9-10 in bracket 8
      N=16 → every bracket has 2 consecutive gates
      N=18 → first 6 brackets get 2 gates, last 2 get 3

    Returns None when either input is missing or out-of-range
    (gate must be 1..participants, participants must be 1..18).
    Callers default to "skill applies" when this returns None
    so we don't gray-out skills on partially-OCR'd rows."""
    if gate is None or participants is None:
        return None
    if participants < 1 or participants > 18:
        return None
    if gate < 1 or gate > participants:
        return None
    base = participants // 8
    remainder = participants % 8
    # Number of brackets in the "left, smaller" section.
    full_bracket_count = 8 - remainder
    # Last gate that falls into a smaller-sized bracket.
    full_gates = base * full_bracket_count
    if gate <= full_gates:
        # 1-indexed bracket within the smaller section.
        return (gate - 1) // base + 1 if base > 0 else gate
    extra_offset = gate - full_gates - 1
    return full_bracket_count + extra_offset // (base + 1) + 1


@dataclass(frozen=True)
class RaceContext:
    """Static race conditions a SkillCondition's predicate can be
    matched against. None for any field means "context unknown" —
    a predicate that constrains that axis won't match (we can't
    confirm a match when we don't know the value)."""

    direction: str | None = None         # "Left" / "Right"
    surface: str | None = None           # "Turf" / "Dirt"
    weather: str | None = None           # "Sunny" / "Cloudy" / "Rainy" / "Snowy"
    season: str | None = None            # "Spring" / "Summer" / "Autumn" / "Winter"
    distance_category: str | None = None  # "Sprint" / "Mile" / "Medium" / "Long"
    distance_meters: int | None = None
    strategy: str | None = None          # "Front" / "Pace" / "Late" / "End"
    venue: str | None = None             # "Nakayama" / "Tokyo" / ...
    # PR-SK7 — "Firm" / "Good" / "Soft" / "Heavy". Drives the
    # Firm Conditions / Firm Course Menace gray-out.
    ground_condition: str | None = None

    @property
    def is_standard_distance(self) -> bool | None:
        if self.distance_meters is None:
            return None
        return self.distance_meters in _STANDARD_DISTANCES


@dataclass(frozen=True)
class Buff:
    """Per-stat buff a matching SkillCondition contributes. All
    fields default to 0; consumers sum across multiple matching
    skills."""

    speed: int = 0
    stamina: int = 0
    power: int = 0
    guts: int = 0
    wisdom: int = 0

    @property
    def any_nonzero(self) -> bool:
        return any(
            (self.speed, self.stamina, self.power, self.guts, self.wisdom)
        )

    def __add__(self, other: Buff) -> Buff:
        return Buff(
            speed=self.speed + other.speed,
            stamina=self.stamina + other.stamina,
            power=self.power + other.power,
            guts=self.guts + other.guts,
            wisdom=self.wisdom + other.wisdom,
        )


def condition_matches(
    condition: SkillCondition, context: RaceContext
) -> bool:
    """True iff every non-NULL STATIC predicate matches the race
    context. The function answers "could this skill fire?" — not
    "will it definitely fire?". Specifically: `is_dynamic=True`
    skills (those with runtime-only sub-conditions like
    `order_rate<=50` or `straight_random==1`) CAN match, provided
    their static predicates (strategy, surface, etc.) line up.

    Why: a skill like Speed Star ◎ requires `strategy=Pace` AND a
    runtime "near the front of the pack" trigger. On a Late
    Surger uma, Speed Star definitely can't fire (wrong strategy)
    — gray out. On a Pace Chaser uma, Speed Star MIGHT fire (the
    runtime check happens during the race) — don't gray. The
    is_dynamic flag stays in the catalog so consumers like item 6
    (apply green-skill buffs to displayed stats) can distinguish
    "definitely fires" (apply buff) from "might fire" (don't);
    item 5 only cares about "definitely won't fire"."""

    def _matches(predicate, context_value) -> bool:
        # NULL predicate = "skill doesn't care".
        if predicate is None:
            return True
        # Context unknown but predicate set = can't confirm match.
        if context_value is None:
            return False
        return predicate == context_value

    if not _matches(condition.direction, context.direction):
        return False
    if not _matches(condition.surface, context.surface):
        return False
    if not _matches(condition.weather, context.weather):
        return False
    if not _matches(condition.season, context.season):
        return False
    if not _matches(condition.distance_category, context.distance_category):
        return False
    if not _matches(condition.strategy, context.strategy):
        return False
    if not _matches(condition.venue, context.venue):
        return False
    if not _matches(condition.ground_condition, context.ground_condition):
        return False
    if not _matches(
        condition.is_standard_distance, context.is_standard_distance
    ):
        return False
    # PR-SK8 — `ground_condition_exclude` is the inverse of a
    # positive predicate. Skill applies UNLESS context matches.
    # NULL exclude = no constraint; NULL context with non-NULL
    # exclude = can't confirm (skill stays brightened, callers
    # decide if they want stricter behavior).
    if condition.ground_condition_exclude is not None:
        if context.ground_condition is None:
            return True
        if context.ground_condition == condition.ground_condition_exclude:
            return False
    return True


def applies_passively(
    condition: SkillCondition, context: RaceContext
) -> bool:
    """Stricter than `condition_matches`: True only when the skill
    will definitely fire on this race — no runtime conditions
    remaining. Used by item 6 (green-skill buff display) to decide
    whether to add the buff to the shown stat; we only add it
    when we know the skill will passively be active in the race.

    Caller must STILL check holder count + bracket bounds
    separately — those depend on per-race aggregates this
    function doesn't see. See `passive_buffs_by_result` for the
    full "will definitely fire on this result" check."""
    if condition.is_dynamic:
        return False
    return condition_matches(condition, context)


def passive_buffs_by_result(results, race) -> dict[int, Buff]:
    """PR-SK10 — for each result, sum the buffs from every skill
    that DEFINITELY fires passively on this race. Used to
    augment displayed stats (item 6).

    Stricter than the gray-out check (`inapplicable_skill_ids_by_result`):

    - `is_dynamic=True` skills don't contribute (their trigger is
      runtime; we can't guarantee they'll fire so we don't lie
      about a buff to the displayed stat).
    - Holder count and bracket bounds must be inside the
      catalog's [min, max] range. Missing data (no participant
      count → no bracket) is conservative — bracketed skills
      don't contribute when bracket can't be computed.

    Single batched fetch of `SkillCondition` + a single pass over
    holder counts, same as the gray-out helper."""
    if not results:
        return {}
    all_skill_ids: set[int] = set()
    holder_counts: dict[int, int] = {}
    for r in results:
        for assoc in getattr(r, "skills", []) or []:
            sid = getattr(assoc, "skill_id", None)
            if sid is not None:
                all_skill_ids.add(sid)
                holder_counts[sid] = holder_counts.get(sid, 0) + 1
    catalog = conditions_for(list(all_skill_ids))

    participant_count = getattr(race, "participant_count", None)

    out: dict[int, Buff] = {}
    for r in results:
        ctx = race_context_for(race, r)
        bracket = gate_bracket(
            getattr(r, "gate", None), participant_count
        )
        total = Buff()
        for assoc in getattr(r, "skills", []) or []:
            sid = getattr(assoc, "skill_id", None)
            if sid is None:
                continue
            cond = catalog.get(sid)
            if cond is None:
                continue
            # Item 6 only credits skills that WILL definitely fire.
            if cond.is_dynamic:
                continue
            if not condition_matches(cond, ctx):
                continue
            count = holder_counts.get(sid, 0)
            if cond.min_holders is not None and count < cond.min_holders:
                continue
            if cond.max_holders is not None and count > cond.max_holders:
                continue
            if (
                cond.min_post_number is not None
                or cond.max_post_number is not None
            ):
                # Bracket-conditional skill: skip when bracket
                # can't be computed (defensive — don't lie about
                # a buff that depends on data we don't have).
                if bracket is None:
                    continue
                if (
                    cond.min_post_number is not None
                    and bracket < cond.min_post_number
                ):
                    continue
                if (
                    cond.max_post_number is not None
                    and bracket > cond.max_post_number
                ):
                    continue
            total = total + buff_for(cond)
        out[r.id] = total
    return out


def buff_for(condition: SkillCondition) -> Buff:
    """Extract the catalog row's stat-buff fields as a Buff."""
    return Buff(
        speed=condition.buff_speed,
        stamina=condition.buff_stamina,
        power=condition.buff_power,
        guts=condition.buff_guts,
        wisdom=condition.buff_wisdom,
    )


def conditions_for(skill_ids: list[int]) -> dict[int, SkillCondition]:
    """Fetch SkillCondition rows for the given skill ids, keyed by
    skill_id. Missing entries simply absent from the result —
    caller decides what "no catalog entry" means (item 5 likely
    "don't gray out", item 6 "no buff")."""
    if not skill_ids:
        return {}
    rows = db.session.scalars(
        db.select(SkillCondition).where(
            SkillCondition.skill_id.in_(skill_ids)
        )
    ).all()
    return {row.skill_id: row for row in rows}


def race_context_for(race, result=None) -> RaceContext:
    """Build a `RaceContext` from an OfficialRace (+ optional
    OfficialRaceResult). The strategy axis comes from
    `result.strategy` (the strategy the uma ran with); when no
    result is given, that axis is None and any style-conditional
    skill will read as non-applying.

    Defensive `getattr` on each field so callers can pass anything
    duck-typed — the test suite synthesises objects with just the
    handful of attributes the function reads."""
    preset = getattr(race, "preset", None) if race is not None else None
    return RaceContext(
        direction=getattr(preset, "direction", None),
        surface=getattr(preset, "surface", None),
        weather=getattr(race, "weather", None) if race is not None else None,
        season=(
            getattr(race, "race_season", None) if race is not None else None
        ),
        distance_category=getattr(preset, "distance_category", None),
        distance_meters=getattr(preset, "distance_meters", None),
        strategy=(
            getattr(result, "strategy", None) if result is not None else None
        ),
        venue=getattr(preset, "venue", None),
        ground_condition=(
            getattr(race, "ground_condition", None) if race is not None else None
        ),
    )


def inapplicable_skill_ids_by_result(
    results, race
) -> dict[int, set[int]]:
    """For each result in `results`, return the set of skill ids
    on its `r.skills` list whose `SkillCondition` predicate
    doesn't match the race + strategy context. Skills without a
    catalog entry are treated as "applies" (we don't have data
    to gray them out conservatively).

    Single batched fetch of `SkillCondition` across all skills
    on all results — avoids N+1 lookups when the page renders
    a 9-uma race with ~16 skills each.

    PR-SK8 — also evaluates the cross-result holder count for
    Sympathy / Lone Wolf class skills. holder_count[skill_id] is
    the number of results carrying that exact skill_id; we gray
    when count falls outside [min_holders, max_holders]."""
    if not results:
        return {}
    all_skill_ids: set[int] = set()
    holder_counts: dict[int, int] = {}
    for r in results:
        for assoc in getattr(r, "skills", []) or []:
            sid = getattr(assoc, "skill_id", None)
            if sid is not None:
                all_skill_ids.add(sid)
                holder_counts[sid] = holder_counts.get(sid, 0) + 1
    catalog = conditions_for(list(all_skill_ids))

    participant_count = getattr(race, "participant_count", None)

    out: dict[int, set[int]] = {}
    for r in results:
        ctx = race_context_for(race, r)
        # PR-SK9 — compute the result's gate bracket once per
        # result; reused across every post_number check below.
        # None when gate or participant_count is missing —
        # callers treat that as "skill applies" (defensive).
        bracket = gate_bracket(
            getattr(r, "gate", None), participant_count
        )
        inapp: set[int] = set()
        for assoc in getattr(r, "skills", []) or []:
            sid = getattr(assoc, "skill_id", None)
            if sid is None:
                continue
            cond = catalog.get(sid)
            if cond is None:
                # Unknown skill — default permissive (no gray-out).
                continue
            if not condition_matches(cond, ctx):
                inapp.add(sid)
                continue
            count = holder_counts.get(sid, 0)
            if cond.min_holders is not None and count < cond.min_holders:
                inapp.add(sid)
                continue
            if cond.max_holders is not None and count > cond.max_holders:
                inapp.add(sid)
                continue
            # PR-SK9 — post_number / gate bracket bounds. Skip
            # entirely when we couldn't compute a bracket (missing
            # gate or missing participant_count): defaulting to
            # "applies" matches the user's requirement that
            # gate-conditional skills not gray when the race
            # wasn't parsed.
            if bracket is not None:
                if (
                    cond.min_post_number is not None
                    and bracket < cond.min_post_number
                ):
                    inapp.add(sid)
                    continue
                if (
                    cond.max_post_number is not None
                    and bracket > cond.max_post_number
                ):
                    inapp.add(sid)
                    continue
        out[r.id] = inapp
    return out
