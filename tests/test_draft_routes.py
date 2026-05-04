from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import RacePreset, Season, SeasonStatus
from uma_ladder.models.enums import PresetSource


def _season(app: Flask) -> int:
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S1",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=89),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


def _add_preset(app: Flask) -> None:
    with app.app_context():
        db.session.add(
            RacePreset(
                source=PresetSource.CUSTOM_BUILTIN,
                name="Sapporo Turf 2000m (Medium) Right",
                venue="Sapporo",
                surface="Turf",
                distance_meters=2000,
                distance_category="Medium",
                direction="Right",
                course_variant=None,
                max_runners=16,
                enabled=True,
            )
        )
        db.session.commit()


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_index_anonymous_renders(client: FlaskClient) -> None:
    resp = client.get("/draft/")
    assert resp.status_code == 200


def test_create_requires_login(client: FlaskClient, app: Flask) -> None:
    _season(app)
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_create_with_no_active_season_redirects_with_flash(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new", data={"umas_per_player": 2, "preset_pool": "custom"}
    )
    assert resp.status_code == 302
    assert "/draft/" in resp.headers["Location"]


def test_full_match_flow_via_routes(client: FlaskClient, app: Flask, make_user) -> None:
    _season(app)
    _add_preset(app)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    # Capture join code
    from uma_ladder.models import DraftMatch

    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        assert match is not None
        join_code = match.join_code

    # bob joins
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.post(
        "/draft/join",
        data={"join_code": join_code},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # both submit umas + ready
    for username in ("alice", "bob"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        resp = client.post(
            f"/draft/{match_id}/umas",
            data={
                "uma_character_id_0": "",
                "custom_uma_name_0": f"{username}-uma1",
                "build_nickname_0": "",
                "uma_character_id_1": "",
                "custom_uma_name_1": f"{username}-uma2",
                "build_nickname_1": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        resp = client.post(f"/draft/{match_id}/ready", follow_redirects=False)
        assert resp.status_code == 302

    # both submit bans
    from uma_ladder.models import DraftMatchUmaEntry

    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        host_uid = match.host_user_id
        opp_uid = match.opponent_user_id
        entries = (
            db.session.query(DraftMatchUmaEntry)
            .filter_by(draft_match_id=match_id)
            .order_by(DraftMatchUmaEntry.id)
            .all()
        )
        host_entries = [e for e in entries if e.user_id == host_uid]
        opp_entries = [e for e in entries if e.user_id == opp_uid]

    # currently logged in as bob; bob is opp
    resp = client.post(
        f"/draft/{match_id}/bans",
        data={
            "banned_uma_entry_id": str(host_entries[0].id),
            "track_ban_type": "direction",
            "track_condition_key": "Left",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    client.post("/auth/logout")
    _login(client, "alice", "password123")
    resp = client.post(
        f"/draft/{match_id}/bans",
        data={
            "banned_uma_entry_id": str(opp_entries[0].id),
            "track_ban_type": "venue",
            "track_condition_key": "Tokyo",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # randomize → room-code → results
    resp = client.post(f"/draft/{match_id}/randomize", follow_redirects=False)
    assert resp.status_code == 302

    resp = client.post(
        f"/draft/{match_id}/room-code",
        data={"room_code": "RC-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    resp = client.post(
        f"/draft/{match_id}/results",
        data={
            f"placement_{host_uid}": "1",
            f"placement_{opp_uid}": "2",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # ladder shows alice (host_uid) on top
    season_id = _season_for(match_id, app)
    resp = client.get(f"/draft/ladder/{season_id}")
    assert resp.status_code == 200
    body = resp.data
    assert body.index(b"alice") < body.index(b"bob")


def _season_for(match_id: int, app: Flask) -> int:
    from uma_ladder.models import DraftMatch

    with app.app_context():
        m = db.session.get(DraftMatch, match_id)
        assert m is not None
        return m.season_id


def test_dashboard_includes_elo_block(client: FlaskClient, app: Flask) -> None:
    _season(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Draft Elo" in resp.data
