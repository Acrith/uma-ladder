from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.models import Role
from uma_ladder.services.permissions import min_role_required, role_required


def _attach_protected_routes(app: Flask) -> None:
    @app.get("/_test/admin-only")
    @role_required(Role.ADMIN, Role.SUPERADMIN)
    def admin_only() -> str:
        return "ok"

    @app.get("/_test/at-least-organizer")
    @min_role_required(Role.ORGANIZER)
    def at_least_organizer() -> str:
        return "ok"


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_role_required_anonymous_gets_401(client: FlaskClient, app: Flask) -> None:
    _attach_protected_routes(app)
    resp = client.get("/_test/admin-only")
    assert resp.status_code == 401


def test_role_required_wrong_role_gets_403(client: FlaskClient, app: Flask, make_user) -> None:
    _attach_protected_routes(app)
    make_user(username="bob", password="password123", role=Role.USER)
    _login(client, "bob", "password123")
    resp = client.get("/_test/admin-only")
    assert resp.status_code == 403


def test_role_required_allowed_role_passes(client: FlaskClient, app: Flask, make_user) -> None:
    _attach_protected_routes(app)
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")
    resp = client.get("/_test/admin-only")
    assert resp.status_code == 200


def test_min_role_required_allows_higher_role(client: FlaskClient, app: Flask, make_user) -> None:
    _attach_protected_routes(app)
    make_user(username="ed", password="password123", role=Role.SENIOR_ORGANIZER)
    _login(client, "ed", "password123")
    resp = client.get("/_test/at-least-organizer")
    assert resp.status_code == 200


def test_min_role_required_rejects_lower_role(client: FlaskClient, app: Flask, make_user) -> None:
    _attach_protected_routes(app)
    make_user(username="u", password="password123", role=Role.USER)
    _login(client, "u", "password123")
    resp = client.get("/_test/at-least-organizer")
    assert resp.status_code == 403
