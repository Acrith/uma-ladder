from __future__ import annotations

import pytest

from uma_ladder.services.elo import (
    DEFAULT_K,
    apply_match,
    expected_score,
)


def test_equal_ratings_split_expectation() -> None:
    assert expected_score(1000, 1000) == pytest.approx(0.5)


def test_higher_rating_higher_expectation() -> None:
    assert expected_score(1200, 1000) > 0.5
    assert expected_score(1000, 1200) < 0.5


def test_equal_ratings_winner_gains_half_k() -> None:
    a, b = apply_match(1000, 1000, outcome_a=1.0)
    assert a.rating_after - 1000 == DEFAULT_K // 2 == 16
    assert b.rating_after - 1000 == -16
    assert a.delta == 16
    assert b.delta == -16


def test_loser_loses_more_against_lower_rated_opponent() -> None:
    # 1200 vs 1000, 1200 loses → 1000 expected, but only got 0
    high, low = apply_match(1200, 1000, outcome_a=0.0)
    assert high.delta < 0
    assert low.delta > 0
    # symmetric magnitude
    assert high.delta == -low.delta


def test_outcome_must_be_valid() -> None:
    with pytest.raises(ValueError):
        apply_match(1000, 1000, outcome_a=0.7)


def test_zero_sum_property() -> None:
    a, b = apply_match(1100, 980, outcome_a=1.0)
    # Rating points are conserved (within rounding).
    assert abs(a.delta + b.delta) <= 1


def test_draw_outcome_05() -> None:
    a, b = apply_match(1000, 1000, outcome_a=0.5)
    assert a.delta == 0
    assert b.delta == 0
