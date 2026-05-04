"""Elo rating math.

Pure functions. The DB-side application (writing change rows) lives in
services/draft.py — this module knows nothing about ORM, sessions, or
matches as records.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RATING = 1000
DEFAULT_K = 32


@dataclass(frozen=True)
class RatingChange:
    rating_before: int
    rating_after: int
    delta: int
    outcome: float


def expected_score(rating_a: int, rating_b: int) -> float:
    """Standard logistic expectation."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def apply_match(
    rating_a: int,
    rating_b: int,
    outcome_a: float,
    *,
    k: int = DEFAULT_K,
) -> tuple[RatingChange, RatingChange]:
    """Return (change_for_a, change_for_b) given player A's outcome.

    `outcome_a` is 1.0 for A win, 0.0 for A loss, 0.5 for draw.
    Ratings are integers; rounding is half-to-even via builtin round().
    """
    if outcome_a not in (0.0, 0.5, 1.0):
        raise ValueError(f"outcome_a must be 0, 0.5, or 1; got {outcome_a}")

    expected_a = expected_score(rating_a, rating_b)
    expected_b = 1.0 - expected_a
    outcome_b = 1.0 - outcome_a

    new_a = round(rating_a + k * (outcome_a - expected_a))
    new_b = round(rating_b + k * (outcome_b - expected_b))
    return (
        RatingChange(rating_a, new_a, new_a - rating_a, outcome_a),
        RatingChange(rating_b, new_b, new_b - rating_b, outcome_b),
    )
