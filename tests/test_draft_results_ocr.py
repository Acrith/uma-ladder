"""PR-I1: Draft results OCR upload + bot dismissal flow.

Mirrors the official-race OCR pipeline. The review page returns one
form row per parsed cluster; the user assigns each to host / opp /
skip. Skipped rows are dropped (covers the bot-filled seats in a
9-uma room). Multi-uma assignments per player work since PR-I2's
placement-sum aggregation in submit_results.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftEloChange,
    DraftMatch,
    DraftMatchStatus,
    DraftRaceResult,
    OcrParseAttempt,
    OcrParseStatus,
    PresetSource,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.services import draft as draft_service
from uma_ladder.services import ocr as ocr_service


def _png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d76368686800000005000170d80b240000000049"
        "454e44ae426082"
    )


def _file_storage() -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(_png()),
        filename="result.png",
        content_type="image/png",
    )


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _setup_match_in_room_code_phase(host_id: int, opp_id: int) -> int:
    now = datetime.now(UTC)
    s = Season(
        name="S",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    preset = RacePreset(
        source=PresetSource.G1_IMPORT,
        name="Tokyo G1",
        grade="G1",
        venue="Tokyo",
        surface="Turf",
        distance_meters=2000,
        distance_category="Medium",
        direction="Left",
        course_variant=None,
        max_runners=18,
        enabled=True,
    )
    db.session.add(preset)
    db.session.commit()
    m = draft_service.create_match(
        draft_service.CreateMatchRequest(
            season_id=s.id,
            host_user_id=host_id,
            umas_per_player=2,
            preset_pool="custom",
        )
    )
    m.opponent_user_id = opp_id
    m.selected_preset_id = preset.id
    m.status = DraftMatchStatus.ROOM_CODE_AVAILABLE
    m.room_code = "ROOM-1"
    db.session.commit()
    return m.id


def _seed_parse(match_id: int, host_id: int, opp_id: int) -> int:
    """Skip the actual OCR and seed an OcrParseAttempt directly with
    rows shaped like a 9-uma room. Real player rows for host (#1, #4)
    and opp (#2, #3); rest are bot rows the user will mark as skip."""
    image = ocr_service.save_uploaded_image(_file_storage(), uploader_user_id=host_id)
    attempt = OcrParseAttempt(
        uploaded_image_id=image.id,
        provider="mock",
        status=OcrParseStatus.PARSED,
        parsed_json={
            "rows": [
                {"placement": 1, "uma_name": "Player Uma A", "raw_line": "1 Player Uma A", "confidence": 0.9},
                {"placement": 2, "uma_name": "Player Uma B", "raw_line": "2 Player Uma B", "confidence": 0.9},
                {"placement": 3, "uma_name": "Player Uma C", "raw_line": "3 Player Uma C", "confidence": 0.9},
                {"placement": 4, "uma_name": "Player Uma D", "raw_line": "4 Player Uma D", "confidence": 0.9},
                {"placement": 5, "uma_name": "Bot Uma E", "raw_line": "5 Bot Uma E", "confidence": 0.9},
                {"placement": 6, "uma_name": "Bot Uma F", "raw_line": "6 Bot Uma F", "confidence": 0.9},
                {"placement": 7, "uma_name": "Bot Uma G", "raw_line": "7 Bot Uma G", "confidence": 0.9},
                {"placement": 8, "uma_name": "Bot Uma H", "raw_line": "8 Bot Uma H", "confidence": 0.9},
                {"placement": 9, "uma_name": "Bot Uma I", "raw_line": "9 Bot Uma I", "confidence": 0.9},
            ]
        },
        confidence_json={"overall": 0.9},
    )
    db.session.add(attempt)
    db.session.commit()
    return attempt.id


# ---------- HTTP layer ----------


def test_upload_route_rejects_non_participant(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host")
    opp = make_user(username="opp")
    make_user(username="random", password="password123", role=Role.USER)
    with app.app_context():
        match_id = _setup_match_in_room_code_phase(host["id"], opp["id"])
    _login(client, "random")
    resp = client.post(
        f"/draft/{match_id}/results-screenshot",
        data={"image": (io.BytesIO(_png()), "x.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_review_page_admin_can_access(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host")
    opp = make_user(username="opp")
    make_user(username="adm", password="password123", role=Role.ADMIN)
    with app.app_context():
        match_id = _setup_match_in_room_code_phase(host["id"], opp["id"])
        attempt_id = _seed_parse(match_id, host["id"], opp["id"])
    _login(client, "adm")
    resp = client.get(f"/draft/{match_id}/results-from-ocr/{attempt_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Confirm parsed results" in body
    # Default option is "skip" — bots stay dismissed unless toggled.
    assert "skip (bot)" in body


def test_review_page_participant_can_access(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        match_id = _setup_match_in_room_code_phase(host["id"], opp["id"])
        attempt_id = _seed_parse(match_id, host["id"], opp["id"])
    _login(client, "host")
    resp = client.get(f"/draft/{match_id}/results-from-ocr/{attempt_id}")
    assert resp.status_code == 200


def test_confirm_parse_skips_bot_rows_and_submits(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The headline PR-I1 case. User assigns rows 1+4 to host and 2+3
    to opp; rows 5-9 stay on default 'skip'. submit_results gets
    exactly four lines and PR-I2's placement-sum logic decides the
    winner (host sum 5 vs opp sum 5 → tiebreak on best individual,
    host wins with placement 1)."""
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        match_id = _setup_match_in_room_code_phase(host["id"], opp["id"])
        attempt_id = _seed_parse(match_id, host["id"], opp["id"])

    _login(client, "host")
    form_data: dict[str, str] = {}
    # Rows 0..3 are the player rows (placements 1-4).
    assignments = ["host", "opp", "opp", "host"]
    for i, assign in enumerate(assignments):
        form_data[f"assign_{i}"] = assign
        form_data[f"placement_{i}"] = str(i + 1)
        form_data[f"uma_name_{i}"] = f"Player Uma {chr(65 + i)}"
    # Rows 4..8 — bots. Leave assign_* unset so it falls through to skip.
    resp = client.post(
        f"/draft/{match_id}/results-from-ocr/{attempt_id}",
        data=form_data,
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        results = (
            db.session.query(DraftRaceResult)
            .filter_by(draft_match_id=match_id)
            .all()
        )
        assert len(results) == 4
        # Match completed and host won via tiebreak (best individual = 1).
        match = db.session.get(DraftMatch, match_id)
        assert match.status == DraftMatchStatus.COMPLETED
        assert match.winner_user_id == host["id"]
        # Two ELO change rows (one per side).
        assert db.session.query(DraftEloChange).filter_by(
            draft_match_id=match_id
        ).count() == 2


def test_confirm_parse_rejects_duplicate_placements(
    client: FlaskClient, app: Flask, make_user
) -> None:
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        match_id = _setup_match_in_room_code_phase(host["id"], opp["id"])
        attempt_id = _seed_parse(match_id, host["id"], opp["id"])
    _login(client, "host")
    resp = client.post(
        f"/draft/{match_id}/results-from-ocr/{attempt_id}",
        data={
            "assign_0": "host",
            "placement_0": "1",
            "uma_name_0": "X",
            "assign_1": "opp",
            "placement_1": "1",  # duplicate — should error
            "uma_name_1": "Y",
        },
        follow_redirects=False,
    )
    # Redirects back to the review page with a flash; the match is
    # not completed.
    assert resp.status_code == 302
    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        assert match.status == DraftMatchStatus.ROOM_CODE_AVAILABLE


def test_review_page_collapses_non_placement_rows_under_other(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Header / footer text without a placement digit should land in
    the "Other detected text" collapsible, not the main row list, so
    the user only triages real race entrants by default."""
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        match_id = _setup_match_in_room_code_phase(host["id"], opp["id"])
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=host["id"]
        )
        attempt = OcrParseAttempt(
            uploaded_image_id=image.id,
            provider="mock",
            status=OcrParseStatus.PARSED,
            parsed_json={
                "rows": [
                    {"placement": None, "uma_name": "Result Summary", "raw_line": "Result Summary", "confidence": 0.9},
                    {"placement": 1, "uma_name": "Player Uma A", "raw_line": "1 Player Uma A", "confidence": 0.9},
                    {"placement": None, "uma_name": "(c) Cygames footer", "raw_line": "(c) Cygames footer", "confidence": 0.9},
                ]
            },
            confidence_json={"overall": 0.9},
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = attempt.id
    _login(client, "host")
    resp = client.get(f"/draft/{match_id}/results-from-ocr/{attempt_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Placement row visible in the main list.
    assert "Player Uma A" in body
    # Non-placement rows live inside the <details> wrapper.
    assert "Other detected text" in body
    assert "Result Summary" in body
    assert "Cygames footer" in body


def test_confirm_parse_rejects_no_rows_assigned(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """If every row stays on 'skip' (bots only — match must have
    real players), refuse to submit and bounce back to the review."""
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        match_id = _setup_match_in_room_code_phase(host["id"], opp["id"])
        attempt_id = _seed_parse(match_id, host["id"], opp["id"])
    _login(client, "host")
    resp = client.post(
        f"/draft/{match_id}/results-from-ocr/{attempt_id}",
        data={},  # nothing assigned — every row is skip by default
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        assert match.status == DraftMatchStatus.ROOM_CODE_AVAILABLE
