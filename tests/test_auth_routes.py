from __future__ import annotations

import pytest
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


# ---------- PR-J10 — login lockout shown in the form ----------


def test_login_form_surfaces_cooldown_message(
    client: FlaskClient,
) -> None:
    """After MAX_FAILED_LOGINS wrong attempts, the form must show a
    "try again in N minute(s)" message — the user otherwise sees
    the same generic InvalidCredentials error and has no idea why
    correct credentials suddenly stop working."""
    from uma_ladder.services import auth as auth_service

    _register(client, "alice", "password123")
    client.post("/auth/logout")

    for _ in range(auth_service.MAX_FAILED_LOGINS):
        client.post(
            "/auth/login",
            data={"username": "alice", "password": "WRONG"},
        )

    resp = client.post(
        "/auth/login",
        data={"username": "alice", "password": "password123"},
    )
    assert resp.status_code == 200
    body = resp.data.decode().lower()
    assert "too many failed attempts" in body
    assert "minute" in body


# ---------- PR-J8: open-redirect gate on /auth/login?next= ----------


def test_login_honours_safe_relative_next(client: FlaskClient) -> None:
    """Legitimate same-origin relative `next` paths must still
    work — that's the whole reason the param exists."""
    _register(client)
    client.post("/auth/logout")

    resp = client.post(
        "/auth/login?next=/draft/123",
        data={"username": "alice", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/draft/123")


@pytest.mark.parametrize(
    "evil_next",
    [
        "https://evil.com/phish",
        "http://evil.com/phish",
        "//evil.com/phish",
        "/\\evil.com/phish",
        "javascript:alert(1)",
    ],
)
def test_login_rejects_external_next_target(
    client: FlaskClient, evil_next: str
) -> None:
    """Crafted `next` values must not bounce a freshly-authed
    user out of the trust boundary. Falls back to the dashboard."""
    _register(client)
    client.post("/auth/logout")

    resp = client.post(
        f"/auth/login?next={evil_next}",
        data={"username": "alice", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "evil.com" not in location
    assert "javascript" not in location
    # Default target is the dashboard.
    assert location.endswith("/") or "/dashboard" in location


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
