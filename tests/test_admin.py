"""Admin UI: user management + draft match management. Player-facing
dashboards stay scoped to the user's own activity — cross-user views
live only under /admin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftMatch,
    DraftMatchStatus,
    Season,
    SeasonStatus,
    User,
)
from uma_ladder.models.users import Role
from uma_ladder.services import admin as admin_service
from uma_ladder.services import draft as draft_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Service-level self-protection rules. Tested without HTTP for speed.


def test_change_role_blocks_self_edit(app: Flask, make_user) -> None:
    actor = make_user(username="admin1", role=Role.ADMIN)
    with app.app_context():
        u = db.session.get(User, actor["id"])
        with pytest.raises(admin_service.CannotEditSelfError):
            admin_service.change_user_role(actor=u, target=u, new_role=Role.USER)


def test_admin_cannot_edit_other_admin(app: Flask, make_user) -> None:
    actor = make_user(username="admin1", role=Role.ADMIN)
    target = make_user(username="admin2", role=Role.ADMIN)
    with app.app_context():
        a = db.session.get(User, actor["id"])
        t = db.session.get(User, target["id"])
        with pytest.raises(admin_service.InsufficientRankError):
            admin_service.change_user_role(actor=a, target=t, new_role=Role.USER)


def test_admin_cannot_promote_to_admin(app: Flask, make_user) -> None:
    actor = make_user(username="admin1", role=Role.ADMIN)
    target = make_user(username="user1", role=Role.USER)
    with app.app_context():
        a = db.session.get(User, actor["id"])
        t = db.session.get(User, target["id"])
        with pytest.raises(admin_service.InsufficientRankError):
            admin_service.change_user_role(actor=a, target=t, new_role=Role.ADMIN)


def test_superadmin_can_edit_admin(app: Flask, make_user) -> None:
    actor = make_user(username="super", role=Role.SUPERADMIN)
    target = make_user(username="someadmin", role=Role.ADMIN)
    with app.app_context():
        a = db.session.get(User, actor["id"])
        t = db.session.get(User, target["id"])
        admin_service.change_user_role(actor=a, target=t, new_role=Role.USER)
        assert db.session.get(User, target["id"]).role == Role.USER


def test_last_superadmin_cannot_be_demoted(app: Flask, make_user) -> None:
    """If only one superadmin exists, demoting them must fail — even by
    another superadmin. Self-edit and rank rules already make this
    unreachable through HTTP, but the count-based guard protects against
    forged calls (CLI, internal automation, future bugs)."""
    only_super = make_user(username="lone", role=Role.SUPERADMIN)
    helper = make_user(username="helper", role=Role.ADMIN)
    with app.app_context():
        target = db.session.get(User, only_super["id"])
        actor = db.session.get(User, helper["id"])
        # Forge a superadmin actor without committing the change to the
        # DB — expunge from session so the role mutation stays in-memory.
        # Otherwise the service's commit() would persist the forge,
        # creating a second real superadmin and defeating the test.
        db.session.expunge(actor)
        actor.role = Role.SUPERADMIN
        with pytest.raises(admin_service.LastSuperadminError):
            admin_service.change_user_role(
                actor=actor, target=target, new_role=Role.ADMIN
            )


# ---------------------------------------------------------------------------
# HTTP layer.


def test_non_admin_cannot_access_admin(client: FlaskClient, make_user) -> None:
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    for path in ("/admin/", "/admin/users", "/admin/matches"):
        resp = client.get(path)
        assert resp.status_code == 403, f"{path} should be 403 for non-admin"


def test_admin_can_view_index(client: FlaskClient, make_user) -> None:
    make_user(username="adm", role=Role.ADMIN)
    _login(client, "adm")
    resp = client.get("/admin/")
    assert resp.status_code == 200
    assert b"Control room" in resp.data


def test_admin_users_list_search(client: FlaskClient, make_user) -> None:
    make_user(username="adm", role=Role.ADMIN)
    make_user(username="findme", role=Role.USER)
    make_user(username="other", role=Role.USER)
    _login(client, "adm")
    resp = client.get("/admin/users?q=findme")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "@findme" in body
    assert "@other" not in body


def test_admin_can_change_role(client: FlaskClient, app: Flask, make_user) -> None:
    adm = make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    _login(client, "adm")
    resp = client.post(
        f"/admin/users/{target['id']}/role",
        data={"role": Role.ORGANIZER.value},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(User, target["id"]).role == Role.ORGANIZER
    # Adm cannot self-edit — flash + role unchanged.
    resp = client.post(
        f"/admin/users/{adm['id']}/role",
        data={"role": Role.USER.value},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(User, adm["id"]).role == Role.ADMIN


def test_admin_matches_list_filter_and_cancel(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    host = make_user(username="hostie", role=Role.USER)
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
        m = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        match_id = m.id

    _login(client, "adm")
    resp = client.get("/admin/matches")
    assert resp.status_code == 200
    assert b"@hostie" in resp.data
    # Filter by status
    resp = client.get(
        f"/admin/matches?status={DraftMatchStatus.WAITING_FOR_OPPONENT.value}"
    )
    assert resp.status_code == 200
    assert b"@hostie" in resp.data
    # Filter by impossible status — match should not appear.
    resp = client.get(f"/admin/matches?status={DraftMatchStatus.COMPLETED.value}")
    assert resp.status_code == 200
    assert b"@hostie" not in resp.data
    # Cancel inline
    resp = client.post(
        f"/admin/matches/{match_id}/cancel", follow_redirects=False
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(DraftMatch, match_id).status == DraftMatchStatus.CANCELLED


# ---------- PR-J11 — admin-issued password reset ----------


def test_issue_password_reset_url_round_trip(
    app: Flask, make_user
) -> None:
    """Service generates a URL, the token in it is consumable, and
    the audit log records the action."""
    from uma_ladder.models import AdminAuditLog
    from uma_ladder.services import auth as auth_service

    actor = make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER, password="oldpass1234")
    with app.app_context():
        a = db.session.get(User, actor["id"])
        t = db.session.get(User, target["id"])
        url = admin_service.issue_password_reset_url(actor=a, target=t)
        # URL anatomy: ends in /auth/reset/<token>.
        assert "/auth/reset/" in url
        token = url.rsplit("/", 1)[-1]
        # Token consumes back to the right user.
        loaded = auth_service.consume_reset_token(
            app.config["SECRET_KEY"], token
        )
        assert loaded.id == t.id
        # Audit row written.
        rows = list(
            db.session.scalars(
                db.select(AdminAuditLog).where(
                    AdminAuditLog.action == "password_reset_issued"
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].actor_user_id == a.id
        assert rows[0].target_user_id == t.id


def test_issue_password_reset_blocks_admin_resetting_admin(
    app: Flask, make_user
) -> None:
    """Same rank-protection as change_user_role — a regular admin
    can't generate a reset for another admin (or higher). Only
    superadmins can."""
    actor = make_user(username="adm1", role=Role.ADMIN)
    target = make_user(username="adm2", role=Role.ADMIN)
    with app.app_context():
        a = db.session.get(User, actor["id"])
        t = db.session.get(User, target["id"])
        with pytest.raises(admin_service.InsufficientRankError):
            admin_service.issue_password_reset_url(actor=a, target=t)


def test_superadmin_can_reset_admin(app: Flask, make_user) -> None:
    actor = make_user(username="root", role=Role.SUPERADMIN)
    target = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        a = db.session.get(User, actor["id"])
        t = db.session.get(User, target["id"])
        url = admin_service.issue_password_reset_url(actor=a, target=t)
        assert "/auth/reset/" in url


def test_admin_route_renders_url_inline(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The route must render the URL on the same page (no
    redirect-after-POST) so the sensitive token doesn't end up in
    a browser-history Location header."""
    make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    _login(client, "adm")
    resp = client.post(
        f"/admin/users/{target['id']}/reset-password",
        follow_redirects=False,
    )
    # No redirect — page is rendered directly with the URL on it.
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "/auth/reset/" in body
    assert "Generated" in body
    # Audit hint visible — admin sees what they're doing.
    assert "shown only once" in body


def test_admin_route_requires_admin_rank(
    client: FlaskClient, make_user
) -> None:
    """Plain users + organizers can't issue resets."""
    make_user(username="bob", role=Role.USER, password="x")
    target = make_user(username="alice", role=Role.USER)
    _login(client, "bob", password="x")
    resp = client.post(
        f"/admin/users/{target['id']}/reset-password",
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_admin_route_full_recovery_round_trip(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """End-to-end: admin generates URL, target visits it, sets new
    password, logs in with the new password."""
    import re

    make_user(username="adm", role=Role.ADMIN)
    make_user(username="bob", role=Role.USER, password="oldpass1234")

    _login(client, "adm")
    target_id = None
    with app.app_context():
        target_id = db.session.scalar(
            db.select(User.id).where(User.username == "bob")
        )

    resp = client.post(
        f"/admin/users/{target_id}/reset-password",
        follow_redirects=False,
    )
    assert resp.status_code == 200
    # Pull the URL out of the rendered card.
    match = re.search(r"/auth/reset/([A-Za-z0-9._\-]+)", resp.data.decode())
    assert match, "reset URL not in admin response body"
    token = match.group(1)

    # Switch to bob's perspective: visit reset page + set new pw.
    client.post("/auth/logout")
    resp = client.post(
        f"/auth/reset/{token}",
        data={"password": "newpass5678", "confirm": "newpass5678"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Old password no longer works.
    bad = client.post(
        "/auth/login",
        data={"username": "bob", "password": "oldpass1234"},
    )
    assert b"Invalid username or password" in bad.data
    # New password works.
    good = client.post(
        "/auth/login",
        data={"username": "bob", "password": "newpass5678"},
        follow_redirects=False,
    )
    assert good.status_code == 302
