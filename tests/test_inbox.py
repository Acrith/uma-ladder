"""PR-J12 — in-app notification inbox.

Service-level + integration tests. Covers: creation, list ordering,
unread count, mark-on-view, navbar bell badge, fan-out from
invite_to_match, cleanup on accept/decline/cancel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftMatch,
    DraftMatchStatus,
    Role,
    Season,
    SeasonStatus,
    User,
    UserNotification,
)
from uma_ladder.services import inbox as inbox_service


def _seed_match(
    app: Flask, host_id: int, opp_id: int | None = None
) -> int:
    with app.app_context():
        s = db.session.query(Season).first()
        if s is None:
            now = datetime.now(UTC)
            s = Season(
                name="S",
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=10),
                status=SeasonStatus.ACTIVE,
            )
            db.session.add(s)
            db.session.commit()
        m = DraftMatch(
            season_id=s.id,
            host_user_id=host_id,
            opponent_user_id=opp_id,
            join_code=f"INV{host_id}-{opp_id or 0}",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.WAITING_FOR_OPPONENT,
        )
        db.session.add(m)
        db.session.commit()
        return m.id


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------- service ----------


def test_create_then_list_for_user(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="x")
    with app.app_context():
        inbox_service.create(
            user_id=info["id"],
            kind="draft_invite",
            payload={"match_id": 7, "inviter_username": "bob"},
        )
        inbox_service.create(
            user_id=info["id"],
            kind="draft_invite",
            payload={"match_id": 8, "inviter_username": "carol"},
        )
        rows = inbox_service.list_for_user(info["id"])
        assert len(rows) == 2
        # Newest first.
        assert rows[0].payload_json["match_id"] == 8


def test_unread_count_and_mark_all_read(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="x")
    with app.app_context():
        for i in range(3):
            inbox_service.create(
                user_id=info["id"],
                kind="draft_invite",
                payload={"match_id": i, "inviter_username": "bob"},
            )
        assert inbox_service.unread_count_for_user(info["id"]) == 3
        marked = inbox_service.mark_all_read_for_user(info["id"])
        assert marked == 3
        assert inbox_service.unread_count_for_user(info["id"]) == 0
        # Idempotent — running again touches nothing.
        assert inbox_service.mark_all_read_for_user(info["id"]) == 0


def test_unread_count_isolated_per_user(app: Flask, make_user) -> None:
    a = make_user(username="alice", password="x")
    b = make_user(username="bob", password="x")
    with app.app_context():
        inbox_service.create(
            user_id=a["id"], kind="draft_invite", payload={}
        )
        assert inbox_service.unread_count_for_user(a["id"]) == 1
        assert inbox_service.unread_count_for_user(b["id"]) == 0


# ---------- fan-out from draft service ----------


def test_invite_to_match_creates_inbox_notification(
    app: Flask, make_user
) -> None:
    """Original gap from PR-J5 audit: invite_to_match was silent.
    With J12 wired in, the invitee gets an inbox row."""
    from uma_ladder.services import draft as draft_service

    host = make_user(username="alice", password="x")
    invitee = make_user(username="bob", password="x")
    with app.app_context():
        match_id = _seed_match(app, host["id"])
        invite = draft_service.invite_to_match(
            match_id,
            inviter_user_id=host["id"],
            invitee_username="bob",
        )
        rows = inbox_service.list_for_user(invitee["id"])
        assert len(rows) == 1
        n = rows[0]
        assert n.kind == "draft_invite"
        assert n.payload_json["match_id"] == match_id
        assert n.payload_json["inviter_username"] == "alice"
        assert n.payload_json["invite_id"] == invite.id


def test_inbox_notification_dropped_on_decline(
    app: Flask, make_user
) -> None:
    """Once the invitee declines, the notification is no longer
    actionable — drop it so the inbox reflects current state."""
    from uma_ladder.services import draft as draft_service

    host = make_user(username="alice", password="x")
    invitee = make_user(username="bob", password="x")
    with app.app_context():
        match_id = _seed_match(app, host["id"])
        invite = draft_service.invite_to_match(
            match_id,
            inviter_user_id=host["id"],
            invitee_username="bob",
        )
        assert inbox_service.unread_count_for_user(invitee["id"]) == 1
        draft_service.decline_invite(invite.id, by_user_id=invitee["id"])
        assert inbox_service.unread_count_for_user(invitee["id"]) == 0


def test_inbox_notification_dropped_on_cancel(
    app: Flask, make_user
) -> None:
    from uma_ladder.services import draft as draft_service

    host = make_user(username="alice", password="x")
    invitee = make_user(username="bob", password="x")
    with app.app_context():
        match_id = _seed_match(app, host["id"])
        invite = draft_service.invite_to_match(
            match_id,
            inviter_user_id=host["id"],
            invitee_username="bob",
        )
        assert inbox_service.unread_count_for_user(invitee["id"]) == 1
        draft_service.cancel_invite(invite.id, by_user_id=host["id"])
        assert inbox_service.unread_count_for_user(invitee["id"]) == 0


def test_inbox_notification_dropped_on_accept(
    app: Flask, make_user
) -> None:
    """Accepting fills the match seat; sibling pending invites for
    the same match get cancelled and their inbox rows removed too."""
    from uma_ladder.services import draft as draft_service

    host = make_user(username="alice", password="x")
    invitee_a = make_user(username="bob", password="x")
    invitee_b = make_user(username="carol", password="x")
    with app.app_context():
        match_id = _seed_match(app, host["id"])
        invite_a = draft_service.invite_to_match(
            match_id,
            inviter_user_id=host["id"],
            invitee_username="bob",
        )
        draft_service.invite_to_match(
            match_id,
            inviter_user_id=host["id"],
            invitee_username="carol",
        )
        # Both invitees see one pending notification each.
        assert inbox_service.unread_count_for_user(invitee_a["id"]) == 1
        assert inbox_service.unread_count_for_user(invitee_b["id"]) == 1
        draft_service.accept_invite(invite_a.id, by_user_id=invitee_a["id"])
        # Bob accepted → his row drops; Carol's sibling-cancel
        # should also drop hers.
        assert inbox_service.unread_count_for_user(invitee_a["id"]) == 0
        assert inbox_service.unread_count_for_user(invitee_b["id"]) == 0


# ---------- HTTP route + navbar bell ----------


def test_inbox_route_renders_notifications(
    client: FlaskClient, app: Flask, make_user
) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        inbox_service.create(
            user_id=info["id"],
            kind="draft_invite",
            payload={
                "match_id": 42,
                "inviter_username": "bob",
                "invite_id": 1,
            },
        )
    _login(client, "alice", "password123")
    resp = client.get("/inbox/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Inbox" in body
    assert "@bob" in body
    assert "Match #42" in body


def test_inbox_route_marks_read_on_view(
    client: FlaskClient, app: Flask, make_user
) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        inbox_service.create(
            user_id=info["id"],
            kind="draft_invite",
            payload={"match_id": 1, "inviter_username": "bob"},
        )
        assert inbox_service.unread_count_for_user(info["id"]) == 1
    _login(client, "alice", "password123")
    client.get("/inbox/")
    with app.app_context():
        assert inbox_service.unread_count_for_user(info["id"]) == 0


def test_inbox_route_requires_login(client: FlaskClient) -> None:
    resp = client.get("/inbox/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_inbox_route_empty_state(
    client: FlaskClient, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/inbox/")
    assert resp.status_code == 200
    assert b"No notifications" in resp.data


def test_navbar_bell_shows_unread_badge(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The badge must appear on every page (not just /inbox), and
    must show the actual unread count."""
    info = make_user(username="alice", password="password123")
    with app.app_context():
        for i in range(3):
            inbox_service.create(
                user_id=info["id"],
                kind="draft_invite",
                payload={"match_id": i, "inviter_username": "bob"},
            )
    _login(client, "alice", "password123")
    # Land on the dashboard — the bell badge must be there.
    resp = client.get("/")
    body = resp.data.decode()
    assert 'href="/inbox/"' in body
    assert ">3<" in body  # badge content


def test_navbar_bell_hides_when_zero(
    client: FlaskClient, make_user
) -> None:
    """Bell icon stays, but badge bubble is suppressed when there's
    nothing unread."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/")
    body = resp.data.decode()
    # Bell link present.
    assert 'href="/inbox/"' in body
    # No badge bubble — it shouldn't render any number near the bell
    # when the count is 0. We assert the absence of the bubble
    # span class which only renders inside the unread guard.
    assert "bg-fuchsia-500 px-1 text-[10px]" not in body


def test_anonymous_visit_does_not_query_inbox(
    client: FlaskClient,
) -> None:
    """Context processor short-circuits for anonymous so we don't
    do a pointless COUNT for every unauthenticated page view."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    # No bell for anonymous visitors.
    assert 'href="/inbox/"' not in body


def test_users_isolation_in_route(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Bob's notification must not appear in Alice's inbox."""
    alice = make_user(username="alice", password="password123")
    bob = make_user(username="bob", password="password123")
    with app.app_context():
        inbox_service.create(
            user_id=bob["id"],
            kind="draft_invite",
            payload={"match_id": 99, "inviter_username": "carol"},
        )
        # Sanity: Bob has 1, Alice has 0.
        assert inbox_service.unread_count_for_user(bob["id"]) == 1
        assert inbox_service.unread_count_for_user(alice["id"]) == 0
    _login(client, "alice", "password123")
    resp = client.get("/inbox/")
    assert b"@carol" not in resp.data
    assert b"Match #99" not in resp.data


# ---------- model parity ----------


def test_user_notification_cascade_on_user_delete(
    app: Flask, make_user
) -> None:
    """ondelete=CASCADE on user_id must wipe inbox rows when the
    user is hard-deleted (admin tooling not yet exposed but the
    constraint should hold)."""
    info = make_user(username="alice", password="x")
    with app.app_context():
        inbox_service.create(
            user_id=info["id"], kind="draft_invite", payload={}
        )
        assert (
            db.session.query(UserNotification).count() == 1
        )
        u = db.session.get(User, info["id"])
        db.session.delete(u)
        db.session.commit()
        assert (
            db.session.query(UserNotification).count() == 0
        ), "inbox row should cascade-delete with the user"


def test_admin_notifications_url_unchanged(
    client: FlaskClient, make_user
) -> None:
    """Sanity: introducing /inbox didn't accidentally collide with
    or replace the existing admin /notifications route."""
    make_user(username="adm", password="password123", role=Role.ADMIN)
    _login(client, "adm", "password123")
    resp = client.get("/notifications/")
    assert resp.status_code == 200
