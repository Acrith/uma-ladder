from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import UmaCharacter
from uma_ladder.services.seed_characters import seed_characters


def _seed_file(tmp_path: Path, characters: list[dict]) -> Path:
    p = tmp_path / "chars.json"
    p.write_text(json.dumps({"characters": characters}), encoding="utf-8")
    return p


def test_seed_inserts_then_skips(app: Flask, tmp_path: Path) -> None:
    seed = _seed_file(
        tmp_path,
        [
            {"slug": "special-week", "name_en": "Special Week", "name_jp": "スペシャルウィーク"},
            {"slug": "gold-ship", "name_en": "Gold Ship"},
        ],
    )
    with app.app_context():
        first = seed_characters(seed)
        assert (first.inserted, first.updated, first.skipped) == (2, 0, 0)
        second = seed_characters(seed)
        assert (second.inserted, second.updated, second.skipped) == (0, 0, 2)
        assert db.session.query(UmaCharacter).count() == 2


def test_seed_updates_changed_fields(app: Flask, tmp_path: Path) -> None:
    seed_a = _seed_file(
        tmp_path, [{"slug": "tokai-teio", "name_en": "Tokai Teio"}]
    )
    with app.app_context():
        seed_characters(seed_a)
        seed_b = _seed_file(
            tmp_path,
            [{"slug": "tokai-teio", "name_en": "Tokai Teio (updated)"}],
        )
        report = seed_characters(seed_b)
        assert report.updated == 1
        row = db.session.query(UmaCharacter).filter_by(slug="tokai-teio").one()
        assert row.name_en == "Tokai Teio (updated)"


def test_default_seed_file_loads(app: Flask) -> None:
    with app.app_context():
        report = seed_characters()
        assert report.total > 0
        assert db.session.query(UmaCharacter).count() == report.total


def test_seed_rejects_malformed_payload(app: Flask, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"oops": []}), encoding="utf-8")
    with app.app_context(), pytest.raises(ValueError):
        seed_characters(bad)
