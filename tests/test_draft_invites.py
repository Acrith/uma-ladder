"""PR-I7 — Layer-A draft invites by username.

Host opens a match (status=WAITING_FOR_OPPONENT) and sends a typed
username invite. Target user gets a banner on their dashboard +
draft index with Accept / Decline buttons. Accept runs the standard
join_match flow; Decline just dismisses the invite without changing
the match.

Sibling pending invites are auto-cancelled once one's accepted (the
match seat is filled).

No presence tracking (Layer B) and no Discord DM (Layer C) — those
are deferred backlog.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftInviteStatus,
    DraftMatch,
    DraftMatchInvite,
    DraftMatchStatus,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.services import draft as draft_service


def _ensure_season(app: Flask) -> Season:
    s = db.session.query(Season).first()
    if s:
        return s
    now = datetime.now(UTC)
    s = Season(
        name="S",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    return s


def _waiting_match(host_id: int) -> int:
    s = _ensure_season(None)  # type: ignore[arg-type]
    m = draft_service.create_match(
        draft_service.CreateMatchRequest(
            season_id=s.id,
            host_user_id=host_id,
            umas_per_player=2,
            preset_pool="custom",
        )
    )
    return m.id


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------- service-level ----------


def test_invite_happy_path(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    invitee = make_user(username="bob", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        invite = draft_service.invite_to_match(
            match_id,
            inviter_user_id=host["id"],
            invitee_username="bob",
        )
        assert invite.id is not None
        assert invite.draft_match_id == match_id
        assert invite.invitee_user_id == invitee["id"]
        assert invite.status == DraftInviteStatus.PENDING


def test_invite_rejects_self(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        with pytest.raises(draft_service.InviteError) as exc:
            draft_service.invite_to_match(
                match_id,
                inviter_user_id=host["id"],
                invitee_username="host",
            )
        assert "yourself" in str(exc.value)


def test_invite_rejects_unknown_user(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        with pytest.raises(draft_service.InviteError):
            draft_service.invite_to_match(
                match_id,
                inviter_user_id=host["id"],
                invitee_username="nobody",
            )


def test_invite_rejects_when_match_already_has_opponent(
    app: Flask, make_user
) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    make_user(username="third", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        draft_service.join_match(match_id, opp["id"])
        with pytest.raises(draft_service.InvalidMatchStateError):
            draft_service.invite_to_match(
                match_id,
                inviter_user_id=host["id"],
                invitee_username="third",
            )


def test_invite_rejects_double_invite(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    make_user(username="bob", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        with pytest.raises(draft_service.InviteError) as exc:
            draft_service.invite_to_match(
                match_id, inviter_user_id=host["id"], invitee_username="bob"
            )
        assert "already" in str(exc.value)


def test_accept_invite_joins_the_match(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    bob = make_user(username="bob", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        invite = draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        draft_service.accept_invite(invite.id, by_user_id=bob["id"])
        match = db.session.get(DraftMatch, match_id)
        assert match.opponent_user_id == bob["id"]
        # Status moved out of WAITING_FOR_OPPONENT.
        assert match.status != DraftMatchStatus.WAITING_FOR_OPPONENT
        # Invite row marked accepted with a responded_at timestamp.
        refreshed = db.session.get(DraftMatchInvite, invite.id)
        assert refreshed.status == DraftInviteStatus.ACCEPTED
        assert refreshed.responded_at is not None


def test_accept_cancels_sibling_pending_invites(app: Flask, make_user) -> None:
    """Once the match seat is filled, every other pending invite for
    the same match becomes void — flip them to CANCELLED so the
    other invitees don't see stale banners."""
    host = make_user(username="host", role=Role.USER)
    bob = make_user(username="bob", role=Role.USER)
    cara = make_user(username="cara", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        bob_invite = draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        cara_invite = draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="cara"
        )
        draft_service.accept_invite(bob_invite.id, by_user_id=bob["id"])
        cara_refreshed = db.session.get(DraftMatchInvite, cara_invite.id)
        assert cara_refreshed.status == DraftInviteStatus.CANCELLED
        # cara doesn't see this invite in her pending list anymore.
        assert draft_service.list_pending_invites_for_user(cara["id"]) == []


def test_decline_invite_leaves_match_unchanged(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    bob = make_user(username="bob", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        invite = draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        draft_service.decline_invite(invite.id, by_user_id=bob["id"])
        match = db.session.get(DraftMatch, match_id)
        assert match.opponent_user_id is None
        assert match.status == DraftMatchStatus.WAITING_FOR_OPPONENT
        refreshed = db.session.get(DraftMatchInvite, invite.id)
        assert refreshed.status == DraftInviteStatus.DECLINED


def test_cancel_invite_only_by_inviter(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    bob = make_user(username="bob", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        invite = draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        # bob (the invitee) trying to cancel is rejected.
        with pytest.raises(draft_service.InviteError):
            draft_service.cancel_invite(invite.id, by_user_id=bob["id"])
        # host can.
        draft_service.cancel_invite(invite.id, by_user_id=host["id"])
        refreshed = db.session.get(DraftMatchInvite, invite.id)
        assert refreshed.status == DraftInviteStatus.CANCELLED


def test_list_pending_filters_to_active_matches(app: Flask, make_user) -> None:
    """A pending invite to a match that subsequently gets cancelled
    or has someone else join shouldn't surface as actionable."""
    host = make_user(username="host", role=Role.USER)
    bob = make_user(username="bob", role=Role.USER)
    other = make_user(username="other", role=Role.USER)
    with app.app_context():
        match_id = _waiting_match(host["id"])
        draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        # Someone else joins via the join code → match leaves
        # WAITING_FOR_OPPONENT.
        draft_service.join_match(match_id, other["id"])
        # bob's pending list filters out the now-stale invite.
        assert draft_service.list_pending_invites_for_user(bob["id"]) == []


# ---------- HTTP layer ----------


def test_dashboard_renders_pending_invite_banner(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host", password="password123")
    make_user(username="bob", password="password123")
    with app.app_context():
        match_id = _waiting_match(host["id"])
        draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
    _login(client, "bob")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "@host" in body
    assert "invited you to" in body
    assert f"draft match #{match_id}" in body


def test_invite_route_only_host_can_send(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Non-host attempting the invite POST gets a flash but no
    invite created."""
    host = make_user(username="host", password="password123")
    bob = make_user(username="bob", password="password123")  # noqa: F841
    make_user(username="cara", password="password123")
    with app.app_context():
        match_id = _waiting_match(host["id"])
    _login(client, "bob")
    resp = client.post(
        f"/draft/{match_id}/invite",
        data={"invitee_username": "cara"},
        follow_redirects=False,
    )
    # Redirects back to detail with a flash; no invite created.
    assert resp.status_code == 302
    with app.app_context():
        invites = db.session.query(DraftMatchInvite).all()
        assert len(invites) == 0


def test_accept_invite_route_jumps_into_match(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host", password="password123")
    make_user(username="bob", password="password123")
    with app.app_context():
        match_id = _waiting_match(host["id"])
        invite = draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        invite_id = invite.id
    _login(client, "bob")
    resp = client.post(
        f"/draft/invites/{invite_id}/accept", follow_redirects=False
    )
    assert resp.status_code == 302
    assert f"/draft/{match_id}" in resp.headers["Location"]
    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        assert match.status != DraftMatchStatus.WAITING_FOR_OPPONENT


def test_decline_invite_route_clears_banner(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host", password="password123")
    bob = make_user(username="bob", password="password123")
    with app.app_context():
        match_id = _waiting_match(host["id"])
        invite = draft_service.invite_to_match(
            match_id, inviter_user_id=host["id"], invitee_username="bob"
        )
        invite_id = invite.id
    _login(client, "bob")
    resp = client.post(
        f"/draft/invites/{invite_id}/decline", follow_redirects=False
    )
    assert resp.status_code == 302
    with app.app_context():
        # Match unchanged; bob has no pending invites.
        match = db.session.get(DraftMatch, match_id)
        assert match.status == DraftMatchStatus.WAITING_FOR_OPPONENT
        assert draft_service.list_pending_invites_for_user(bob["id"]) == []
