"""Players browse page (/profiles/) + audit that usernames render as
profile links across the key view templates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftMatchStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import draft as draft_service
from uma_ladder.services import official as official_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------- Players browse ----------


def test_players_index_lists_users(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", role=Role.USER)
    make_user(username="bob", role=Role.USER)
    resp = client.get("/profiles/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "@alice" in body
    assert "@bob" in body
    # Each row links to the public profile.
    assert 'href="/profiles/alice"' in body
    assert 'href="/profiles/bob"' in body


def test_players_index_search_filters(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", role=Role.USER)
    make_user(username="bob", role=Role.USER)
    resp = client.get("/profiles/?q=alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "@alice" in body
    assert "@bob" not in body


def test_players_index_paginates(
    client: FlaskClient, app: Flask, make_user
) -> None:
    for i in range(35):
        make_user(username=f"u{i:02d}", role=Role.USER)
    resp = client.get("/profiles/?page=1")
    assert resp.status_code == 200
    assert b"Page 1 / 2" in resp.data


def test_players_index_no_match_message(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", role=Role.USER)
    resp = client.get("/profiles/?q=zzz")
    assert resp.status_code == 200
    assert b"No players match" in resp.data


# ---------- Clickable usernames in key contexts ----------


def test_draft_detail_player_panel_links_to_profile(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="hostie", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        match.opponent_user_id = opp["id"]
        match.status = DraftMatchStatus.READY_CHECK
        db.session.commit()
        match_id = match.id

    _login(client, "hostie")
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/profiles/hostie"' in body
    assert 'href="/profiles/opp"' in body


def test_official_detail_registrations_link_to_profile(
    client: FlaskClient, app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    alice = make_user(username="alice", role=Role.USER)
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
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
        db.session.add_all([s, p])
        db.session.commit()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id,
                organizer_user_id=org["id"],
                name="R",
                preset_id=p.id,
                max_players=12,
                notes=None,
            )
        )
        race_id = race.id
        official_service.open_registration(race_id)
        official_service.register(race_id, alice["id"])

    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/profiles/alice"' in body


def test_admin_user_detail_has_view_public_profile_link(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    _login(client, "adm")
    resp = client.get(f"/admin/users/{target['id']}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/profiles/bob"' in body
    assert "View public profile" in body


def test_admin_users_list_links_to_public_profile(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    make_user(username="alice", role=Role.USER)
    _login(client, "adm")
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/profiles/alice"' in body


def test_admin_matches_list_links_host_and_opponent(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    host = make_user(username="hostie", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        match.opponent_user_id = opp["id"]
        db.session.commit()

    _login(client, "adm")
    resp = client.get("/admin/matches")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/profiles/hostie"' in body
    assert 'href="/profiles/opp"' in body
