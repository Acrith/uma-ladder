from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    OfficialRaceStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource


def _make_preset(app: Flask) -> int:
    with app.app_context():
        p = RacePreset(
            source=PresetSource.G1_IMPORT,
            name="Tokyo G1",
            venue="Tokyo",
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
        return p.id


def _make_season(app: Flask, name: str = "S1") -> int:
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name=name,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=89),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_index_anonymous_ok(client: FlaskClient) -> None:
    resp = client.get("/official/")
    assert resp.status_code == 200


def test_create_requires_organizer(client: FlaskClient, app: Flask, make_user) -> None:
    sid = _make_season(app)
    # anon → 401
    resp = client.post("/official/new", data={"season_id": sid, "name": "R"})
    assert resp.status_code == 401

    # regular user → 403
    make_user(username="alice", password="password123", role=Role.USER)
    _login(client, "alice", "password123")
    resp = client.post("/official/new", data={"season_id": sid, "name": "R"})
    assert resp.status_code == 403


def test_organizer_create_open_register_room_code_results_flow(
    client: FlaskClient, app: Flask, make_user
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)
    make_user(username="bob", password="password123", role=Role.USER)

    # organizer creates race
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "Spring G1", "preset_id": pid},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    race_url = resp.headers["Location"]
    race_id = int(race_url.rsplit("/", 1)[-1])

    # organizer opens registration
    resp = client.post(f"/official/{race_id}/open", follow_redirects=False)
    assert resp.status_code == 302

    client.post("/auth/logout")

    # alice + bob register
    _login(client, "alice", "password123")
    assert client.post(f"/official/{race_id}/register").status_code == 302
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    assert client.post(f"/official/{race_id}/register").status_code == 302
    client.post("/auth/logout")

    # organizer pastes room code and submits results
    _login(client, "org", "password123")
    resp = client.post(
        f"/official/{race_id}/room-code", data={"room_code": "ROOM-7"}
    )
    assert resp.status_code == 302

    # find registration ids by re-rendering detail page (or query DB)
    from uma_ladder.models import OfficialRaceRegistration

    with app.app_context():
        regs = (
            db.session.query(OfficialRaceRegistration)
            .filter_by(official_race_id=race_id)
            .order_by(OfficialRaceRegistration.id)
            .all()
        )
        reg_ids = {r.user.username: r.id for r in regs}

    resp = client.post(
        f"/official/{race_id}/results",
        data={
            f"placement_{reg_ids['alice']}": "1",
            f"placement_{reg_ids['bob']}": "2",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # ladder shows alice on top
    resp = client.get(f"/official/ladder/{sid}")
    assert resp.status_code == 200
    body = resp.data
    assert body.index(b"alice") < body.index(b"bob")

    # race status = completed
    from uma_ladder.models import OfficialRace

    with app.app_context():
        race = db.session.get(OfficialRace, race_id)
        assert race is not None
        assert race.status == OfficialRaceStatus.COMPLETED


def test_anonymous_cannot_register(client: FlaskClient, app: Flask, make_user) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")

    resp = client.post(f"/official/{race_id}/register", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_dashboard_shows_top5_block(client: FlaskClient, app: Flask) -> None:
    _make_season(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Official ladder" in resp.data


def test_new_form_renders_preset_dropdown_with_data_source(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The preset picker must render presets as <option> tags with
    data-source attributes (drives the client-side pool filter)."""
    _make_season(app)
    _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.get("/official/new")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Preset is in dropdown with data-source attr.
    assert 'id="preset-select"' in body
    assert 'data-source="g1_import"' in body
    assert "Tokyo G1" in body
    # Pool filter selector is present.
    assert 'id="preset-pool"' in body
    # Random button is type=button so it never submits the form.
    assert 'onclick="suggestRandomPreset()"' in body


def test_create_without_preset_id_rejected(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Track is required at creation time."""
    sid = _make_season(app)
    _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "missing track"},
        follow_redirects=False,
    )
    # Validation fails → form re-renders (200), no redirect.
    assert resp.status_code == 200
    from uma_ladder.models import OfficialRace
    with app.app_context():
        assert db.session.query(OfficialRace).count() == 0


def test_dashboard_shows_upcoming_official_races(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """An official race in registration_open status should appear on the
    dashboard's 'Upcoming' card with its registration count."""
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "Tonight 8pm", "preset_id": pid, "max_players": 12},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Sign-ups open" in body
    assert "Tonight 8pm" in body
    # Registration count rendered "0 / 12".
    assert "/ 12" in body


def test_list_upcoming_races_filters_correctly(app: Flask, make_user) -> None:
    """list_upcoming_races returns only registration-open through
    room-code-available; not draft, completed, cancelled, expired."""
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)

    from uma_ladder.models import OfficialRace
    from uma_ladder.services import official as official_service

    with app.app_context():
        # Helper to create a race in arbitrary status.
        def _race(name: str, status: OfficialRaceStatus) -> int:
            r = OfficialRace(
                season_id=sid,
                organizer_user_id=1,  # any organizer-ish id, FK relaxed in tests
                name=name,
                preset_id=pid,
                status=status,
            )
            db.session.add(r)
            db.session.commit()
            return r.id

        ok_id = _race("OK1", OfficialRaceStatus.REGISTRATION_OPEN)
        _race("OK2", OfficialRaceStatus.ROOM_CODE_AVAILABLE)
        _race("HIDDEN_DRAFT", OfficialRaceStatus.DRAFT)
        _race("HIDDEN_DONE", OfficialRaceStatus.COMPLETED)
        _race("HIDDEN_CXL", OfficialRaceStatus.CANCELLED)

        upcoming = official_service.list_upcoming_races()
        names = {r.name for r in upcoming}
        assert "OK1" in names
        assert "OK2" in names
        assert "HIDDEN_DRAFT" not in names
        assert "HIDDEN_DONE" not in names
        assert "HIDDEN_CXL" not in names
        # OK1 is one of them
        assert ok_id in {r.id for r in upcoming}
