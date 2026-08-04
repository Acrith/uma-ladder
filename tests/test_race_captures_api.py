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


# ─── review + confirm ────────────────────────────────────────────────

def _archive_payload() -> dict:
    """The on-disk archive shape: results live in a separate array and
    stats use the game's own field names."""
    return json.loads((CAPTURE_DIR / "34408987_800095.full.json").read_text())


def test_runner_rows_tolerates_the_archive_shape() -> None:
    """The extractor's upload payload folds the outcome into each
    runner; the archive keeps it separate. Both must read the same, or
    a capture silently looks like it has no result."""
    rows = captures_service._runner_rows(_archive_payload())
    assert rows, "archive payload produced no runners"
    assert all(r.get("finish_position") for r in rows)
    # pow/wiz normalized into the nested stats shape
    assert rows[0]["stats"]["power"] is not None
    assert rows[0]["stats"]["wit"] is not None


def test_merge_keeps_the_richer_capture(app: Flask, client: FlaskClient, token: str) -> None:
    """A second upload of the same room may be poorer — the scenario
    may have failed to decode. It must not erase a good first capture."""
    good = _payload()
    client.post("/api/race-captures", json=good, headers=_auth(token))
    poor = _payload()
    for r in poor["runners"]:
        r["finish_position"] = None
    client.post("/api/race-captures", json=poor, headers=_auth(token))
    with app.app_context():
        capture = db.session.query(RaceCapture).one()
        rows = captures_service._runner_rows(capture.payload_json)
        assert any(r.get("finish_position") for r in rows), "richer capture was lost"


def test_match_runners_finds_accounts_by_username_and_display_name(
    app: Flask, make_user
) -> None:
    from uma_ladder.models import User
    from uma_ladder.services.profiles import get_or_create_profile

    make_user(username="acrith", password="password123")
    shown = make_user(username="kezuke", password="password123")
    with app.app_context():
        u = db.session.get(User, shown["id"])
        get_or_create_profile(u).display_name = "Shizu"
        db.session.commit()
        result = captures_service.ingest(_payload(), submitted_by_user_id=None)
        matches = captures_service.match_runners(result.capture)
        by_name = {m.trainer_name: m for m in matches}
        assert by_name["Acrith"].match_confidence == "exact"
        assert by_name["Acrith"].user_label == "acrith"
        # game name "Kezuke" == that account's username, not its display
        assert by_name["Kezuke"].user_id is not None
        # nobody on the ladder is called this
        assert by_name["Trubber"].user_id is None
        assert by_name["Trubber"].match_confidence == "none"


def test_confirm_writes_results_and_reranks(app: Flask, make_user) -> None:
    """Runners that aren't ladder members are skipped, and the rest are
    re-ranked 1..N so the race sees a dense finishing order."""
    from uma_ladder.models import OfficialRaceResult, PresetSource, RacePreset
    from uma_ladder.services import official as official_service
    from uma_ladder.services import seasons as seasons_service

    org = make_user(username="acrith", password="password123", role="organizer")
    a = make_user(username="aisha alsadhazi", password="password123")
    b = make_user(username="electricfire", password="password123")

    with app.app_context():
        from datetime import UTC, datetime, timedelta

        season = seasons_service.create_season(
            name="cap season",
            starts_at=datetime.now(UTC) - timedelta(days=1),
            ends_at=datetime.now(UTC) + timedelta(days=30),
        )
        preset = RacePreset(
            name="T", venue="Tokyo", surface="Turf", distance_meters=1600,
            distance_category="Mile", direction="Right", max_runners=12,
            source=PresetSource.MANUAL, enabled=True,
        )
        db.session.add(preset)
        db.session.commit()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="Cap Race",
                organizer_user_id=org["id"], preset_id=preset.id,
            )
        )
        capture = captures_service.ingest(
            _payload(), submitted_by_user_id=org["id"]
        ).capture
        # Aisha finished 1st and StarlitFire 2nd; only they are mapped.
        rows = {m.trainer_name: m.gate for m in captures_service.match_runners(capture)}
        captures_service.confirm(
            capture.id,
            race_id=race.id,
            user_by_gate={rows["Aisha AlSadhazi"]: a["id"],
                          rows["StarlitFire"]: b["id"]},
            actor_user_id=org["id"],
        )

        results = list(
            db.session.scalars(
                db.select(OfficialRaceResult).where(
                    OfficialRaceResult.official_race_id == race.id
                )
            ).unique()
        )
        assert len(results) == 2
        assert sorted(r.placement for r in results) == [1, 2]
        winner = next(r for r in results if r.placement == 1)
        assert winner.user_id == a["id"]
        # rich detail carried across, not just placement
        assert winner.speed and winner.gate and winner.strategy
        assert winner.finish_time_or_lengths

        refreshed = db.session.get(RaceCapture, capture.id)
        assert refreshed.status == RaceCaptureStatus.CONFIRMED
        assert refreshed.official_race_id == race.id


def test_confirm_refuses_twice(app: Flask, make_user) -> None:
    org = make_user(username="acrith", password="password123", role="organizer")
    with app.app_context():
        capture = captures_service.ingest(
            _payload(), submitted_by_user_id=org["id"]
        ).capture
        capture.status = RaceCaptureStatus.CONFIRMED
        db.session.commit()
        with pytest.raises(captures_service.CaptureError):
            captures_service.confirm(
                capture.id, race_id=1, user_by_gate={1: org["id"]},
                actor_user_id=org["id"],
            )


def test_review_page_is_organizer_gated_for_confirming(
    app: Flask, client: FlaskClient, make_user
) -> None:
    make_user(username="plain", password="password123")
    with app.app_context():
        capture = captures_service.ingest(_payload(), submitted_by_user_id=None).capture
        cid = capture.id
    client.post("/auth/login", data={"username": "plain", "password": "password123"})
    # A non-organizer cannot push a capture into the ladder.
    resp = client.post(f"/captures/{cid}/confirm", data={"race_id": 1})
    assert resp.status_code in (302, 403)
    with app.app_context():
        assert db.session.get(RaceCapture, cid).status == RaceCaptureStatus.PENDING


def test_confirm_writes_skills_and_aptitudes(app: Flask, make_user) -> None:
    """The capture carries every runner's full skill list and all ten
    aptitude grades — far more than a placement. Confirming must push
    those through the enrichment path so the race page renders them."""
    from datetime import UTC, datetime, timedelta

    from uma_ladder.models import OfficialRaceResult, PresetSource, RacePreset
    from uma_ladder.services import official as official_service
    from uma_ladder.services import seasons as seasons_service

    org = make_user(username="acrith", password="password123", role="organizer")
    winner = make_user(username="aisha alsadhazi", password="password123")

    with app.app_context():
        season = seasons_service.create_season(
            name="enrich season",
            starts_at=datetime.now(UTC) - timedelta(days=1),
            ends_at=datetime.now(UTC) + timedelta(days=30),
        )
        preset = RacePreset(
            name="T", venue="Tokyo", surface="Turf", distance_meters=1600,
            distance_category="Mile", direction="Right", max_runners=12,
            source=PresetSource.MANUAL, enabled=True,
        )
        db.session.add(preset)
        db.session.commit()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="Enrich Race",
                organizer_user_id=org["id"], preset_id=preset.id,
            )
        )
        payload = _payload()
        # The capture carries skill *ids*; resolving them to names needs
        # the catalog, which a bare test DB doesn't have. Seed just the
        # ids this runner actually used so the id->name path is what's
        # under test rather than an empty table.
        from uma_ladder.models import UmaSkill

        winner_row = next(
            r for r in payload["runners"] if r["trainer_name"] == "Aisha AlSadhazi"
        )
        for entry in winner_row["skills"][:6]:
            db.session.add(
                UmaSkill(
                    gametora_id=entry["skill_id"],
                    name_en=f"Test Skill {entry['skill_id']}",
                )
            )
        db.session.commit()

        capture = captures_service.ingest(
            payload, submitted_by_user_id=org["id"]
        ).capture
        gate = next(
            m.gate for m in captures_service.match_runners(capture)
            if m.trainer_name == "Aisha AlSadhazi"
        )
        captures_service.confirm(
            capture.id, race_id=race.id,
            user_by_gate={gate: winner["id"]}, actor_user_id=org["id"],
        )
        result = db.session.scalars(
            db.select(OfficialRaceResult).where(
                OfficialRaceResult.official_race_id == race.id
            )
        ).unique().one()
        assert len(result.skills) >= 5, "skill list did not land"
        assert result.aptitudes, "aptitudes did not land"
        assert result.aptitudes["track"]["turf"] in set("GFEDCBAS")
        assert set(result.aptitudes) == {"track", "distance", "style"}


def test_aptitude_block_reads_both_payload_shapes() -> None:
    """Upload payload nests aptitudes; the archive keeps the game's raw
    `proper_*` field names. Both must normalize identically."""
    nested = {"gate": 1, "aptitudes": {"turf": 8, "dirt": 4}}
    raw = {"gate": 1, "proper_ground_turf": 8, "proper_ground_dirt": 4}
    a = captures_service._runner_rows({"runners": [nested]})[0]["aptitudes"]
    b = captures_service._runner_rows({"runners": [raw]})[0]["aptitudes"]
    assert a["turf"] == b["turf"] == 8
    assert captures_service._aptitude_block(b)["track"]["turf"] == "S"
