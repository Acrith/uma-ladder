from __future__ import annotations

import pytest

from uma_ladder.services.scoring import points_for_placement


@pytest.mark.parametrize(
    "placement, expected",
    [
        (1, 10),
        (2, 8),
        (3, 6),
        (4, 5),
        (5, 4),
        (6, 3),
        (7, 2),
        (8, 1),
        (9, 0),
        (10, 0),
        (18, 0),
    ],
)
def test_simple_placement_table(placement: int, expected: int) -> None:
    assert points_for_placement(placement) == expected


def test_grade_multiplier_off_by_default() -> None:
    assert points_for_placement(1, grade="G1") == 10


def test_grade_multiplier_on_g1() -> None:
    assert points_for_placement(1, grade="G1", apply_grade_multiplier=True) == 11


def test_grade_multiplier_op_lowers() -> None:
    # 10 * 0.9 = 9
    assert points_for_placement(1, grade="OP", apply_grade_multiplier=True) == 9


def test_unknown_grade_falls_back_to_1x() -> None:
    assert points_for_placement(2, grade="LISTED", apply_grade_multiplier=True) == 8


def test_invalid_placement() -> None:
    with pytest.raises(ValueError):
        points_for_placement(0)
