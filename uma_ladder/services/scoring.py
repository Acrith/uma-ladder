"""Official race scoring.

Centralized so future variants (grade multipliers, custom curves) plug
in here rather than scattering through routes.
"""

from __future__ import annotations

# §9 simple placement table. Anything above 8th gets 0.
_PLACEMENT_POINTS: dict[int, int] = {
    1: 10,
    2: 8,
    3: 6,
    4: 5,
    5: 4,
    6: 3,
    7: 2,
    8: 1,
}

_GRADE_MULTIPLIERS: dict[str, float] = {
    "G1": 1.1,
    "G2": 1.0,
    "G3": 1.0,
    "OP": 0.9,
    "PRE-OP": 0.9,
}


def points_for_placement(
    placement: int, *, grade: str | None = None, apply_grade_multiplier: bool = False
) -> int:
    """Return integer points for a placement.

    `apply_grade_multiplier` is off by default (intentions §9 recommends
    flat MVP scoring). When on, multiply by `_GRADE_MULTIPLIERS[grade]`
    and round half-to-even.
    """
    if placement < 1:
        raise ValueError(f"placement must be >= 1, got {placement}")
    base = _PLACEMENT_POINTS.get(placement, 0)
    if not apply_grade_multiplier or grade is None:
        return base
    multiplier = _GRADE_MULTIPLIERS.get(grade, 1.0)
    return round(base * multiplier)
