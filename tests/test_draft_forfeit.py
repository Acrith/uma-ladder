"""Forfeit / ban-violation flow for draft matches.

Closes a match where a player used a banned uma in the room, or simply
ghosted — awards the match to the other player and applies Elo so the
ladder reflects the bad behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftEloChange,
    DraftMatch,
    DraftMatchStatus,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.services import draft as draft_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _make_room_code_match(app: Flask, host_id: int, opp_id: int) -> int:
    """Create a match wired up to ROOM_CODE_AVAILABLE so submit_forfeit
    has a valid state to act on."""
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
                host_user_id=host_id,
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        match.opponent_user_id = opp_id
        match.status = DraftMatchStatus.ROOM_CODE_AVAILABLE
        db.session.commit()
        return match.id


# ---------- service-level tests ----------


def test_forfeit_awards_other_player_and_applies_elo(
    app: Flask, make_user
) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    org = make_user(username="org", role=Role.ORGANIZER)
    match_id = _make_room_code_match(app, host["id"], opp["id"])

    with app.app_context():
        # Host forfeits → opponent wins.
        draft_service.submit_forfeit(
            match_id,
            forfeiter_user_id=host["id"],
            by_user_id=org["id"],
            reason="ban_violation",
        )
        match = db.session.get(DraftMatch, match_id)
        assert match.status == DraftMatchStatus.COMPLETED
        assert match.winner_user_id == opp["id"]
        assert match.loser_user_id == host["id"]
        assert match.forfeit_user_id == host["id"]
        assert match.forfeit_reason == "ban_violation"
        # Two DraftEloChange rows written; winner positive, loser negative.
        changes = (
            db.session.query(DraftEloChange)
            .filter_by(draft_match_id=match_id)
            .all()
        )
        assert len(changes) == 2
        winner_change = next(c for c in changes if c.user_id == opp["id"])
        loser_change = next(c for c in changes if c.user_id == host["id"])
        assert winner_change.delta > 0
        assert loser_change.delta < 0
        # Elo conserved (modulo rounding).
        assert abs(winner_change.delta + loser_change.delta) <= 1


def test_forfeit_skips_banned_uma_validation(app: Flask, make_user) -> None:
    """submit_results refuses banned-uma rows; submit_forfeit must NOT —
    the whole point is to close a match where exactly that happened."""
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    org = make_user(username="org", role=Role.ORGANIZER)
    match_id = _make_room_code_match(app, host["id"], opp["id"])
    with app.app_context():
        # Forfeit even with no DraftRaceResult rows recorded should
        # succeed. We're testing the absence of placement validation.
        draft_service.submit_forfeit(
            match_id,
            forfeiter_user_id=host["id"],
            by_user_id=org["id"],
        )
        match = db.session.get(DraftMatch, match_id)
        assert match.status == DraftMatchStatus.COMPLETED


def test_forfeit_refuses_completed_match(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    org = make_user(username="org", role=Role.ORGANIZER)
    match_id = _make_room_code_match(app, host["id"], opp["id"])
    with app.app_context():
        draft_service.submit_forfeit(
            match_id, forfeiter_user_id=host["id"], by_user_id=org["id"]
        )
        with pytest.raises(draft_service.InvalidMatchStateError):
            draft_service.submit_forfeit(
                match_id, forfeiter_user_id=host["id"], by_user_id=org["id"]
            )


def test_forfeit_refuses_non_participant(app: Flask, make_user) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    org = make_user(username="org", role=Role.ORGANIZER)
    rando = make_user(username="rando", role=Role.USER)
    match_id = _make_room_code_match(app, host["id"], opp["id"])
    with app.app_context(), pytest.raises(draft_service.NotAParticipantOfMatchError):
        draft_service.submit_forfeit(
            match_id,
            forfeiter_user_id=rando["id"],
            by_user_id=org["id"],
        )


# ---------- HTTP layer ----------


def test_route_organiser_can_forfeit(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    make_user(username="org", role=Role.ORGANIZER)
    match_id = _make_room_code_match(app, host["id"], opp["id"])

    _login(client, "org")
    resp = client.post(
        f"/draft/{match_id}/forfeit",
        data={"forfeiter_user_id": str(host["id"]), "reason": "no_show"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        assert match.status == DraftMatchStatus.COMPLETED
        assert match.forfeit_reason == "no_show"


def test_route_plain_user_cannot_forfeit(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    make_user(username="alice", role=Role.USER)
    match_id = _make_room_code_match(app, host["id"], opp["id"])
    _login(client, "alice")
    resp = client.post(
        f"/draft/{match_id}/forfeit",
        data={"forfeiter_user_id": str(host["id"])},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_completed_match_detail_renders_forfeit_marker(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    make_user(username="org", role=Role.ORGANIZER)
    match_id = _make_room_code_match(app, host["id"], opp["id"])
    _login(client, "org")
    client.post(
        f"/draft/{match_id}/forfeit",
        data={
            "forfeiter_user_id": str(host["id"]),
            "reason": "ban_violation",
        },
    )
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # The completed-banner card mentions the forfeit + the reason.
    assert "Forfeit by" in body
    assert "ban_violation" in body
