from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    RacePreset,
    Season,
    SeasonStatus,
    UmaCharacter,
)
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
    """Seed a small variety of presets so bans during the test don't all
    fall foul of the feasibility check (each ban must remove >=1 preset
    and leave >=1)."""
    with app.app_context():
        rows = [
            ("Sapporo Right", "Sapporo", "Right"),
            ("Tokyo Left", "Tokyo", "Left"),
            ("Hakodate Left", "Hakodate", "Left"),
            ("Niigata Right", "Niigata", "Right"),
        ]
        for name, venue, direction in rows:
            db.session.add(
                RacePreset(
                    source=PresetSource.CUSTOM_BUILTIN,
                    name=name,
                    venue=venue,
                    surface="Turf",
                    distance_meters=2000,
                    distance_category="Medium",
                    direction=direction,
                    course_variant=None,
                    max_runners=18,
                    enabled=True,
                )
            )
        db.session.commit()


def _add_characters(app: Flask) -> tuple[int, int, int]:
    with app.app_context():
        chars = [
            UmaCharacter(slug="char-a", name_en="Char A"),
            UmaCharacter(slug="char-b", name_en="Char B"),
            UmaCharacter(slug="char-c", name_en="Char C"),
        ]
        db.session.add_all(chars)
        db.session.commit()
        return chars[0].id, chars[1].id, chars[2].id


def _add_outfits(app: Flask, *char_ids: int) -> dict[int, int]:
    """Give each character a single default costume so it can be banned."""
    from uma_ladder.models import UmaOutfit

    with app.app_context():
        out = {}
        for cid in char_ids:
            o = UmaOutfit(
                uma_character_id=cid,
                costume_id=cid * 100 + 1,
                title_en=f"costume {cid}",
                released_globally=True,
                enabled=True,
            )
            db.session.add(o)
            db.session.commit()
            out[cid] = o.id
        return out


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


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
    resp = client.post("/draft/new", data={"umas_per_player": 2, "preset_pool": "custom"})
    assert resp.status_code == 302
    assert "/draft/" in resp.headers["Location"]


def test_full_match_flow_via_routes(client: FlaskClient, app: Flask, make_user) -> None:
    _season(app)
    _add_preset(app)
    char_a, char_b, char_c = _add_characters(app)
    outfits = _add_outfits(app, char_a, char_b, char_c)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    # alice creates
    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        join_code = match.join_code

    # bob joins
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.post("/draft/join", data={"join_code": join_code}, follow_redirects=False)
    assert resp.status_code == 302

    # both ready
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        resp = client.post(f"/draft/{match_id}/ready", follow_redirects=False)
        assert resp.status_code == 302

    # both submit track bans (alice currently logged in)
    resp = client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "venue", "condition_key": "Tokyo"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "direction", "condition_key": "Left"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # randomize
    resp = client.post(f"/draft/{match_id}/randomize", follow_redirects=False)
    assert resp.status_code == 302

    # capture host/opp ids
    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        host_uid = match.host_user_id
        opp_uid = match.opponent_user_id

    # uma bans (currently logged in as bob); each ban targets a specific costume
    resp = client.post(
        f"/draft/{match_id}/uma-ban",
        data={
            "uma_character_id": str(char_a),
            "uma_outfit_id": str(outfits[char_a]),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    resp = client.post(
        f"/draft/{match_id}/uma-ban",
        data={
            "uma_character_id": str(char_b),
            "uma_outfit_id": str(outfits[char_b]),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # host pastes room code
    resp = client.post(
        f"/draft/{match_id}/room-code",
        data={"room_code": "RC-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # results
    resp = client.post(
        f"/draft/{match_id}/results",
        data={
            f"placement_{host_uid}": "1",
            f"placement_{opp_uid}": "2",
            f"uma_character_id_{host_uid}": str(char_c),
            f"uma_character_id_{opp_uid}": "",
            f"custom_uma_name_{opp_uid}": "Some Uma",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # ladder shows alice (host) on top
    season_id = _season_for(match_id, app)
    resp = client.get(f"/draft/ladder/{season_id}")
    assert resp.status_code == 200
    body = resp.data
    assert body.index(b"alice") < body.index(b"bob")


def _season_for(match_id: int, app: Flask) -> int:
    from uma_ladder.models import DraftMatch

    with app.app_context():
        m = db.session.get(DraftMatch, match_id)
        return m.season_id


def test_dashboard_includes_elo_block(client: FlaskClient, app: Flask) -> None:
    _season(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Draft Elo" in resp.data
