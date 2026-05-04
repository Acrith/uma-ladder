from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import RacePreset
from uma_ladder.services.preset_parser import PresetParseError
from uma_ladder.services.seed_presets import seed_custom_presets


def _seed_file(tmp_path: Path, lines: list[str], name: str = "races.txt") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_seed_inserts_then_skips(app: Flask, tmp_path: Path) -> None:
    seed = _seed_file(
        tmp_path,
        [
            "Sapporo Turf 2600m (Long) Right Max Runners: 14",
            "Niigata Turf 2400m (Medium) Left / Inner Max Runners: 18",
        ],
    )
    with app.app_context():
        first = seed_custom_presets(seed)
        assert (first.inserted, first.updated, first.skipped) == (2, 0, 0)
        second = seed_custom_presets(seed)
        assert (second.inserted, second.updated, second.skipped) == (0, 0, 2)
        assert db.session.query(RacePreset).count() == 2


def test_seed_updates_max_runners(app: Flask, tmp_path: Path) -> None:
    seed_a = _seed_file(
        tmp_path, ["Sapporo Turf 2600m (Long) Right Max Runners: 14"], name="a.txt"
    )
    seed_b = _seed_file(
        tmp_path, ["Sapporo Turf 2600m (Long) Right Max Runners: 16"], name="b.txt"
    )
    with app.app_context():
        seed_custom_presets(seed_a)
        report = seed_custom_presets(seed_b)
        assert report.updated == 1
        row = db.session.query(RacePreset).first()
        assert row is not None
        assert row.max_runners == 16


def test_default_appendix_seed_loads(app: Flask) -> None:
    with app.app_context():
        report = seed_custom_presets()
        assert report.total == 85


def test_seed_propagates_parse_errors(app: Flask, tmp_path: Path) -> None:
    bad = _seed_file(tmp_path, ["complete nonsense"])
    with app.app_context(), pytest.raises(PresetParseError):
        seed_custom_presets(bad)
