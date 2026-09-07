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


# ─── activations + replay ────────────────────────────────────────────

def test_scenario_decoder_reads_skill_activations() -> None:
    """Which skills fired is only knowable from the scenario blob."""
    import base64
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "race_extractor"))
    from scenario_decode import inflate_scenario, parse_skill_activations

    full = _archive_payload()
    plain = inflate_scenario(
        base64.b64decode(full["scenario"]["raw_b64"]), full["scenario"]["key"]
    )
    equipped = [{s["skill_id"] for s in (r.get("skills") or [])} for r in full["runners"]]
    acts = parse_skill_activations(plain, equipped)
    assert len(acts) > 100, "no activation records found"
    assert any(acts.values()) and not all(acts.values()), (
        "expected a mix of fired and never-fired"
    )
    # Every verdict must belong to a skill that runner actually had —
    # that constraint is what makes locating records by skill id safe.
    for (runner, skill_id) in acts:
        assert skill_id in equipped[runner]


def test_confirm_writes_raw_stats(app: Flask, make_user) -> None:
    """The capture carries every runner's five stats. They were being
    parsed, shown in the review UI, and then silently dropped on the
    way to the ladder — the race page rendered aptitudes and skills but
    no stats at all."""
    from datetime import UTC, datetime, timedelta

    from uma_ladder.models import OfficialRaceResult, PresetSource, RacePreset
    from uma_ladder.services import official as official_service
    from uma_ladder.services import seasons as seasons_service

    org = make_user(username="statorg", password="password123", role="organizer")
    winner = make_user(username="aisha alsadhazi", password="password123")

    with app.app_context():
        season = seasons_service.create_season(
            name="stat season",
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
                season_id=season.id, name="Stat Race",
                organizer_user_id=org["id"], preset_id=preset.id,
            )
        )
        payload = _payload()
        capture = captures_service.ingest(payload, submitted_by_user_id=org["id"]).capture
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
        source = next(
            r for r in payload["runners"] if r["trainer_name"] == "Aisha AlSadhazi"
        )["stats"]
        assert result.speed == source["speed"]
        assert result.stamina == source["stamina"]
        assert result.power == source["power"]
        assert result.guts == source["guts"]
        # The capture calls it "wit"; the ladder column is "wisdom".
        assert result.wisdom == source["wit"]


def test_reapply_details_repairs_an_already_confirmed_capture(app: Flask, make_user) -> None:
    """Confirming is one-shot, so a race saved while enrichment had a
    gap would stay broken forever. Re-applying must restore it without
    disturbing the placement."""
    from datetime import UTC, datetime, timedelta

    from uma_ladder.models import OfficialRaceResult, PresetSource, RacePreset
    from uma_ladder.services import official as official_service
    from uma_ladder.services import seasons as seasons_service

    org = make_user(username="fixorg", password="password123", role="organizer")
    winner = make_user(username="aisha alsadhazi", password="password123")

    with app.app_context():
        season = seasons_service.create_season(
            name="fix season",
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
                season_id=season.id, name="Fix Race",
                organizer_user_id=org["id"], preset_id=preset.id,
            )
        )
        capture = captures_service.ingest(
            _payload(), submitted_by_user_id=org["id"]
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
        placement_before = result.placement
        # Simulate a result saved by the older, stat-dropping path.
        result.speed = result.stamina = result.power = None
        result.guts = result.wisdom = None
        db.session.commit()

        updated = captures_service.reapply_details(
            capture.id, actor_user_id=org["id"]
        )
        assert updated == 1
        # Runners are re-matched by trainer name through the same index
        # the review UI uses — including display names, which live on
        # UserProfile rather than User. Getting that wrong only shows up
        # when the username does NOT match, so assert the lookup shape
        # directly rather than relying on this fixture's happy path.
        by_username, by_display = captures_service._index_users()
        assert winner["id"] in {u.id for u in by_username.values()}
        assert all(
            hasattr(u, "id") for u in by_display.values() if u is not None
        )
        db.session.refresh(result)
        assert result.speed and result.wisdom
        assert result.placement == placement_before


def test_confirm_records_which_skills_fired(app: Flask, make_user) -> None:
    from datetime import UTC, datetime, timedelta

    from uma_ladder.models import (
        OfficialRaceResult,
        PresetSource,
        RacePreset,
        UmaSkill,
    )
    from uma_ladder.services import official as official_service
    from uma_ladder.services import seasons as seasons_service

    org = make_user(username="acrith", password="password123", role="organizer")
    winner = make_user(username="aisha alsadhazi", password="password123")

    with app.app_context():
        season = seasons_service.create_season(
            name="act season",
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
                season_id=season.id, name="Act Race",
                organizer_user_id=org["id"], preset_id=preset.id,
            )
        )
        payload = _payload()
        row = next(
            r for r in payload["runners"] if r["trainer_name"] == "Aisha AlSadhazi"
        )
        verdicts = [e for e in row["skills"] if "activated" in e]
        assert verdicts, "fixture carries no activation data"
        for e in verdicts[:8]:
            db.session.add(
                UmaSkill(gametora_id=e["skill_id"], name_en=f"S{e['skill_id']}")
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
        flags = [s.activated for s in result.skills]
        assert any(f is True for f in flags), "no skill marked as fired"
        # A skill the scenario never mentions stays None — "unknown",
        # not "didn't fire". The screenshot path leaves everything None.
        assert all(f in (True, False, None) for f in flags)


def test_replay_series_is_absent_without_telemetry(app: Flask) -> None:
    """With no live telemetry AND no scenario blob there is nothing to
    build a replay from; the page must simply omit the chart rather
    than error. (A payload that does carry the blob now gets a replay
    parsed out of it — that path has its own tests.)"""
    payload = _payload()
    payload.pop("scenario", None)
    with app.app_context():
        capture = captures_service.ingest(payload, submitted_by_user_id=None).capture
        assert captures_service.replay_series(capture) is None


def test_replay_series_builds_traces(app: Flask) -> None:
    replay_file = CAPTURE_DIR / "34408987_800095.withreplay.json"
    if not replay_file.exists():
        pytest.skip("no replay-bearing capture archived")
    payload = json.loads(replay_file.read_text())
    payload["source"] = "memory_scan"
    with app.app_context():
        capture = captures_service.ingest(payload, submitted_by_user_id=None).capture
        series = captures_service.replay_series(capture)
        assert series is not None
        assert series["frame_count"] > 50
        assert series["max_distance"] > 1000
        first = series["runners"][0]
        assert len(first["gap"]) == series["frame_count"]
        assert len(first["hp"]) == series["frame_count"]
        # Leader's gap to the leader is zero somewhere by definition.
        assert min(y for _x, y in first["gap"]) == 0
        # Rank drives the bump chart: a valid position per sample, and
        # every position occupied at any instant.
        assert len(first["rank"]) == series["frame_count"]
        for i in range(0, series["frame_count"], 17):
            ranks_now = sorted(r["rank"][i] for r in series["runners"])
            assert ranks_now == list(range(1, series["runner_count"] + 1))
        # The strip needs own-distance and lane, not just gap.
        assert len(first["dist"]) == len(first["lane"]) == series["frame_count"]
        # Phase bands tile the whole course without gaps.
        bands = series["phases"]
        assert bands[0]["start"] == 0
        assert abs(bands[-1]["end"] - series["max_distance"]) < 1
        for a, b in zip(bands, bands[1:], strict=False):
            assert abs(a["end"] - b["start"]) < 1
        assert len(series["x_axis"]) == series["frame_count"]
        # Runners come back in finishing order for a readable legend.
        finishes = [r["finish"] for r in series["runners"] if r["finish"]]
        assert finishes == sorted(finishes)


def test_replay_series_falls_back_to_the_scenario_blob(app: Flask) -> None:
    """A capture taken from the result screen has no live telemetry —
    but its scenario blob contains the whole replay. The page must get
    frames, events and a correctly scaled clock from the blob alone."""
    replay_file = CAPTURE_DIR / "34408987_800095.upload.json"
    if not replay_file.exists():
        pytest.skip("no blob-bearing capture archived")
    payload = json.loads(replay_file.read_text())
    assert not payload.get("sim"), "fixture unexpectedly carries live telemetry"
    payload["source"] = "memory_scan"
    with app.app_context():
        capture = captures_service.ingest(payload, submitted_by_user_id=None).capture
        series = captures_service.replay_series(capture)
        assert series is not None
        assert series["frame_count"] > 50
        # The two-clock rescale needs the blob's result records.
        assert series["time_scale"] > 1.0
        # And the event stream decodes from the blob's packed records.
        assert series["events"] and series["events"]["skills"]
        # No course geometry in the blob — that stays live-only.
        assert series["course"] is None


def test_blob_replay_matches_the_live_memory_replay() -> None:
    """The blob parse and the live RaceSimulateData read describe the
    same race — frame for frame, result for result, event for event.
    This is the guarantee that lets the blob replace the live read as
    the replay source. Lane/speed carry the blob's u16 quantization,
    so those compare at quantization precision; everything else is
    float32 in both sources and must match exactly."""
    blob_file = CAPTURE_DIR / "34408987_800095.upload.json"
    live_file = CAPTURE_DIR / "34408987_800095.withevents.json"
    if not (blob_file.exists() and live_file.exists()):
        pytest.skip("need both blob-only and live-telemetry captures")
    from uma_ladder.services import race_blob

    from_blob = race_blob.sim_from_payload(json.loads(blob_file.read_text()))
    from_live = json.loads(live_file.read_text())["sim"]
    assert from_blob is not None

    assert len(from_blob["frames"]) == len(from_live["frames"])
    for fb, fl in zip(from_blob["frames"], from_live["frames"], strict=True):
        assert fb["t"] == fl["t"]
        for hb, hl in zip(fb["h"], fl["h"], strict=True):
            assert hb["Distance"] == hl["Distance"]
            assert abs(hb["LanePosition"] - hl["LanePosition"]) < 1e-4
            assert abs(hb["Speed"] - hl["Speed"]) < 1e-2
            assert abs(hb["Hp"] - hl["Hp"]) <= 1.0
            assert hb["BlockFrontHorseIndex"] == hl["BlockFrontHorseIndex"]

    for rb, rl in zip(from_blob["horses"], from_live["horses"], strict=True):
        for field in ("FinishOrder", "FinishTime", "StartDelayTime",
                      "LastSpurtStartDistance", "RunningStyle"):
            assert rb[field] == rl[field], field

    assert len(from_blob["events"]) == len(from_live["events"])
    for eb, el in zip(from_blob["events"], from_live["events"], strict=True):
        assert eb["t"] == el["t"]
        assert eb["type"] == el["type"]
        assert eb["param"] == el["param"]


def test_skill_duration_is_course_scaled_tenthousandths() -> None:
    """A Skill event's param[2] is the effect duration in 1/10000 s,
    already scaled by course length — so `duration = param[2] / 10000`
    needs no further adjustment.

    This can only be proven across distances: on a single course the
    /10000 and the length scaling are mathematically indistinguishable.
    Corpus covers 1600 m, 2200 m and 3600 m, and the same skill reads
    48000 / 66000 / 107999 respectively — one constant base duration of
    3.0 s. Guards against anyone "fixing" the unit later.
    """
    import collections

    from uma_ladder.services import race_blob

    races = {
        "34408987_800095.upload.json": 1600,
        "70090038_800074.upload.json": 2200,
        "85520900_800072.upload.json": 3600,
    }
    by_skill: dict[int, dict[int, int]] = collections.defaultdict(dict)
    for name, distance in races.items():
        path = CAPTURE_DIR / name
        if not path.exists():
            pytest.skip(f"{name} not archived")
        sim = race_blob.sim_from_payload(json.loads(path.read_text()))
        assert sim, f"{name} carries no parseable blob"
        for ev in sim["events"]:
            p = ev.get("param") or []
            if ev["type"] == "Skill" and len(p) > 2 and p[2] != -1:
                by_skill[p[1]].setdefault(distance, p[2])

    shared = {s: r for s, r in by_skill.items() if len(r) > 1 and any(r.values())}
    assert len(shared) >= 10, "not enough cross-distance skills to conclude anything"

    agreeing = 0
    for row in shared.values():
        # base seconds implied by  param[2] = base * 10000 * distance/1000
        implied = [raw / 10.0 / dist for dist, raw in row.items() if raw]
        if len(implied) > 1 and max(implied) - min(implied) < 0.002:
            agreeing += 1
            # and the base is always a clean skill duration, never a
            # number that only makes sense under some other unit
            assert 0.5 <= implied[0] <= 10.0
    # A couple of ids legitimately differ (same skill, different level),
    # so this asserts an overwhelming majority rather than unanimity.
    assert agreeing >= len(shared) * 0.8, f"only {agreeing}/{len(shared)} scaled"


def _events_capture(app: Flask):
    """A capture that carries the event stream and course geometry."""
    replay_file = CAPTURE_DIR / "34408987_800095.withevents.json"
    if not replay_file.exists():
        pytest.skip("no event-bearing capture archived")
    payload = json.loads(replay_file.read_text())
    payload["source"] = "memory_scan"
    return captures_service.ingest(payload, submitted_by_user_id=None).capture


def test_replay_reads_course_geometry(app: Flask) -> None:
    """Corners and slopes come from the game's own course tables, so
    the track strip is the real shape of the venue rather than a
    generic bar."""
    with app.app_context():
        series = captures_service.replay_series(_events_capture(app))
        course = series["course"]
        assert course["corners"], "no corners decoded"
        # Segments are ordered and lie inside the course.
        for key in ("straights", "corners", "slopes"):
            for seg in course[key]:
                assert 0 <= seg["start"] < seg["end"] <= series["race_distance"] + 1
        # Exactly one corner is the final one — that's what the UI
        # highlights, and two would mean we mis-read the flag.
        assert sum(1 for c in course["corners"] if c["is_final"]) == 1
        # The finish line is the course length, not how far runners ran
        # past it before pulling up.
        assert series["race_distance"] < series["max_distance"]


def test_replay_clock_matches_the_recorded_finish_times(app: Flask) -> None:
    """The simulation's clock runs slower than the clock the game
    quotes finishing times on. Unless we rescale, the replay would end
    at 76s for a race the results table calls 90s."""
    with app.app_context():
        capture = _events_capture(app)
        series = captures_service.replay_series(capture)
        assert series["time_scale"] > 1.0
        winner = min(
            (r for r in capture.payload_json["runners"] if r.get("finish_time_seconds")),
            key=lambda r: r["finish_position"],
        )
        # Playback runs to the last sample, which is a beat past the
        # line — but it must be the winner's time, not 20% short of it.
        assert series["duration"] >= winner["finish_time_seconds"]
        assert series["duration"] < winner["finish_time_seconds"] + 3


def test_replay_decodes_skill_activations(app: Flask) -> None:
    """Every fired skill, when it fired, how long it lasted, and — via
    the affected-runner bitmask — whether it was a self-buff or thrown
    at rivals."""
    with app.app_context():
        capture = _events_capture(app)
        series = captures_service.replay_series(capture)
        skills = series["events"]["skills"]
        assert skills, "no activations decoded"
        # Chronological, inside the race, and every one has a duration.
        assert skills == sorted(skills, key=lambda s: s["t"])
        for s in skills:
            assert 0 <= s["t"] <= series["duration"] + 1
            assert s["duration"] >= 0
            assert 0 <= s["runner"] < series["runner_count"]
            # Placed on the chart as well as the clock.
            assert 0 <= s["x"] <= series["max_distance"] + 1
            # A caster is never listed among its own targets, or a
            # plain self-buff would render as a debuff.
            assert s["runner"] not in s["targets"]
        # Skills that never fired are dropped, so the count is below the
        # number equipped across the field.
        equipped = sum(
            len(r.get("skills") or []) for r in capture.payload_json["runners"]
        )
        assert 0 < len(skills) < equipped
        # At least one debuff landed on someone else — that decode is
        # the part a plain "did it fire" flag can't express.
        assert any(s["targets"] for s in skills)


def test_replay_feed_is_chronological_commentary(app: Flask) -> None:
    with app.app_context():
        series = captures_service.replay_series(_events_capture(app))
        feed = series["events"]["feed"]
        assert feed
        assert [e["t"] for e in feed] == sorted(e["t"] for e in feed)
        for e in feed:
            assert e["who"] and e["text"]
            assert e["kind"] in ("skill", "debuff", "moment")
        # Race moments are translated, never surfaced as engine enum
        # names like "ReleaseConservePower".
        assert not any(e["text"][0].isupper() and "Power" in e["text"] for e in feed)


def test_sectionals_read_in_finishing_order(app: Flask) -> None:
    """The heatmap is only readable if row 1 is the winner — gate order
    would put a back-marker on top."""
    replay_file = CAPTURE_DIR / "34408987_800095.withreplay.json"
    if not replay_file.exists():
        pytest.skip("no replay-bearing capture archived")
    payload = json.loads(replay_file.read_text())
    payload["source"] = "memory_scan"
    with app.app_context():
        capture = captures_service.ingest(payload, submitted_by_user_id=None).capture
        sec = captures_service.replay_series(capture)["sectionals"]
        finishes = [r["finish"] for r in sec["rows"] if r["finish"]]
        assert finishes == sorted(finishes)
        assert finishes[0] == 1
        # Segments stop at the finish rather than crediting the metres
        # runners cover past the line.
        assert sec["edges"][-1] <= int(
            captures_service.replay_series(capture)["max_distance"]
        ) + 1
        assert sec["labels"][-1] == "finish"
        # Every cell is scored against that segment's field mean.
        assert len(sec["means"]) == len(sec["labels"])
