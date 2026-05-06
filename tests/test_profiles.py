from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import UmaCharacter
from uma_ladder.services import profiles as profiles_service


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _make_character(app: Flask, slug: str = "special-week") -> int:
    with app.app_context():
        c = UmaCharacter(slug=slug, name_en=slug.replace("-", " ").title())
        db.session.add(c)
        db.session.commit()
        return c.id


def test_me_requires_login(client: FlaskClient) -> None:
    resp = client.get("/profiles/me", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_owner_can_edit_own_profile(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", password="password123")
    oshi_id = _make_character(app)
    _login(client, "alice", "password123")

    resp = client.post(
        "/profiles/me",
        data={
            "display_name": "Alice in Wonderland",
            "friend_code": "1234-5678",
            "description": "Hello there",
            "oshi_character_id": str(oshi_id),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        assert user is not None
        profile = profiles_service.get_or_create_profile(user)
        assert profile.display_name == "Alice in Wonderland"
        assert profile.friend_code == "1234-5678"
        assert profile.oshi_character_id == oshi_id


def test_unknown_oshi_is_rejected(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.post(
        "/profiles/me",
        data={"oshi_character_id": "9999"},
    )
    assert resp.status_code == 200
    assert b"Unknown character." in resp.data


def test_public_profile_renders(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", password="password123")
    oshi_id = _make_character(app, "gold-ship")
    _login(client, "alice", "password123")
    client.post(
        "/profiles/me",
        data={
            "display_name": "Alice",
            "description": "Bio body",
            "oshi_character_id": str(oshi_id),
        },
    )
    client.post("/auth/logout")

    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    assert b"Alice" in resp.data
    assert b"Bio body" in resp.data
    assert b"Gold Ship" in resp.data


def test_public_profile_404_for_unknown(client: FlaskClient) -> None:
    resp = client.get("/profiles/ghost")
    assert resp.status_code == 404


def test_get_or_create_profile_idempotent(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        user = profiles_service.find_user_by_username(info["username"])
        assert user is not None
        a = profiles_service.get_or_create_profile(user)
        b = profiles_service.get_or_create_profile(user)
        assert a.id == b.id


def test_update_profile_unknown_oshi_raises(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        user = profiles_service.find_user_by_username(info["username"])
        assert user is not None
        with pytest.raises(profiles_service.UnknownOshiError):
            profiles_service.update_profile(
                user, profiles_service.ProfileUpdate(oshi_character_id=9999)
            )


def test_nav_points_to_public_profile_not_edit(
    client: FlaskClient, make_user
) -> None:
    """Clicking the user's own name in the nav opens the public profile
    page, not the edit form. The edit form is reachable from the
    page's Edit affordance."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/profiles/alice"' in body
    assert 'href="/profiles/me"' not in body


def test_public_profile_shows_edit_button_for_owner(
    client: FlaskClient, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Edit profile" in body
    assert 'href="/profiles/me"' in body


def test_public_profile_hides_edit_button_for_other_users(
    client: FlaskClient, make_user
) -> None:
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")
    _login(client, "bob", "password123")
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Edit profile" not in body
