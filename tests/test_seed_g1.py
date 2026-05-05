from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import RacePreset
from uma_ladder.models.enums import PresetSource
from uma_ladder.services.seed_g1 import G1ImportError, import_g1_races


def _seed(tmp_path: Path, races: list[dict], name: str = "g1.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"races": races}), encoding="utf-8")
    return p


def _row(**overrides) -> dict:
    base = {
        "name": "Tokyo Yushun",
        "grade": "G1",
        "venue": "Tokyo",
        "surface": "Turf",
        "distance_meters": 2400,
        "distance_category": "Medium",
        "direction": "Left",
        "course_variant": None,
        "max_runners": 18,
        "external_source_url": "https://gametora.com/umamusume/races",
    }
    base.update(overrides)
    return base


def test_inserts_then_skips(app: Flask, tmp_path: Path) -> None:
    src = _seed(tmp_path, [_row(), _row(name="Arima Kinen", venue="Nakayama", direction="Right", course_variant="Inner", distance_meters=2500, distance_category="Long", max_runners=16)])
    with app.app_context():
        first = import_g1_races(src)
        assert (first.inserted, first.updated, first.skipped) == (2, 0, 0)
        second = import_g1_races(src)
        assert (second.inserted, second.updated, second.skipped) == (0, 0, 2)
        rows = db.session.query(RacePreset).all()
        assert len(rows) == 2
        assert all(r.source == PresetSource.G1_IMPORT for r in rows)
        assert all(r.grade == "G1" for r in rows)


def test_updates_changed_max_runners(app: Flask, tmp_path: Path) -> None:
    a = _seed(tmp_path, [_row(max_runners=18)], name="a.json")
    b = _seed(tmp_path, [_row(max_runners=14)], name="b.json")
    with app.app_context():
        import_g1_races(a)
        report = import_g1_races(b)
        assert report.updated == 1
        row = db.session.query(RacePreset).first()
        assert row.max_runners == 14


def test_default_snapshot_loads(app: Flask) -> None:
    with app.app_context():
        report = import_g1_races()
        assert report.total > 0


def test_rejects_unknown_venue(app: Flask, tmp_path: Path) -> None:
    src = _seed(tmp_path, [_row(venue="Mars")])
    with app.app_context(), pytest.raises(G1ImportError) as exc:
        import_g1_races(src)
    assert "Mars" in str(exc.value)


def test_rejects_missing_field(app: Flask, tmp_path: Path) -> None:
    bad = _row()
    del bad["max_runners"]
    src = _seed(tmp_path, [bad])
    with app.app_context(), pytest.raises(G1ImportError) as exc:
        import_g1_races(src)
    assert "max_runners" in str(exc.value)


def test_rejects_non_int_distance(app: Flask, tmp_path: Path) -> None:
    src = _seed(tmp_path, [_row(distance_meters="2400")])
    with app.app_context(), pytest.raises(G1ImportError):
        import_g1_races(src)


def test_rejects_unknown_surface(app: Flask, tmp_path: Path) -> None:
    src = _seed(tmp_path, [_row(surface="Sand")])
    with app.app_context(), pytest.raises(G1ImportError):
        import_g1_races(src)


def test_g1_and_custom_coexist_after_pr_g2(app: Flask, tmp_path: Path) -> None:
    """A G1 import keeps a same-track-config custom preset intact —
    the row identity is (name, venue), so a custom row with a
    different name survives the G1 import alongside the new row."""
    with app.app_context():
        db.session.add(
            RacePreset(
                source=PresetSource.CUSTOM_BUILTIN,
                name="Tokyo Turf 2400m custom",
                venue="Tokyo",
                surface="Turf",
                distance_meters=2400,
                distance_category="Medium",
                direction="Left",
                course_variant=None,
                max_runners=18,
                enabled=True,
            )
        )
        db.session.commit()
        src = _seed(tmp_path, [_row()])
        report = import_g1_races(src)
        assert report.inserted == 1
        rows = db.session.query(RacePreset).order_by(RacePreset.name).all()
        assert {r.source for r in rows} == {
            PresetSource.G1_IMPORT,
            PresetSource.CUSTOM_BUILTIN,
        }
        # Both at the same physical track config — that's allowed now.
        assert all(r.venue == "Tokyo" and r.distance_meters == 2400 for r in rows)


def test_distinct_g1_names_at_same_track_each_get_a_row(
    app: Flask, tmp_path: Path
) -> None:
    """The headline PR-G2 case: Tokyo Yushun and Japanese Oaks share
    Tokyo 2400m turf left, and the seeder must keep both rows."""
    src = _seed(
        tmp_path,
        [
            _row(name="Tokyo Yushun"),
            _row(name="Japanese Oaks"),
            _row(name="Japan Cup"),
        ],
    )
    with app.app_context():
        report = import_g1_races(src)
        assert report.inserted == 3
        names = sorted(
            r.name for r in db.session.query(RacePreset).all()
        )
        assert names == ["Japan Cup", "Japanese Oaks", "Tokyo Yushun"]


def test_existing_row_updates_on_track_drift(
    app: Flask, tmp_path: Path
) -> None:
    """If GameTora corrects a track configuration upstream (rare),
    re-importing should rewrite the affected columns rather than
    creating a phantom second row."""
    a = _seed(tmp_path, [_row(distance_meters=2400)], name="a.json")
    b = _seed(tmp_path, [_row(distance_meters=2300, distance_category="Medium")], name="b.json")
    with app.app_context():
        import_g1_races(a)
        report = import_g1_races(b)
        assert report.updated == 1
        rows = db.session.query(RacePreset).all()
        assert len(rows) == 1
        assert rows[0].distance_meters == 2300
