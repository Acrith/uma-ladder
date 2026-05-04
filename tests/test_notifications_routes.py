from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DiscordNotificationAttempt,
    NotificationStatus,
    Role,
)
from uma_ladder.notifications.discord import FakeTransport, set_transport


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _seed_attempt(app: Flask, status: str = NotificationStatus.FAILED) -> int:
    with app.app_context():
        a = DiscordNotificationAttempt(
            event_type="test",
            target_name="race_registration",
            payload_json={"hello": "world"},
            status=status,
            error_message="boom",
        )
        db.session.add(a)
        db.session.commit()
        return a.id


def test_index_requires_admin(client: FlaskClient, app: Flask, make_user) -> None:
    # anon → 401
    resp = client.get("/notifications/")
    assert resp.status_code == 401

    # regular user → 403
    make_user(username="alice", password="password123", role=Role.USER)
    _login(client, "alice", "password123")
    resp = client.get("/notifications/")
    assert resp.status_code == 403


def test_admin_sees_attempts(client: FlaskClient, app: Flask, make_user) -> None:
    _seed_attempt(app)
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")
    resp = client.get("/notifications/")
    assert resp.status_code == 200
    assert b"failed" in resp.data
    assert b"Retry" in resp.data


def test_retry_button_resends(client: FlaskClient, app: Flask, make_user) -> None:
    aid = _seed_attempt(app)
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        set_transport(FakeTransport())

    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")
    resp = client.post(f"/notifications/{aid}/retry", follow_redirects=False)
    assert resp.status_code == 302
    with app.app_context():
        a = db.session.get(DiscordNotificationAttempt, aid)
        assert a is not None
        assert a.status == NotificationStatus.SENT
