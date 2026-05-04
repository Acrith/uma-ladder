"""seed_skills idempotent upsert + prune semantics."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import UmaSkill
from uma_ladder.services.seed_skills import seed_skills


def _write(path: Path, skills: list[dict]) -> None:
    payload = {
        "source": "test",
        "attribution": "test",
        "fetched_at": "2026-05-05T00:00:00+00:00",
        "skills": skills,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _row(gametora_id: int, name_en: str = "Skill", **kw) -> dict:
    base = {
        "gametora_id": gametora_id,
        "name_en": name_en,
        "is_unique": False,
        "is_inherited": False,
    }
    base.update(kw)
    return base


def test_seed_inserts_then_idempotent_re_run(app: Flask, tmp_path: Path) -> None:
    seed_path = tmp_path / "uma_skills.json"
    _write(seed_path, [_row(110031, "A"), _row(910031, "B", is_inherited=True)])
    with app.app_context():
        r1 = seed_skills(seed_path)
        assert r1.inserted == 2
        assert r1.updated == 0
        assert db.session.query(UmaSkill).count() == 2
        # Re-running with no changes should leave the table alone.
        r2 = seed_skills(seed_path)
        assert r2.inserted == 0
        assert r2.updated == 0
        assert r2.skipped == 2


def test_seed_updates_on_field_change(app: Flask, tmp_path: Path) -> None:
    seed_path = tmp_path / "uma_skills.json"
    _write(seed_path, [_row(110031, "Old name")])
    with app.app_context():
        seed_skills(seed_path)
        # Bump the EN name in the snapshot — should mark as updated.
        _write(seed_path, [_row(110031, "New name")])
        report = seed_skills(seed_path)
        assert report.updated == 1
        skill = db.session.query(UmaSkill).filter_by(gametora_id=110031).one()
        assert skill.name_en == "New name"


def test_seed_prune_disables_missing(app: Flask, tmp_path: Path) -> None:
    seed_path = tmp_path / "uma_skills.json"
    _write(seed_path, [_row(110031, "A"), _row(110099, "B")])
    with app.app_context():
        seed_skills(seed_path)
        # Snapshot now drops row 110099.
        _write(seed_path, [_row(110031, "A")])
        report = seed_skills(seed_path, prune_missing=True)
        assert report.pruned == 1
        b = db.session.query(UmaSkill).filter_by(gametora_id=110099).one()
        assert b.enabled is False


def test_seed_re_enables_previously_pruned(app: Flask, tmp_path: Path) -> None:
    """Prune then re-include: the row should come back as enabled, not
    leak a dead disabled state."""
    seed_path = tmp_path / "uma_skills.json"
    _write(seed_path, [_row(110031, "A"), _row(110099, "B")])
    with app.app_context():
        seed_skills(seed_path)
        # Prune B.
        _write(seed_path, [_row(110031, "A")])
        seed_skills(seed_path, prune_missing=True)
        # Re-include B.
        _write(seed_path, [_row(110031, "A"), _row(110099, "B")])
        seed_skills(seed_path)
        b = db.session.query(UmaSkill).filter_by(gametora_id=110099).one()
        assert b.enabled is True


def test_seed_writes_image_url(app: Flask, tmp_path: Path) -> None:
    seed_path = tmp_path / "uma_skills.json"
    _write(
        seed_path,
        [
            _row(
                110031,
                "Skill",
                icon_id=20013,
                image_url="https://gametora.com/images/umamusume/skill_icons/utx_ico_skill_20013.png",
            )
        ],
    )
    with app.app_context():
        seed_skills(seed_path)
        s = db.session.query(UmaSkill).filter_by(gametora_id=110031).one()
        assert s.image_url == (
            "https://gametora.com/images/umamusume/skill_icons/utx_ico_skill_20013.png"
        )


def test_seed_skips_invalid_rows(app: Flask, tmp_path: Path) -> None:
    seed_path = tmp_path / "uma_skills.json"
    _write(
        seed_path,
        [
            _row(110031, "ok"),
            {"name_en": "no id"},
            {"gametora_id": 110099},  # missing name_en
        ],
    )
    with app.app_context():
        report = seed_skills(seed_path)
        assert report.inserted == 1
        assert report.skipped == 2
