"""PR-G1: Champions Meeting service + admin CRUD + dashboard widget.

CMs are admin-managed events with a required FK to a G1 RacePreset and
optional override columns for the rare CM that uses non-standard
conditions. Dashboard widget shows the next 1-3 chronologically.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    ChampionsMeeting,
    PresetSource,
    RacePreset,
    Role,
)
from uma_ladder.services import cm as cm_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _ensure_preset(*, name: str = "Tokyo G1", venue: str = "Tokyo") -> RacePreset:
    p = (
        db.session.query(RacePreset)
        .filter_by(venue=venue, distance_meters=2000, surface="Turf", direction="Left")
        .first()
    )
    if p:
        return p
    p = RacePreset(
        source=PresetSource.G1_IMPORT,
        name=name,
        grade="G1",
        venue=venue,
        surface="Turf",
        distance_meters=2000,
        distance_category="Medium",
        direction="Left",
        course_variant=None,
        max_runners=18,
        enabled=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


# ---------- service-level ----------


def test_create_cm_inherits_preset_conditions(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        today = datetime.now(UTC).date()
        cm = cm_service.create_cm(
            cm_service.CmInput(
                name="Oct 2026 CM",
                starts_on=today + timedelta(days=10),
                ends_on=None,
                preset_id=preset.id,
            ),
            by_user_id=actor["id"],
        )
        assert cm.id is not None
        assert cm.effective_venue == "Tokyo"
        assert cm.effective_distance_meters == 2000
        assert cm.effective_surface == "Turf"
        assert cm.effective_direction == "Left"
        assert cm.is_clockwise is False  # Left = counterclockwise


def test_override_overrides_preset(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        cm = cm_service.create_cm(
            cm_service.CmInput(
                name="Edge CM",
                starts_on=date(2026, 6, 1),
                ends_on=None,
                preset_id=preset.id,
                override_distance_meters=2400,
                override_direction="Right",
            ),
            by_user_id=actor["id"],
        )
        assert cm.effective_distance_meters == 2400
        assert cm.effective_direction == "Right"
        assert cm.is_clockwise is True
        # Non-overridden fields still inherit.
        assert cm.effective_venue == "Tokyo"
        assert cm.effective_surface == "Turf"


def test_unknown_preset_raises(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        try:
            cm_service.create_cm(
                cm_service.CmInput(
                    name="Bad",
                    starts_on=date(2026, 6, 1),
                    ends_on=None,
                    preset_id=99999,
                ),
                by_user_id=actor["id"],
            )
        except cm_service.UnknownPresetError:
            pass
        else:
            raise AssertionError("expected UnknownPresetError")


def test_list_upcoming_filters_past_and_orders_chronologically(
    app: Flask, make_user
) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        today = datetime.now(UTC).date()
        # Past, sooner-future, later-future.
        for offset, name in [(-30, "old"), (5, "soon"), (20, "later"), (40, "far")]:
            cm_service.create_cm(
                cm_service.CmInput(
                    name=name,
                    starts_on=today + timedelta(days=offset),
                    ends_on=None,
                    preset_id=preset.id,
                ),
                by_user_id=actor["id"],
            )
        upcoming = cm_service.list_upcoming(limit=2)
        assert [c.name for c in upcoming] == ["soon", "later"]


def test_list_upcoming_includes_currently_running_cm(
    app: Flask, make_user
) -> None:
    """A CM whose starts_on is in the past but ends_on is in the
    future (or canonical 5-day window still open) should still surface
    on the dashboard."""
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        today = datetime.now(UTC).date()
        cm = cm_service.create_cm(
            cm_service.CmInput(
                name="Live CM",
                starts_on=today - timedelta(days=2),
                ends_on=today + timedelta(days=2),
                preset_id=preset.id,
            ),
            by_user_id=actor["id"],
        )
        names = [c.name for c in cm_service.list_upcoming(limit=5)]
        assert "Live CM" in names
        assert cm_service.is_active_now(cm) is True


def test_is_active_now_falls_back_to_5day_window(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        today = datetime.now(UTC).date()
        cm = cm_service.create_cm(
            cm_service.CmInput(
                name="No-end CM",
                starts_on=today - timedelta(days=1),
                ends_on=None,
                preset_id=preset.id,
            ),
            by_user_id=actor["id"],
        )
        assert cm_service.is_active_now(cm) is True
        # Six days in the past → outside the canonical window.
        cm.starts_on = today - timedelta(days=6)
        db.session.commit()
        assert cm_service.is_active_now(cm) is False


def test_update_and_delete_cm(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        cm = cm_service.create_cm(
            cm_service.CmInput(
                name="ToEdit",
                starts_on=date(2026, 6, 1),
                ends_on=None,
                preset_id=preset.id,
            ),
            by_user_id=actor["id"],
        )
        cm_service.update_cm(
            cm.id,
            cm_service.CmInput(
                name="Edited",
                starts_on=date(2026, 6, 1),
                ends_on=None,
                preset_id=preset.id,
                notes="updated",
            ),
            by_user_id=actor["id"],
        )
        refreshed = cm_service.get_cm(cm.id)
        assert refreshed.name == "Edited"
        assert refreshed.notes == "updated"

        cm_service.delete_cm(cm.id, by_user_id=actor["id"])
        assert cm_service.get_cm(cm.id) is None


# ---------- HTTP layer ----------


def test_admin_cm_list_gate(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    resp = client.get("/admin/cm")
    assert resp.status_code == 403


def test_admin_cm_list_renders(
    client: FlaskClient, app: Flask, make_user
) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        cm_service.create_cm(
            cm_service.CmInput(
                name="UI CM",
                starts_on=date(2026, 12, 1),
                ends_on=None,
                preset_id=preset.id,
            ),
            by_user_id=actor["id"],
        )
    _login(client, "adm")
    resp = client.get("/admin/cm")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "UI CM" in body
    assert "Counterclockwise" in body  # Left direction renders the human label


def test_admin_cm_create_via_post(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        preset_id = preset.id
    _login(client, "adm")
    resp = client.post(
        "/admin/cm/new",
        data={
            "name": "Posted CM",
            "starts_on": "2026-09-15",
            "ends_on": "2026-09-19",
            "preset_id": str(preset_id),
            "notes": "test note",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        rows = db.session.query(ChampionsMeeting).filter_by(name="Posted CM").all()
        assert len(rows) == 1
        assert rows[0].notes == "test note"


def test_admin_cm_create_rejects_missing_preset(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    _login(client, "adm")
    resp = client.post(
        "/admin/cm/new",
        data={
            "name": "X",
            "starts_on": "2026-09-15",
            "preset_id": "",
        },
        follow_redirects=False,
    )
    # Redirects back to the form with a flash message.
    assert resp.status_code == 302


def test_dashboard_renders_upcoming_cm_card(
    client: FlaskClient, app: Flask, make_user
) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        today = datetime.now(UTC).date()
        cm_service.create_cm(
            cm_service.CmInput(
                name="Dashboard CM",
                starts_on=today + timedelta(days=7),
                ends_on=None,
                preset_id=preset.id,
            ),
            by_user_id=actor["id"],
        )
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Champions Meeting" in body
    assert "Dashboard CM" in body


def test_dashboard_omits_card_when_no_cms(
    client: FlaskClient, app: Flask
) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    # The eyebrow text only appears when the card renders.
    assert "Champions Meeting · upcoming" not in body
