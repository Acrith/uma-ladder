"""Organiser-only match cancellation. Used to clear the backlog of test
matches that never completed — not a player-facing forfeit feature."""

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
)
from uma_ladder.services import draft as draft_service


def _make_match(app: Flask, *, host_id: int, opp_id: int | None = None) -> int:
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
                host_user_id=host_id,
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        if opp_id is not None:
            draft_service.join_match(m.id, opp_id)
        return m.id


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_organizer_can_cancel_pre_completed_match(
    client: FlaskClient, app: Flask, make_user
) -> None:
    org = make_user(username="org", password="password123", role="organizer")
    host = make_user(username="hostie", password="password123")
    match_id = _make_match(app, host_id=host["id"])

    _login(client, org["username"], "password123")
    resp = client.post(f"/draft/{match_id}/cancel", follow_redirects=False)
    assert resp.status_code == 302
    with app.app_context():
        m = db.session.get(DraftMatch, match_id)
        assert m.status == DraftMatchStatus.CANCELLED
        assert m.cancelled_at is not None
        assert m.cancelled_by_user_id == org["id"]


def test_plain_user_cannot_cancel(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Even the host can't cancel — only organisers."""
    host = make_user(username="hostie", password="password123")
    match_id = _make_match(app, host_id=host["id"])

    _login(client, host["username"], "password123")
    resp = client.post(f"/draft/{match_id}/cancel", follow_redirects=False)
    assert resp.status_code == 403
    with app.app_context():
        m = db.session.get(DraftMatch, match_id)
        assert m.status != DraftMatchStatus.CANCELLED


def test_completed_match_cannot_be_cancelled(app: Flask, make_user) -> None:
    org = make_user(username="org", password="password123", role="organizer")
    host = make_user(username="hostie", password="password123")
    match_id = _make_match(app, host_id=host["id"])

    with app.app_context():
        m = db.session.get(DraftMatch, match_id)
        m.status = DraftMatchStatus.COMPLETED
        db.session.commit()
        with pytest.raises(draft_service.InvalidMatchStateError):
            draft_service.cancel_match(match_id, by_user_id=org["id"])


def test_double_cancel_is_rejected(app: Flask, make_user) -> None:
    org = make_user(username="org", password="password123", role="organizer")
    host = make_user(username="hostie", password="password123")
    match_id = _make_match(app, host_id=host["id"])

    with app.app_context():
        draft_service.cancel_match(match_id, by_user_id=org["id"])
        with pytest.raises(draft_service.InvalidMatchStateError):
            draft_service.cancel_match(match_id, by_user_id=org["id"])


def test_cancelled_detail_renders_banner_and_no_phase_forms(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Once cancelled, the detail page shows the cancelled banner and
    suppresses phase forms (ready/track-ban/uma-ban)."""
    org = make_user(username="org", password="password123", role="organizer")
    host = make_user(username="hostie", password="password123")
    match_id = _make_match(app, host_id=host["id"])

    _login(client, org["username"], "password123")
    client.post(f"/draft/{match_id}/cancel")

    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Cancelled" in body
    # No phase form actions should be rendered.
    assert "track-ban" not in body
    assert "uma-ban" not in body
    assert "/ready" not in body
    # Cancel button itself should disappear once cancelled.
    assert ">Cancel match<" not in body
