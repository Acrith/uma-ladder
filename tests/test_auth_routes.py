from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient


def _register(client: FlaskClient, username: str = "alice", password: str = "password123"):
    return client.post(
        "/auth/register",
        data={"username": username, "password": password, "confirm": password},
        follow_redirects=False,
    )


def test_register_logs_user_in_and_redirects(client: FlaskClient) -> None:
    resp = _register(client)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")

    # follow-up request authenticated → logout works
    resp2 = client.post("/auth/logout", follow_redirects=False)
    assert resp2.status_code == 302


def test_register_rejects_short_password(client: FlaskClient) -> None:
    resp = client.post(
        "/auth/register",
        data={"username": "alice", "password": "abc", "confirm": "abc"},
    )
    assert resp.status_code == 200
    assert b"Field must be" in resp.data or b"at least" in resp.data.lower()


def test_register_rejects_duplicate_username(client: FlaskClient) -> None:
    _register(client)
    client.post("/auth/logout")
    resp = _register(client)
    assert resp.status_code == 200
    assert b"already taken" in resp.data


def test_login_logout_cycle(client: FlaskClient) -> None:
    _register(client)
    client.post("/auth/logout")

    bad = client.post(
        "/auth/login", data={"username": "alice", "password": "WRONG"}
    )
    assert bad.status_code == 200
    assert b"Invalid username or password" in bad.data

    good = client.post(
        "/auth/login",
        data={"username": "alice", "password": "password123"},
        follow_redirects=False,
    )
    assert good.status_code == 302


def test_logout_requires_login(client: FlaskClient) -> None:
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_password_reset_flow(client: FlaskClient, app: Flask) -> None:
    _register(client)
    client.post("/auth/logout")

    from uma_ladder.services import auth as auth_service

    with app.app_context():
        user = auth_service.find_user_by_username("alice")
        assert user is not None
        token = auth_service.issue_reset_token(app.config["SECRET_KEY"], user)

    resp = client.post(
        f"/auth/reset/{token}",
        data={"password": "newpassword99", "confirm": "newpassword99"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # old password rejected
    bad = client.post(
        "/auth/login", data={"username": "alice", "password": "password123"}
    )
    assert b"Invalid username or password" in bad.data

    # new password works
    good = client.post(
        "/auth/login",
        data={"username": "alice", "password": "newpassword99"},
        follow_redirects=False,
    )
    assert good.status_code == 302


def test_password_reset_invalid_token(client: FlaskClient) -> None:
    resp = client.get("/auth/reset/not-a-real-token")
    assert resp.status_code == 400
