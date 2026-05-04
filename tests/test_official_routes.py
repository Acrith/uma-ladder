from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    OfficialRaceStatus,
    Role,
    Season,
    SeasonStatus,
)


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
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    make_user(username="alice", password="password123", role=Role.USER)
    make_user(username="bob", password="password123", role=Role.USER)

    # organizer creates race
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "Spring G1"},
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
    make_user(username="org", password="password123", role=Role.ORGANIZER)
    _login(client, "org", "password123")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R"},
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
