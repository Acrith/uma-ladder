"""PR-X1/X2 — race capture ingest API.

Exercised against a real extractor payload captured from the game
(docs/room-match-scout/captures/), not a hand-written fixture, so the
contract is tested against data the extractor actually produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import RaceCapture, RaceCaptureStatus
from uma_ladder.services import api_tokens as tokens_service
from uma_ladder.services import race_captures as captures_service

CAPTURE_DIR = Path(__file__).resolve().parent.parent / "docs" / "room-match-scout" / "captures"
REAL_CAPTURE = CAPTURE_DIR / "34408987_800095.upload.json"


def _payload() -> dict:
    """The 1600m InyanyaCup capture — 11 runners, known finishing order."""
    return json.loads(REAL_CAPTURE.read_text())


@pytest.fixture
def token(app: Flask, make_user):
    user = make_user(username="extractor_user", password="password123")
    with app.app_context():
        from uma_ladder.models import User

        u = db.session.get(User, user["id"])
        _row, plaintext = tokens_service.issue_token(u, name="test rig")
    return plaintext


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ─── auth ────────────────────────────────────────────────────────────

def test_ingest_requires_a_token(client: FlaskClient) -> None:
    resp = client.post("/api/race-captures", json=_payload())
    assert resp.status_code == 401


def test_ingest_rejects_a_bogus_token(client: FlaskClient) -> None:
    resp = client.post(
        "/api/race-captures", json=_payload(), headers=_auth("not-a-real-token")
    )
    assert resp.status_code == 401


def test_revoked_token_stops_working(app: Flask, client: FlaskClient, make_user) -> None:
    user = make_user(username="revoked_user", password="password123")
    with app.app_context():
        from uma_ladder.models import User

        u = db.session.get(User, user["id"])
        row, plaintext = tokens_service.issue_token(u)
        assert client.get("/api/ping", headers=_auth(plaintext)).status_code == 200
        tokens_service.revoke_token(u, row.id)
    assert client.get("/api/ping", headers=_auth(plaintext)).status_code == 401


def test_ping_names_the_site(client: FlaskClient, token: str) -> None:
    """Lets the extractor confirm it is pointed at umaladder.moe and not
    at the sibling IT site before uploading anything."""
    resp = client.get("/api/ping", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.get_json()["site"] == "umaladder.moe"


# ─── ingest ──────────────────────────────────────────────────────────

def test_ingest_stores_a_real_capture(
    app: Flask, client: FlaskClient, token: str
) -> None:
    resp = client.post("/api/race-captures", json=_payload(), headers=_auth(token))
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["created"] is True
    assert body["status"] == RaceCaptureStatus.PENDING

    with app.app_context():
        capture = db.session.get(RaceCapture, body["id"])
        assert capture.saved_room_id == 34408987
        assert capture.race_instance_id == 800095
        assert capture.participant_count == 11
        assert capture.started_at is not None
        # The raw payload is kept verbatim — it is the analysis corpus.
        assert len(capture.payload_json["runners"]) == 11


def test_ingest_is_idempotent_on_room_id(
    app: Flask, client: FlaskClient, token: str
) -> None:
    """Every participant in a room can run the extractor, so the same
    result legitimately arrives more than once. The second upload is
    not an error."""
    first = client.post("/api/race-captures", json=_payload(), headers=_auth(token))
    second = client.post("/api/race-captures", json=_payload(), headers=_auth(token))
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["created"] is False
    assert second.get_json()["id"] == first.get_json()["id"]
    with app.app_context():
        assert db.session.query(RaceCapture).count() == 1


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p.pop("room"), "room"),
        (lambda p: p["room"].update(saved_room_id=None), "saved_room_id"),
        (lambda p: p.update(runners=[]), "runners"),
        (lambda p: p.update(source="telepathy"), "source"),
    ],
)
def test_ingest_rejects_unusable_payloads(
    client: FlaskClient, token: str, mutate, expected: str
) -> None:
    payload = _payload()
    mutate(payload)
    resp = client.post("/api/race-captures", json=payload, headers=_auth(token))
    assert resp.status_code == 400
    assert expected in resp.get_json()["error"]


def test_capture_is_private_to_its_uploader(
    app: Flask, client: FlaskClient, token: str, make_user
) -> None:
    created = client.post(
        "/api/race-captures", json=_payload(), headers=_auth(token)
    ).get_json()
    other = make_user(username="nosy", password="password123")
    with app.app_context():
        from uma_ladder.models import User

        _row, other_token = tokens_service.issue_token(
            db.session.get(User, other["id"])
        )
    resp = client.get(
        f"/api/race-captures/{created['id']}", headers=_auth(other_token)
    )
    # 404, not 403 — don't confirm someone else's capture exists.
    assert resp.status_code == 404


# ─── normalization ───────────────────────────────────────────────────

def test_summary_translates_game_values(app: Flask, client: FlaskClient, token: str) -> None:
    created = client.post(
        "/api/race-captures", json=_payload(), headers=_auth(token)
    ).get_json()
    resp = client.get(f"/api/race-captures/{created['id']}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    # season 2 / weather 1 / ground 1 in the game's integers.
    assert body["race_season"] == "Summer"
    assert body["weather"] == "Sunny"
    assert body["ground_condition"] == "Firm"
    # Runners come back in finishing order, with the app's own wording
    # for running style rather than the game's integer.
    positions = [r["finish_position"] for r in body["runners"]]
    assert positions == sorted(positions)
    assert body["runners"][0]["trainer_name"] == "Aisha AlSadhazi"
    assert body["runners"][0]["strategy"] in {"Front", "Pace", "Late", "End"}


def test_service_maps_every_game_enum_value() -> None:
    """Guards the lookup tables against a partial edit."""
    assert set(captures_service.GAME_SEASON) == {1, 2, 3, 4}
    assert set(captures_service.GAME_WEATHER) == {1, 2, 3, 4}
    assert set(captures_service.GAME_GROUND) == {1, 2, 3, 4}
    assert set(captures_service.GAME_RUNNING_STYLE) == {1, 2, 3, 4}
    assert set(captures_service.GAME_APTITUDE) == set(range(1, 9))
