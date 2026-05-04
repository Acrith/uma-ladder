from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import RacePreset, Role
from uma_ladder.models.enums import PresetSource


def _make_preset(app: Flask, **overrides) -> int:
    with app.app_context():
        defaults = dict(
            source=PresetSource.CUSTOM_BUILTIN,
            name="Sapporo Turf 2600m (Long) Right",
            venue="Sapporo",
            surface="Turf",
            distance_meters=2600,
            distance_category="Long",
            direction="Right",
            course_variant=None,
            max_runners=14,
            enabled=True,
        )
        defaults.update(overrides)
        p = RacePreset(**defaults)
        db.session.add(p)
        db.session.commit()
        return p.id


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_index_lists_presets_for_anonymous(client: FlaskClient, app: Flask) -> None:
    _make_preset(app)
    resp = client.get("/presets/")
    assert resp.status_code == 200
    assert b"Sapporo" in resp.data


def test_toggle_requires_admin(client: FlaskClient, app: Flask, make_user) -> None:
    pid = _make_preset(app)
    # anonymous → 401
    resp = client.post(f"/presets/{pid}/toggle")
    assert resp.status_code == 401

    # regular user → 403
    make_user(username="alice", password="password123", role=Role.USER)
    _login(client, "alice", "password123")
    resp = client.post(f"/presets/{pid}/toggle")
    assert resp.status_code == 403


def test_toggle_flips_enabled_for_admin(client: FlaskClient, app: Flask, make_user) -> None:
    pid = _make_preset(app, enabled=True)
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")
    resp = client.post(f"/presets/{pid}/toggle", follow_redirects=False)
    assert resp.status_code == 302
    with app.app_context():
        row = db.session.get(RacePreset, pid)
        assert row is not None
        assert row.enabled is False
