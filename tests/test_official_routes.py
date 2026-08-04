from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
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


def test_index_filters_by_status(
    client: FlaskClient, app: Flask, make_user
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", role=Role.ORGANIZER)

    from uma_ladder.models import OfficialRace

    with app.app_context():
        db.session.add_all([
            OfficialRace(
                season_id=sid,
                organizer_user_id=org["id"],
                name="OpenRace",
                preset_id=pid,
                status=OfficialRaceStatus.REGISTRATION_OPEN,
            ),
            OfficialRace(
                season_id=sid,
                organizer_user_id=org["id"],
                name="DoneRace",
                preset_id=pid,
                status=OfficialRaceStatus.COMPLETED,
            ),
        ])
        db.session.commit()

    resp = client.get(
        f"/official/?status={OfficialRaceStatus.REGISTRATION_OPEN.value}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "OpenRace" in body
    assert "DoneRace" not in body


def test_index_paginates(
    client: FlaskClient, app: Flask, make_user
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", role=Role.ORGANIZER)

    from uma_ladder.models import OfficialRace

    with app.app_context():
        for i in range(55):
            db.session.add(
                OfficialRace(
                    season_id=sid,
                    organizer_user_id=org["id"],
                    name=f"Race-{i:02d}",
                    preset_id=pid,
                    status=OfficialRaceStatus.REGISTRATION_OPEN,
                )
            )
        db.session.commit()

    resp = client.get("/official/?page=1")
    assert resp.status_code == 200
    assert b"Page 1 / 2" in resp.data
    resp = client.get("/official/?page=2")
    assert resp.status_code == 200
    assert b"Page 2 / 2" in resp.data


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

    # ladder shows alice on top — PR-N1 unified at /rankings
    resp = client.get(f"/rankings/?season={sid}&mode=official")
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
    assert b"Season leader" in resp.data


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


def test_create_form_persists_scheduled_at(
    client: FlaskClient, app: Flask, make_user
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={
            "season_id": sid,
            "name": "Sat 8pm",
            "preset_id": pid,
            "scheduled_at": "2026-06-01T20:00",
        },
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    from uma_ladder.models import OfficialRace
    with app.app_context():
        race = db.session.get(OfficialRace, race_id)
        assert race.scheduled_at is not None
        assert race.scheduled_at.year == 2026
        assert race.scheduled_at.month == 6
        assert race.scheduled_at.day == 1
        assert race.scheduled_at.hour == 20


def test_upcoming_sorts_scheduled_first_then_unscheduled(
    app: Flask, make_user
) -> None:
    """Scheduled races appear ordered by scheduled_at ascending; races
    without a scheduled time fall to the bottom of the list."""
    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", role=Role.ORGANIZER)

    from uma_ladder.models import OfficialRace, OfficialRaceStatus
    from uma_ladder.services import official as official_service

    with app.app_context():
        unscheduled = OfficialRace(
            season_id=sid,
            organizer_user_id=org["id"],
            name="Unscheduled",
            preset_id=pid,
            status=OfficialRaceStatus.REGISTRATION_OPEN,
        )
        soon = OfficialRace(
            season_id=sid,
            organizer_user_id=org["id"],
            name="Soon",
            preset_id=pid,
            status=OfficialRaceStatus.REGISTRATION_OPEN,
            scheduled_at=datetime(2026, 6, 1, 20, 0, tzinfo=UTC),
        )
        later = OfficialRace(
            season_id=sid,
            organizer_user_id=org["id"],
            name="Later",
            preset_id=pid,
            status=OfficialRaceStatus.REGISTRATION_OPEN,
            scheduled_at=datetime(2026, 6, 2, 20, 0, tzinfo=UTC),
        )
        db.session.add_all([unscheduled, soon, later])
        db.session.commit()

        names = [r.name for r in official_service.list_upcoming_races()]
        assert names == ["Soon", "Later", "Unscheduled"]


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
    assert "registration open" in body
    assert "Tonight 8pm" in body
    # Registration count rendered "0 / 12".
    assert "/ 12" in body


def test_organizer_can_cancel_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "Cancel me", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    resp = client.post(f"/official/{race_id}/cancel", follow_redirects=False)
    assert resp.status_code == 302

    from uma_ladder.models import OfficialRace
    with app.app_context():
        race = db.session.get(OfficialRace, race_id)
        assert race.status == OfficialRaceStatus.CANCELLED
        assert race.cancelled_at is not None
        assert race.cancelled_by_user_id is not None

    # Cancelled banner renders + cancel button is gone.
    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Cancelled" in body
    assert ">Cancel race<" not in body


def test_non_organizer_cannot_cancel_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    resp = client.post(f"/official/{race_id}/cancel", follow_redirects=False)
    assert resp.status_code == 403


def test_completed_race_cannot_be_cancelled(app: Flask, make_user) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)

    from uma_ladder.models import OfficialRace
    from uma_ladder.services import official as official_service

    with app.app_context():
        r = OfficialRace(
            season_id=sid,
            organizer_user_id=org["id"],
            name="done",
            preset_id=pid,
            status=OfficialRaceStatus.COMPLETED,
        )
        db.session.add(r)
        db.session.commit()
        with pytest.raises(official_service.InvalidRaceStateError):
            official_service.cancel_race(r.id, by_user_id=org["id"])


def test_organizer_can_remove_registration(
    client: FlaskClient, app: Flask, make_user
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(f"/official/{race_id}/register")

    # alice now appears in registrations list.
    from uma_ladder.models import OfficialRaceRegistration, RegistrationStatus
    with app.app_context():
        reg = (
            db.session.query(OfficialRaceRegistration)
            .filter_by(official_race_id=race_id)
            .first()
        )
        assert reg is not None
        assert reg.status == RegistrationStatus.REGISTERED
        reg_id = reg.id

    # Organiser removes alice.
    client.post("/auth/logout")
    _login(client, "org", "password123")
    resp = client.post(
        f"/official/{race_id}/registrations/{reg_id}/remove",
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        reg = db.session.get(OfficialRaceRegistration, reg_id)
        assert reg.status == RegistrationStatus.CANCELLED

    # Detail page no longer lists alice (list_registrations filters
    # to REGISTERED only).
    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # username shouldn't appear in the registrations table now.
    # (Could appear in flash; check the count line specifically.)
    assert ">0" in body or "0 /" in body  # zero registrations


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


def test_user_can_unregister_themselves(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Self-unregister flow — the Register button on the detail page
    flips to a 'You're registered ✓' card with an Unregister button
    that posts to /registrations/<id>/remove. The route must accept
    self-removal even though it primarily serves organizer kicks."""
    from uma_ladder.models import OfficialRaceRegistration, RegistrationStatus

    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(f"/official/{race_id}/register")

    with app.app_context():
        reg = (
            db.session.query(OfficialRaceRegistration)
            .filter_by(official_race_id=race_id)
            .first()
        )
        reg_id = reg.id

    resp = client.post(
        f"/official/{race_id}/registrations/{reg_id}/remove",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        reg = db.session.get(OfficialRaceRegistration, reg_id)
        assert reg.status == RegistrationStatus.CANCELLED


def test_user_cannot_remove_other_users_registration(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Non-organizer user trying to kick someone else gets 403."""
    from uma_ladder.models import OfficialRaceRegistration

    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)
    make_user(username="bob", password="password123", role=Role.USER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(f"/official/{race_id}/register")
    with app.app_context():
        alice_reg = (
            db.session.query(OfficialRaceRegistration)
            .filter_by(official_race_id=race_id)
            .first()
        )
        alice_reg_id = alice_reg.id
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.post(
        f"/official/{race_id}/registrations/{alice_reg_id}/remove",
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_re_register_after_being_kicked(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The unique constraint on (race, user) means a previously-
    cancelled registration row exists. Re-registering must revive
    that row in place rather than INSERT a duplicate (which would
    crash with IntegrityError)."""
    from uma_ladder.models import OfficialRaceRegistration, RegistrationStatus

    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(f"/official/{race_id}/register")
    with app.app_context():
        reg_id = (
            db.session.query(OfficialRaceRegistration)
            .filter_by(official_race_id=race_id)
            .first()
        ).id
    client.post(f"/official/{race_id}/registrations/{reg_id}/remove")
    # Now alice is CANCELLED; re-register should revive the same row.
    client.post(f"/official/{race_id}/register")
    with app.app_context():
        rows = (
            db.session.query(OfficialRaceRegistration)
            .filter_by(official_race_id=race_id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == RegistrationStatus.REGISTERED


# ---------- PR-J1: ownership-gated permissions ----------


def _create_race_as(client, app, name="R", organizer_username="orgA") -> int:
    sid = _make_season(app)
    pid = _make_preset(app)
    _login(client, organizer_username, "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": name, "preset_id": pid},
        follow_redirects=False,
    )
    return int(resp.headers["Location"].rsplit("/", 1)[-1])


def test_organizer_cannot_cancel_another_organizers_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Two organizers, two races. Organizer-B can't cancel
    organizer-A's race."""
    make_user(username="orgA", password="password123", role=Role.ORGANIZER)
    make_user(username="orgB", password="password123", role=Role.ORGANIZER)
    race_id = _create_race_as(client, app, organizer_username="orgA")

    # Switch to orgB and try to cancel orgA's race.
    client.post("/auth/logout")
    _login(client, "orgB", "password123")
    resp = client.post(
        f"/official/{race_id}/cancel",
        data={"csrf_token": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_organizer_cannot_open_or_close_another_organizers_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="orgA", password="password123", role=Role.ORGANIZER)
    make_user(username="orgB", password="password123", role=Role.ORGANIZER)
    race_id = _create_race_as(client, app, organizer_username="orgA")
    client.post("/auth/logout")
    _login(client, "orgB", "password123")
    assert client.post(f"/official/{race_id}/open").status_code == 403
    assert client.post(f"/official/{race_id}/close").status_code == 403


def test_organizer_cannot_set_room_code_on_another_organizers_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="orgA", password="password123", role=Role.ORGANIZER)
    make_user(username="orgB", password="password123", role=Role.ORGANIZER)
    race_id = _create_race_as(client, app, organizer_username="orgA")
    # orgA opens the race so room-code is reachable.
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "orgB", "password123")
    resp = client.post(
        f"/official/{race_id}/room-code",
        data={"room_code": "RC-X"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_senior_organizer_can_act_on_any_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Senior organizer is the moderator-of-organizers tier — they
    can cancel any race regardless of who created it."""
    make_user(username="orgA", password="password123", role=Role.ORGANIZER)
    make_user(
        username="senior", password="password123", role=Role.SENIOR_ORGANIZER
    )
    race_id = _create_race_as(client, app, organizer_username="orgA")
    client.post("/auth/logout")
    _login(client, "senior", "password123")
    resp = client.post(
        f"/official/{race_id}/cancel",
        data={"csrf_token": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 302  # success → redirect to detail


def test_admin_can_act_on_any_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="orgA", password="password123", role=Role.ORGANIZER)
    make_user(username="adm", password="password123", role=Role.ADMIN)
    race_id = _create_race_as(client, app, organizer_username="orgA")
    client.post("/auth/logout")
    _login(client, "adm", "password123")
    resp = client.post(
        f"/official/{race_id}/cancel",
        data={"csrf_token": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_race_organizer_can_still_act_on_their_own_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Sanity: the existing flow for an organizer working their own
    race must still succeed under the ownership check."""
    make_user(username="orgA", password="password123", role=Role.ORGANIZER)
    race_id = _create_race_as(client, app, organizer_username="orgA")
    # orgA still logged in from the create call.
    assert client.post(f"/official/{race_id}/open").status_code == 302
    assert client.post(f"/official/{race_id}/close").status_code == 302


# ---------- PR-J13: targeted (Public/Private) races ----------


def _create_private_race(
    app: Flask, organizer_id: int
) -> int:
    """Create a Private race directly via service to keep tests
    tight (form route exercised separately)."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Private Cup",
                organizer_user_id=organizer_id,
                preset_id=pid,
                visibility="private",
            )
        )
        return race.id


def test_create_form_accepts_visibility_private(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The form goes through visibility=private end-to-end. Smoke
    test that wires CreateRaceRequest.visibility into the model."""
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")

    resp = client.post(
        "/official/new",
        data={
            "season_id": str(sid),
            "name": "Private Cup",
            "preset_id": str(pid),
            "visibility": "private",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    from uma_ladder.models import OfficialRace

    with app.app_context():
        race = db.session.query(OfficialRace).first()
        assert race is not None
        assert race.visibility == "private"


def test_index_hides_private_from_uninvited_users(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Anonymous + non-invited users don't see Private races on
    the index. Organizer + invitees do."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    _create_private_race(app, organizer_id=org["id"])
    make_user(username="snoop", password="password123", role=Role.USER)

    # Anonymous: race must not appear.
    resp = client.get("/official/")
    body = resp.data.decode()
    assert "Private Cup" not in body

    # Non-invited user: same.
    _login(client, "snoop", "password123")
    body = client.get("/official/").data.decode()
    assert "Private Cup" not in body

    # Organizer: sees it.
    client.post("/auth/logout")
    _login(client, "org", "password123")
    body = client.get("/official/").data.decode()
    assert "Private Cup" in body


def test_detail_404_for_uninvited(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Private detail returns 404 (not 403) so the URL doesn't
    even confirm the race exists to non-invitees."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    make_user(username="snoop", password="password123", role=Role.USER)

    # Anonymous
    assert client.get(f"/official/{race_id}").status_code == 404
    # Non-invited
    _login(client, "snoop", "password123")
    assert client.get(f"/official/{race_id}").status_code == 404


def test_invite_grants_view_and_register(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """After an organizer invites a user, that user can see + register
    for the Private race."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    make_user(username="alice", password="password123", role=Role.USER)

    _login(client, "org", "password123")
    resp = client.post(
        f"/official/{race_id}/invitees",
        data={"invitee_username": "alice"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Open registration so invitee can register.
    client.post(f"/official/{race_id}/open")

    # Switch to alice — she now sees the detail page.
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    detail = client.get(f"/official/{race_id}")
    assert detail.status_code == 200
    assert b"Private Cup" in detail.data
    # And she can register.
    reg = client.post(
        f"/official/{race_id}/register",
        follow_redirects=False,
    )
    assert reg.status_code == 302


def test_register_blocked_for_non_invitee(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Defense-in-depth: even if a non-invitee POSTs the register
    endpoint directly, the service refuses."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    snoop = make_user(username="snoop", password="password123", role=Role.USER)

    # Open registration
    with app.app_context():
        official_service.open_registration(race_id)

    _login(client, "snoop", "password123")
    client.post(f"/official/{race_id}/register", follow_redirects=False)
    # Detail returns 404, but the register POST goes through min_role +
    # service. The service raises OfficialError → flashed; redirect home.
    # Either 404 or 302 with no registration is acceptable here. What
    # matters: no DB row was created.
    from uma_ladder.models import OfficialRaceRegistration

    with app.app_context():
        regs = db.session.query(OfficialRaceRegistration).filter_by(
            user_id=snoop["id"]
        ).count()
        assert regs == 0


def test_remove_invitee_revokes_access(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Pulling someone off the invitee list immediately makes the
    Private race invisible to them again."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    alice = make_user(username="alice", password="password123", role=Role.USER)
    with app.app_context():
        official_service.add_invitee(
            race_id,
            invitee_username="alice",
            invited_by_user_id=org["id"],
        )

    _login(client, "alice", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200

    # Organizer removes alice.
    client.post("/auth/logout")
    _login(client, "org", "password123")
    resp = client.post(
        f"/official/{race_id}/invitees/{alice['id']}/remove",
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # Alice can no longer see the race.
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    assert client.get(f"/official/{race_id}").status_code == 404


def test_senior_organizer_can_view_private_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Senior organizers act as moderators per docs/permissions.md;
    they should see Private races without being explicitly invited."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    make_user(username="sr", password="password123", role=Role.SENIOR_ORGANIZER)

    _login(client, "sr", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200


def test_create_rejects_unsupported_visibility(
    app: Flask, make_user
) -> None:
    """`club` reserved for the deferred follow-up — must be rejected
    at the service layer until that work lands."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context(), pytest.raises(official_service.OfficialError):
        official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Club Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="club",
            )
        )


# ---------- PR-J14: change visibility on existing races ----------


def test_change_visibility_public_to_private_auto_invites_registrants(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The interesting transition — flipping a Public race that
    already has registrations to Private must keep those users
    able to access the race. We do that by auto-adding them to
    the invitee allowlist."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    alice = make_user(username="alice", password="password123", role=Role.USER)
    bob = make_user(username="bob", password="password123", role=Role.USER)

    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Promote Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="public",
            )
        )
        race_id = race.id
        official_service.open_registration(race_id)
        official_service.register(race_id, alice["id"])
        official_service.register(race_id, bob["id"])

        official_service.change_visibility(
            race_id, new_visibility="private", by_user_id=org["id"]
        )

        invitees = official_service.list_invitees(race_id)
        invitee_user_ids = {inv.user_id for inv in invitees}
        assert alice["id"] in invitee_user_ids
        assert bob["id"] in invitee_user_ids

    # Both can still see the now-Private race.
    _login(client, "alice", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200


def test_change_visibility_private_to_public_opens_gate(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Private→Public just flips the field; nobody's blocked anymore."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    make_user(username="snoop", password="password123", role=Role.USER)

    # Snoop locked out while Private.
    _login(client, "snoop", "password123")
    assert client.get(f"/official/{race_id}").status_code == 404
    client.post("/auth/logout")

    # Organizer flips to Public.
    _login(client, "org", "password123")
    resp = client.post(
        f"/official/{race_id}/visibility",
        data={"visibility": "public"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # Snoop now sees the race.
    client.post("/auth/logout")
    _login(client, "snoop", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200


def test_change_visibility_rejected_on_completed_race(
    app: Flask, make_user
) -> None:
    """Changing a finished race's visibility serves no purpose and
    would rewrite history. Refuse."""
    from uma_ladder.models import OfficialRace
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Done Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
            )
        )
        # Force COMPLETED state; the natural transition involves
        # results submission which is more setup than this test
        # needs.
        db.session.get(OfficialRace, race.id).status = (
            OfficialRaceStatus.COMPLETED
        )
        db.session.commit()
        with pytest.raises(official_service.InvalidRaceStateError):
            official_service.change_visibility(
                race.id,
                new_visibility="private",
                by_user_id=org["id"],
            )


def test_change_visibility_requires_organizer_or_moderator(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Random users cannot flip a race they don't own. Senior
    organizer (moderator) can flip any."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    make_user(username="other_org", password="password123", role=Role.ORGANIZER)

    # Different organizer cannot flip someone else's race.
    _login(client, "other_org", "password123")
    resp = client.post(
        f"/official/{race_id}/visibility",
        data={"visibility": "public"},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    # Senior organizer (moderator) can flip any race.
    client.post("/auth/logout")
    make_user(username="sr", password="password123", role=Role.SENIOR_ORGANIZER)
    _login(client, "sr", "password123")
    resp = client.post(
        f"/official/{race_id}/visibility",
        data={"visibility": "public"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_change_visibility_no_op_on_same_value(
    app: Flask, make_user
) -> None:
    """Flipping to the current value is a no-op (not an error).
    Avoids the form caller having to know what they were."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", role=Role.ORGANIZER)
    race_id = _create_private_race(app, organizer_id=org["id"])
    with app.app_context():
        race = official_service.change_visibility(
            race_id,
            new_visibility="private",
            by_user_id=org["id"],
        )
        assert race.visibility == "private"


# ─── PR-L1 — Club-only matches ──────────────────────────────────


def _set_club_id(app: Flask, user_id: int, club_id: int | None) -> None:
    """Helper: stamp UserProfile.club_id directly. In production this
    is set by services.profiles.sync_club_id_from_trainer when the
    user's profile view fetches a fresh TrainerSummary; tests skip
    the uma.moe roundtrip and write the mirror straight."""
    from uma_ladder.services import profiles as profiles_service

    with app.app_context():
        from uma_ladder.models import User as _User

        user = db.session.get(_User, user_id)
        assert user is not None
        profile = profiles_service.get_or_create_profile(user)
        profile.club_id = club_id
        db.session.commit()


def _create_club_race(app: Flask, *, organizer_id: int, club_id: int = 9001) -> int:
    """Make a CLUB-visibility race for tests. Stamps the organizer's
    UserProfile.club_id first so create_race accepts the visibility."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    _set_club_id(app, organizer_id, club_id)
    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Club Cup",
                organizer_user_id=organizer_id,
                preset_id=pid,
                visibility="club",
            )
        )
        return race.id


def test_create_club_race_requires_organizer_club_id(
    app: Flask, make_user
) -> None:
    """Without a synced club_id the organizer can't create a Club
    race — service surfaces a clean error pointing at profile
    refresh, rather than silently making a race nobody can see."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    # Org has no UserProfile.club_id

    with app.app_context(), pytest.raises(official_service.OfficialError) as exc:
        official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Club Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="club",
            )
        )
    assert "club" in str(exc.value).lower()


def test_create_club_race_succeeds_with_club_id(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"])

    from uma_ladder.services import official as official_service

    with app.app_context():
        race = official_service.get_race(race_id)
        assert race.visibility == "club"


def test_club_race_visible_to_same_club_member(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The whole point of Club-only: a user in the organizer's
    club sees + can register, an outsider cannot."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    same = make_user(username="clubmate", password="password123", role=Role.USER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=42)
    _set_club_id(app, same["id"], 42)

    # Open registration so detail GET is the gate-test target.
    from uma_ladder.services import official as official_service

    with app.app_context():
        official_service.open_registration(race_id)

    _login(client, "clubmate", "password123")
    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    assert b"Club Cup" in resp.data


def test_club_race_invisible_to_different_club_member(
    client: FlaskClient, app: Flask, make_user
) -> None:
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    other = make_user(username="rival", password="password123", role=Role.USER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=42)
    _set_club_id(app, other["id"], 99)  # different club

    _login(client, "rival", "password123")
    assert client.get(f"/official/{race_id}").status_code == 404


def test_club_race_invisible_to_user_with_no_club(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Users with `club_id = None` (no friend code, never synced,
    or genuinely not in any club) can't see Club races. Edge of
    the design — they need to refresh their profile to opt in."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="nobody", password="password123", role=Role.USER)
    race_id = _create_club_race(app, organizer_id=org["id"])
    # nobody.club_id intentionally left None

    _login(client, "nobody", "password123")
    assert client.get(f"/official/{race_id}").status_code == 404


def test_club_race_visible_to_organizer_even_if_club_diverges(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Organizer always sees their own race. Even if their
    UserProfile.club_id later changes (uma.moe reports them in a
    different club), they don't lose access."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=42)
    # Simulate organizer leaving the club they founded the race in
    _set_club_id(app, org["id"], 999)

    _login(client, "org", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200


def test_club_race_visible_to_invitee_outside_club(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Invitees on Club races act as an *override* — useful for
    bringing in an out-of-club coach or friend without flipping
    the race fully open. PR-L1 changed add_invitee to accept Club
    races (was Private-only)."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    coach = make_user(username="coach", password="password123", role=Role.USER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=42)
    _set_club_id(app, coach["id"], 99)  # different club

    with app.app_context():
        official_service.add_invitee(
            race_id,
            invitee_username="coach",
            invited_by_user_id=org["id"],
        )

    _login(client, "coach", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200


def test_club_race_invisible_to_anonymous(
    client: FlaskClient, app: Flask, make_user
) -> None:
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"])
    assert client.get(f"/official/{race_id}").status_code == 404


def test_club_race_visible_to_senior_organizer_moderator(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Senior organizer override extends to Club races for
    consistency with Private."""
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="sr", password="password123", role=Role.SENIOR_ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"])
    _login(client, "sr", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200


def test_change_visibility_public_to_club_promotes_outsider_registrants(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Same protection as Public→Private: registrants who'd lose
    access (because they're not in the organizer's club) get
    auto-promoted to invitees."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    outsider = make_user(username="outsider", password="password123", role=Role.USER)
    _set_club_id(app, org["id"], 42)
    _set_club_id(app, outsider["id"], 99)  # different club

    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Soon-club Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="public",
            )
        )
        race_id = race.id
        official_service.open_registration(race_id)
        official_service.register(race_id, outsider["id"])

        official_service.change_visibility(
            race_id,
            new_visibility="club",
            by_user_id=org["id"],
        )

        invitees = official_service.list_invitees(race_id)
        assert outsider["id"] in {inv.user_id for inv in invitees}

    # Outsider can still see the now-Club race via the invitee
    # override even though they aren't in the organizer's club.
    _login(client, "outsider", "password123")
    assert client.get(f"/official/{race_id}").status_code == 200


def test_index_filters_club_races_by_viewer_club(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Race index hides Club races from users who aren't in the
    organizer's club. Public races still show to everyone."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    same = make_user(username="clubmate", password="password123", role=Role.USER)
    rival = make_user(username="rival", password="password123", role=Role.USER)
    _set_club_id(app, org["id"], 42)
    _set_club_id(app, same["id"], 42)
    _set_club_id(app, rival["id"], 99)

    with app.app_context():
        # One Public, one Club
        official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Open Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="public",
            )
        )
        official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Club Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="club",
            )
        )

    # Same-club user sees both
    _login(client, "clubmate", "password123")
    body = client.get("/official/").data.decode()
    assert "Open Cup" in body
    assert "Club Cup" in body
    client.post("/auth/logout")

    # Rival sees only Public
    _login(client, "rival", "password123")
    body = client.get("/official/").data.decode()
    assert "Open Cup" in body
    assert "Club Cup" not in body


def test_dashboard_upcoming_shows_invited_private_race(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-L2 regression: dashboard upcoming was filtering by Public
    only because list_upcoming_races was called without viewer_user_id.
    Result: invitee never saw a race they were registered for on the
    home page. Fix passes current_user.id when authenticated."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)

    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Invite-Only Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="private",
                scheduled_at=datetime.now(UTC) + timedelta(hours=2),
            )
        )
        race_id = race.id
        official_service.open_registration(race_id)
        official_service.add_invitee(
            race_id, invitee_username="alice", invited_by_user_id=org["id"]
        )

    # Anonymous: Private race must NOT appear (regression-safe).
    body = client.get("/").data.decode()
    assert "Invite-Only Cup" not in body

    # Invitee: race SHOULD appear on the upcoming card.
    _login(client, "alice", "password123")
    body = client.get("/").data.decode()
    assert "Invite-Only Cup" in body


def test_dashboard_upcoming_shows_club_race_to_clubmate(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Same as the Private case — Club races on the dashboard should
    surface to club members and be hidden from outsiders."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    same = make_user(username="clubmate", password="password123", role=Role.USER)
    rival = make_user(username="rival", password="password123", role=Role.USER)
    _set_club_id(app, org["id"], 42)
    _set_club_id(app, same["id"], 42)
    _set_club_id(app, rival["id"], 99)

    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Club-Only Cup",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="club",
                scheduled_at=datetime.now(UTC) + timedelta(hours=2),
            )
        )
        official_service.open_registration(race.id)

    _login(client, "clubmate", "password123")
    body = client.get("/").data.decode()
    assert "Club-Only Cup" in body
    client.post("/auth/logout")

    _login(client, "rival", "password123")
    body = client.get("/").data.decode()
    assert "Club-Only Cup" not in body


def test_sync_club_id_from_trainer_idempotent(app: Flask, make_user) -> None:
    """Service-level helper round-trips circle_id and skips the
    write when the value hasn't changed (avoids commit churn on
    every profile view)."""
    from dataclasses import dataclass

    from uma_ladder.services import profiles as profiles_service

    user = make_user(username="alice", password="password123")

    @dataclass
    class FakeTrainer:
        circle_id: int | None

    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        profile = profiles_service.get_or_create_profile(u)
        assert profile.club_id is None

        profiles_service.sync_club_id_from_trainer(profile, FakeTrainer(42))
        db.session.refresh(profile)
        assert profile.club_id == 42

        # No-op when value matches; we don't have a sentinel, so just
        # assert the value stays as-is.
        profiles_service.sync_club_id_from_trainer(profile, FakeTrainer(42))
        assert profile.club_id == 42

        # Trainer reports None (e.g. user left their club) → mirror clears.
        profiles_service.sync_club_id_from_trainer(profile, FakeTrainer(None))
        assert profile.club_id is None


# ─── PR-O2 — Multi-club race allowlist ──────────────────────────


def _ensure_club(app: Flask, circle_id: int, name: str | None = None) -> None:
    """Insert a Club row directly so the FK target exists for the
    allowlist. In production the service's lazy-create handles this,
    but tests sometimes need the row pre-seeded."""
    from uma_ladder.models import Club

    with app.app_context():
        if db.session.get(Club, circle_id) is None:
            db.session.add(Club(circle_id=circle_id, name=name))
            db.session.commit()


def test_add_allowed_club_lazy_creates_unknown_club(
    app: Flask, make_user
) -> None:
    """Adding a circle_id we've never seen before should create a
    Club row inline — keeps the FK satisfied without forcing the
    organizer to first visit a profile that triggers the sync."""
    from uma_ladder.models import Club
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)

    with app.app_context():
        # Brand-new circle id — not seeded.
        assert db.session.get(Club, 7777) is None
        official_service.add_allowed_club(
            race_id, circle_id=7777, added_by_user_id=org["id"]
        )
        # Lazy-created with NULL name; users can refresh later.
        club = db.session.get(Club, 7777)
        assert club is not None
        assert club.name is None
        # Allowlist row landed.
        rows = official_service.list_allowed_clubs(race_id)
        assert [r.club_circle_id for r in rows] == [7777]


def test_add_allowed_club_is_idempotent(app: Flask, make_user) -> None:
    """Re-adding the same club is a no-op — important because the
    UI's quick-add chip sends a POST every click."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)

    with app.app_context():
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )
        assert len(official_service.list_allowed_clubs(race_id)) == 1


def test_add_allowed_club_rejects_organizer_own_club(
    app: Flask, make_user
) -> None:
    """Organizer's own club is implicit; adding it as an extra
    row would just clutter the UI and confuse the visibility
    semantics."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)

    with app.app_context(), pytest.raises(official_service.OfficialError):
        official_service.add_allowed_club(
            race_id, circle_id=100, added_by_user_id=org["id"]
        )


def test_add_allowed_club_rejects_non_club_race(
    app: Flask, make_user
) -> None:
    """Allowlist is only meaningful on Club-visibility races; a
    Public or Private race can't have one."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Public Race",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="public",
            )
        )
        race_id = race.id
    _ensure_club(app, 200)
    with app.app_context(), pytest.raises(official_service.OfficialError):
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )


def test_remove_allowed_club_removes_visibility(
    app: Flask, make_user
) -> None:
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)

    with app.app_context():
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )
        n = official_service.remove_allowed_club(race_id, circle_id=200)
        assert n == 1
        assert official_service.list_allowed_clubs(race_id) == []
        # Idempotent — second remove returns 0.
        n2 = official_service.remove_allowed_club(race_id, circle_id=200)
        assert n2 == 0


def test_user_in_allowed_club_can_view_race(app: Flask, make_user) -> None:
    """The whole point: a viewer in any allowlisted club should
    pass user_can_view_race, even if they're not in the organizer's
    club."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    ally = make_user(username="ally", password="password123")
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)
    _set_club_id(app, ally["id"], club_id=200)
    with app.app_context():
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )
        race = official_service.get_race(race_id)
        assert official_service.user_can_view_race(
            race, user_id=ally["id"]
        ) is True


def test_user_in_organizer_club_still_passes_after_allowlist_extension(
    app: Flask, make_user
) -> None:
    """Back-compat: PR-L1's "organizer's own club is allowed"
    must still hold after PR-O2's allowlist code runs."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    own_club_member = make_user(username="own_member", password="password123")
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)
    _set_club_id(app, own_club_member["id"], club_id=100)
    # Add an unrelated allied club — must not affect own-club visibility.
    with app.app_context():
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )
        race = official_service.get_race(race_id)
        assert official_service.user_can_view_race(
            race, user_id=own_club_member["id"]
        ) is True


def test_user_in_non_allowed_club_cannot_view_race(
    app: Flask, make_user
) -> None:
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    outsider = make_user(username="outsider", password="password123")
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)
    _set_club_id(app, outsider["id"], club_id=200)
    # Allowlist has 300, NOT 200.
    with app.app_context():
        official_service.add_allowed_club(
            race_id, circle_id=300, added_by_user_id=org["id"]
        )
        race = official_service.get_race(race_id)
        assert official_service.user_can_view_race(
            race, user_id=outsider["id"]
        ) is False


def test_user_with_no_club_cannot_view_club_race(
    app: Flask, make_user
) -> None:
    """A viewer who hasn't synced a club_id (no friend code or no
    club at all) shouldn't see Club races regardless of allowlist
    contents."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    no_club = make_user(username="no_club", password="password123")
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)
    with app.app_context():
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )
        race = official_service.get_race(race_id)
        assert official_service.user_can_view_race(
            race, user_id=no_club["id"]
        ) is False


def test_index_filter_includes_allowlisted_club_member(
    app: Flask, client: FlaskClient, make_user
) -> None:
    """The race index must surface a Club race to users in any
    allowlisted club, not just the organizer's own."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    ally = make_user(username="ally", password="password123")
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)
    _set_club_id(app, ally["id"], club_id=200)
    with app.app_context():
        official_service.add_allowed_club(
            race_id, circle_id=200, added_by_user_id=org["id"]
        )
        race = official_service.get_race(race_id)
        race.status = OfficialRaceStatus.REGISTRATION_OPEN
        db.session.commit()
    _login(client, "ally", "password123")
    body = client.get("/official/").data.decode()
    assert "Club Cup" in body


def test_add_allowed_club_route_requires_organizer(
    app: Flask, client: FlaskClient, make_user
) -> None:
    """Random user posting to the allowlist endpoint should be
    blocked. Not the race's organizer + not senior_organizer+ →
    forbidden."""
    from uma_ladder.services import official as official_service

    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="rando", password="password123")
    race_id = _create_club_race(app, organizer_id=org["id"], club_id=100)
    _login(client, "rando", "password123")
    resp = client.post(
        f"/official/{race_id}/clubs",
        data={"circle_id": "200", "csrf_token": "x"},
        follow_redirects=False,
    )
    # Either 403 (acted-on guard) or 302 (perm decorator blocking).
    assert resp.status_code in (302, 403)
    with app.app_context():
        assert official_service.list_allowed_clubs(race_id) == []


def test_change_visibility_public_to_club_works_with_allowlist_empty(
    app: Flask, make_user
) -> None:
    """Going Public→Club on an existing race should still
    auto-promote registrants from non-allowed clubs to invitees
    (PR-L1 behaviour). The allowlist starts empty when the race
    transitions, so the existing logic doesn't need updating —
    this test guards against accidental regression."""
    from uma_ladder.services import official as official_service

    sid = _make_season(app)
    pid = _make_preset(app)
    org = make_user(username="org", password="password123", role=Role.ORGANIZER)
    racer = make_user(username="racer", password="password123")
    _set_club_id(app, org["id"], club_id=100)
    _set_club_id(app, racer["id"], club_id=999)  # different club
    with app.app_context():
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=sid,
                name="Going Club",
                organizer_user_id=org["id"],
                preset_id=pid,
                visibility="public",
            )
        )
        race.status = OfficialRaceStatus.REGISTRATION_OPEN
        db.session.commit()
        race_id = race.id
        official_service.register(race_id, user_id=racer["id"])
        official_service.change_visibility(
            race_id, new_visibility="club", by_user_id=org["id"]
        )
        # racer's club != 100 → should be on invitee list now.
        invitees = official_service.list_invitees(race_id)
        assert any(inv.user_id == racer["id"] for inv in invitees)


def test_organizer_can_step_back_from_room_code(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """A code pasted too early closes registration as a side effect;
    the step-back clears it and re-opens registration."""
    sid = _make_season(app)
    pid = _make_preset(app)
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "Early", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post(f"/official/{race_id}/room-code", data={"room_code": "OOPS-1"})

    resp = client.post(f"/official/{race_id}/revert-to-registration")
    assert resp.status_code == 302

    from uma_ladder.models import OfficialRace

    with app.app_context():
        race = db.session.get(OfficialRace, race_id)
        assert race.status == "registration_open"
        assert race.room_code is None
        assert race.room_code_expires_at is None
