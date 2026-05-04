from __future__ import annotations

import pytest

from uma_ladder.services.preset_parser import PresetParseError, parse_lines


def test_parses_simple_line() -> None:
    rows = parse_lines("Sapporo Turf 2600m (Long) Right Max Runners: 14")
    assert len(rows) == 1
    r = rows[0]
    assert r.venue == "Sapporo"
    assert r.surface == "Turf"
    assert r.distance_meters == 2600
    assert r.distance_category == "Long"
    assert r.direction == "Right"
    assert r.course_variant is None
    assert r.max_runners == 14


def test_parses_variant_line() -> None:
    rows = parse_lines("Niigata Turf 2400m (Medium) Left / Inner Max Runners: 18")
    assert rows[0].direction == "Left"
    assert rows[0].course_variant == "Inner"


def test_parses_arrow_variant() -> None:
    rows = parse_lines("Hanshin Turf 3200m (Long) Right / Outer→Inner Max Runners: 18")
    assert rows[0].course_variant == "Outer→Inner"


def test_parses_stretch_direction() -> None:
    rows = parse_lines("Niigata Turf 1000m (Sprint) Stretch Max Runners: 18")
    assert rows[0].direction == "Stretch"
    assert rows[0].course_variant is None


def test_skips_section_headers_and_blank_lines() -> None:
    text = """
Turf Races
Sapporo Turf 2600m (Long) Right Max Runners: 14

Dirt Races
Sapporo Dirt 1700m (Mile) Right Max Runners: 14
"""
    rows = parse_lines(text)
    assert [r.surface for r in rows] == ["Turf", "Dirt"]


def test_normalizes_innermax_glitch() -> None:
    # Simulate the appendix glitch where "Inner" runs into "Max" without a space.
    rows = parse_lines("Niigata Turf 2400m (Medium) Left / InnerMax Runners: 18")
    assert rows[0].course_variant == "Inner"
    assert rows[0].max_runners == 18


def test_normalizes_outermax_glitch() -> None:
    rows = parse_lines("Niigata Turf 2000m (Medium) Left / OuterMax Runners: 18")
    assert rows[0].course_variant == "Outer"


def test_unknown_venue_raises_with_line_number() -> None:
    with pytest.raises(PresetParseError) as exc:
        parse_lines("Mars Turf 2000m (Medium) Left Max Runners: 18")
    assert exc.value.line_number == 1
    assert "Mars" in exc.value.reason


def test_garbage_line_raises() -> None:
    with pytest.raises(PresetParseError) as exc:
        parse_lines("Sapporo\ngibberish line here\n")
    assert exc.value.line_number == 1


def test_full_appendix_parses() -> None:
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "seeds"
        / "custom_races.txt"
    ).read_text(encoding="utf-8")
    rows = parse_lines(text)
    assert len(rows) == 85  # full appendix: 60 turf + 25 dirt
    surfaces = {r.surface for r in rows}
    assert surfaces == {"Turf", "Dirt"}
