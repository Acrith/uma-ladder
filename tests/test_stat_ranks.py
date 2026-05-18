"""PR-A2 — stat-rank index/label mapping.

Threshold values mirror kachi-dev/uma-tools' rankForStat. The user's
real data point (Tamamo Cross sheet) confirmed 648 → B; the rest
of the table is exercised against published in-game thresholds.
"""

from __future__ import annotations

import pytest

from uma_ladder.services.stat_ranks import (
    aptitude_grade_icon_filename,
    rank_points_icon_filename,
    rank_points_index,
    rank_points_label,
    season_icon_filename,
    stat_rank_icon_filename,
    stat_rank_index,
    stat_rank_label,
    weather_icon_filename,
)


@pytest.mark.parametrize(
    "value,expected_idx,expected_label",
    [
        # Sub-400: index = value // 50
        (1, 0, "G"),
        (49, 0, "G"),
        (50, 1, "G+"),
        (99, 1, "G+"),
        (100, 2, "F"),
        (199, 3, "F+"),
        (200, 4, "E"),
        (299, 5, "E+"),
        (300, 6, "D"),
        (399, 7, "D+"),
        # 400-1099: index = 8 + (value-400) // 100
        (400, 8, "C"),
        (499, 8, "C"),
        (500, 9, "C+"),
        (599, 9, "C+"),
        (600, 10, "B"),
        (648, 10, "B"),  # real Tamamo Cross stamina from the OCR dump
        (692, 10, "B"),  # real Tamamo Cross guts
        (700, 11, "B+"),
        (800, 12, "A"),
        (900, 13, "A+"),
        (999, 13, "A+"),
        (1000, 14, "S"),
        (1099, 14, "S"),
        # 1100-1149: SS (note: formula SKIPS index 15 = S+)
        (1100, 16, "SS"),
        (1149, 16, "SS"),
        # 1150-1200: SS+
        (1150, 17, "SS+"),
        (1169, 17, "SS+"),  # real Tamamo Cross wisdom
        (1181, 17, "SS+"),  # real Tamamo Cross power
        (1197, 17, "SS+"),  # real Tamamo Cross speed (from first dump)
        (1200, 17, "SS+"),
    ],
)
def test_stat_rank_index_known_thresholds(
    value: int, expected_idx: int, expected_label: str
) -> None:
    assert stat_rank_index(value) == expected_idx
    assert stat_rank_label(value) == expected_label


@pytest.mark.parametrize("value", [None, 0, -5])
def test_stat_rank_index_none_for_invalid(value: int | None) -> None:
    assert stat_rank_index(value) is None
    assert stat_rank_label(value) == ""
    assert stat_rank_icon_filename(value) is None


def test_stat_rank_index_ug_bracket() -> None:
    """Values > 1200 enter the UG bracket. kachi's formula spreads
    letter variants by 100s with +10 minor increments; we cap at 97
    (last shipped icon). Label collapses to a generic "UG" since the
    minor sub-variants aren't useful in a results context."""
    # 1201 → 18 + 0 * 10 + (1201 // 10) % 10 = 18 + 0 + 0 = 18.
    assert stat_rank_index(1201) == 18
    assert stat_rank_label(1201) == "UG"
    # Cap test — very large values shouldn't exceed 97.
    assert stat_rank_index(10_000) == 97


def test_stat_rank_icon_filename_format() -> None:
    """Filename is the 0-padded index → .png. Used by templates to
    build a /static/img/statusrank/<filename> URL."""
    assert stat_rank_icon_filename(1) == "ui_statusrank_00.png"
    assert stat_rank_icon_filename(648) == "ui_statusrank_10.png"
    assert stat_rank_icon_filename(1200) == "ui_statusrank_17.png"


def test_jinja_filters_registered(app) -> None:
    """The helper functions are exposed as Jinja filters so
    templates can do `{{ value | stat_rank_label }}` directly."""
    assert "stat_rank_label" in app.jinja_env.filters
    assert "stat_rank_icon_filename" in app.jinja_env.filters
    assert "aptitude_grade_icon_filename" in app.jinja_env.filters


# ─── PR-A3: aptitude grade icon mapping ──────────────────────────


@pytest.mark.parametrize(
    "grade,expected_filename",
    [
        ("G", "utx_ico_statusrank_00.png"),
        ("F", "utx_ico_statusrank_01.png"),
        ("E", "utx_ico_statusrank_02.png"),
        ("D", "utx_ico_statusrank_03.png"),
        ("C", "utx_ico_statusrank_04.png"),
        ("B", "utx_ico_statusrank_05.png"),
        ("A", "utx_ico_statusrank_06.png"),
        ("S", "utx_ico_statusrank_07.png"),
    ],
)
def test_aptitude_grade_icon_filename_full_table(
    grade: str, expected_filename: str
) -> None:
    """Mapping mirrors kachi-dev: G=00, F=01, ..., S=07. Stored
    uppercase; case-insensitive lookup."""
    assert aptitude_grade_icon_filename(grade) == expected_filename
    # Case insensitive.
    assert aptitude_grade_icon_filename(grade.lower()) == expected_filename


@pytest.mark.parametrize("grade", [None, "", "SS", "Z", "+", "  "])
def test_aptitude_grade_icon_filename_invalid_returns_none(
    grade: str | None,
) -> None:
    """Empty / unknown / out-of-range grades produce None; caller
    renders an em-dash placeholder."""
    assert aptitude_grade_icon_filename(grade) is None


# ─── PR-A4: weather + season icons ───────────────────────────────


@pytest.mark.parametrize(
    "weather,expected",
    [
        ("Sunny", "utx_ico_weather_00.png"),
        ("Cloudy", "utx_ico_weather_01.png"),
        ("Rainy", "utx_ico_weather_02.png"),
        ("Snowy", "utx_ico_weather_03.png"),
        # Case-insensitive (Title-case normalize).
        ("sunny", "utx_ico_weather_00.png"),
        ("SUNNY", "utx_ico_weather_00.png"),
    ],
)
def test_weather_icon_filename(weather: str, expected: str) -> None:
    assert weather_icon_filename(weather) == expected


@pytest.mark.parametrize("weather", [None, "", "Foggy", "Hail"])
def test_weather_icon_filename_invalid(weather: str | None) -> None:
    assert weather_icon_filename(weather) is None


@pytest.mark.parametrize(
    "season,expected",
    [
        ("Spring", "utx_txt_season_00.png"),
        ("Summer", "utx_txt_season_01.png"),
        ("Autumn", "utx_txt_season_02.png"),
        ("Winter", "utx_txt_season_03.png"),
        ("spring", "utx_txt_season_00.png"),
    ],
)
def test_season_icon_filename(season: str, expected: str) -> None:
    assert season_icon_filename(season) == expected


@pytest.mark.parametrize(
    "season", [None, "", "Sakura", "Fall", "Monsoon"]
)
def test_season_icon_filename_invalid(season: str | None) -> None:
    """Sakura is in kachi's enum but not in our RaceSeason, so it
    falls through the lookup just like an unknown value would."""
    assert season_icon_filename(season) is None


def test_pr_a4_jinja_filters_registered(app) -> None:
    assert "weather_icon_filename" in app.jinja_env.filters
    assert "season_icon_filename" in app.jinja_env.filters


# ─── PR-A6: overall uma rank from uma_score ──────────────────────


@pytest.mark.parametrize(
    "score,expected_idx,expected_label",
    [
        # Below first threshold (300) → G (idx 0).
        (1, 0, "G"),
        (299, 0, "G"),
        # 300 lands at G+, the start of the next bucket.
        (300, 1, "G+"),
        (599, 1, "G+"),
        (600, 2, "F"),
        (899, 2, "F"),
        (900, 3, "F+"),
        (1299, 3, "F+"),
        (1300, 4, "E"),
        (1799, 4, "E"),
        (1800, 5, "E+"),
        (2299, 5, "E+"),
        (2300, 6, "D"),
        (2899, 6, "D"),
        (2900, 7, "D+"),
        (3499, 7, "D+"),
        (3500, 8, "C"),
        (4899, 8, "C"),
        (4900, 9, "C+"),
        (6499, 9, "C+"),
        (6500, 10, "B"),
        (8199, 10, "B"),
        (8200, 11, "B+"),
        (9999, 11, "B+"),
        (10000, 12, "A"),
        (12099, 12, "A"),
        (12100, 13, "A+"),
        (14499, 13, "A+"),
        (14500, 14, "S"),
        (15899, 14, "S"),
        (15900, 15, "S+"),
        (17306, 15, "S+"),
        # Real Tamamo Cross uma_score from the user-supplied dump: 17307.
        (17307, 15, "S+"),
        (17499, 15, "S+"),
        (17500, 16, "SS"),
        (19199, 16, "SS"),
        (19200, 17, "SS+"),
        (19599, 17, "SS+"),
        # Ug bracket — each label increments at the next threshold.
        (19600, 18, "Ug⁰"),
        (19999, 18, "Ug⁰"),
        (20000, 19, "Ug¹"),
        (20399, 19, "Ug¹"),
        (20400, 20, "Ug²"),
        (20799, 20, "Ug²"),
        (20800, 21, "Ug³"),
        (21199, 21, "Ug³"),
        (21200, 22, "Ug⁴"),
        (21599, 22, "Ug⁴"),
        (21600, 23, "Ug⁵"),
        (22099, 23, "Ug⁵"),
        # 22100 and above all collapse to the Ug⁶ top bucket.
        (22100, 24, "Ug⁶"),
        (30000, 24, "Ug⁶"),
        (99999, 24, "Ug⁶"),
    ],
)
def test_rank_points_index_known_thresholds(
    score: int, expected_idx: int, expected_label: str
) -> None:
    assert rank_points_index(score) == expected_idx
    assert rank_points_label(score) == expected_label


@pytest.mark.parametrize("score", [None, 0, -1, -10_000])
def test_rank_points_invalid_returns_none(score: int | None) -> None:
    assert rank_points_index(score) is None
    assert rank_points_label(score) == ""
    assert rank_points_icon_filename(score) is None


def test_rank_points_icon_filename_format() -> None:
    """Filename reuses the stat-rank icon set — indices 0-24 map
    directly onto ui_statusrank_<idx:02d>.png. The published
    statusrank set ships icons 00..24+, so all 25 buckets resolve."""
    assert rank_points_icon_filename(1) == "ui_statusrank_00.png"
    assert rank_points_icon_filename(17307) == "ui_statusrank_15.png"
    assert rank_points_icon_filename(19600) == "ui_statusrank_18.png"
    assert rank_points_icon_filename(99999) == "ui_statusrank_24.png"


def test_pr_a6_jinja_filters_registered(app) -> None:
    assert "rank_points_label" in app.jinja_env.filters
    assert "rank_points_icon_filename" in app.jinja_env.filters
