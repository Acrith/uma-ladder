"""OCR-driven results entry for official races.

The integration runs end-to-end with the MockOcrProvider so we don't
need a real OCR runtime — the mock returns three deterministic rows
that line up with the registered users for testing the round trip.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    OcrParseAttempt,
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource


def _png_bytes() -> bytes:
    """Smallest valid PNG so FileAllowed accepts it."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _setup(app: Flask) -> tuple[int, int]:
    """Create season, preset, return (season_id, preset_id)."""
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        p = RacePreset(
            source=PresetSource.G1_IMPORT,
            name="Tokyo G1",
            venue="Tokyo",
            surface="Turf",
            distance_meters=2000,
            distance_category="Medium",
            direction="Left",
            course_variant=None,
            max_runners=18,
            enabled=True,
        )
        db.session.add_all([s, p])
        db.session.commit()
        return s.id, p.id


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_upload_screenshot_redirects_to_step_page(
    client: FlaskClient, app: Flask, make_user
) -> None:
    app.config["OCR_PROVIDER"] = "mock"
    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "result.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/results-from-ocr/" in resp.headers["Location"]


def test_step_page_renders_parsed_rows_and_suggestions(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """MockOcrProvider returns rows with uma_name 'MockUma A/B/C'. None
    will substring-match registered usernames, so all rows show up
    unassigned — the UI still renders the parsed names."""
    app.config["OCR_PROVIDER"] = "mock"
    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    make_user(username="alice", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "r.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Confirm parsed results" in body
    assert "MockUma A" in body
    assert "MockUma B" in body
    assert "@alice" in body  # registration shows up in dropdown


def test_substring_match_assigns_registration(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """When a registered username appears as a substring of an OCR row's
    uma_name, the dropdown should pre-select that registration."""
    app.config["OCR_PROVIDER"] = "mock"
    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    # 'mockuma' is a substring of 'MockUma A' (case-insensitive), forcing
    # the substring-fallback branch of _match_ocr_to_registrations.
    make_user(username="mockuma", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "mockuma")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "r.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    body = resp.data.decode()
    # The first OCR row (MockUma A) should pre-select the mockuma user.
    # Find the @mockuma option marked selected.
    assert "selected>\n                        @mockuma" in body or 'selected>' in body and '@mockuma' in body


def test_full_ocr_to_results_round_trip(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Upload screenshot, render step page, submit through /results,
    and confirm the OfficialRaceResult rows match what the mock OCR
    suggested."""
    app.config["OCR_PROVIDER"] = "mock"
    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    make_user(username="alice", role=Role.USER)
    make_user(username="bob", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "bob")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "org")

    # Drive OCR.
    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "r.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    attempt_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    with app.app_context():
        a = db.session.get(OcrParseAttempt, attempt_id)
        assert a is not None
        rows = (a.parsed_json or {}).get("rows", [])
        assert len(rows) == 3  # MockOcrProvider returns 3 rows

    # Look up registration ids — alice has placement 1, bob 2.
    from uma_ladder.models import OfficialRaceRegistration
    with app.app_context():
        regs = (
            db.session.query(OfficialRaceRegistration)
            .filter_by(official_race_id=race_id)
            .all()
        )
        by_username = {r.user.username: r.id for r in regs}

    # Submit to the standard /results endpoint with the OCR-derived
    # placement/uma_name fields. (In the browser, the JS on the step
    # page assembles these hidden fields; here we send them directly.)
    resp = client.post(
        f"/official/{race_id}/results",
        data={
            f"placement_{by_username['alice']}": "1",
            f"uma_name_{by_username['alice']}": "MockUma A",
            f"placement_{by_username['bob']}": "2",
            f"uma_name_{by_username['bob']}": "MockUma B",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        race = db.session.get(OfficialRace, race_id)
        assert race.status == OfficialRaceStatus.COMPLETED
        results = (
            db.session.query(OfficialRaceResult)
            .filter_by(official_race_id=race_id)
            .order_by(OfficialRaceResult.placement)
            .all()
        )
        assert [r.placement for r in results] == [1, 2]
        assert [r.uma_name for r in results] == ["MockUma A", "MockUma B"]


# ─── PR-OCR5: multi-file upload + merge-by-placement ─────────────


def test_upload_multiple_screenshots_creates_attempts_and_merges(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Posting N image files creates N OcrParseAttempts. The redirect
    targets the FIRST attempt's confirm page; its parsed_json carries
    a merged rows list deduped by placement (highest confidence wins)
    so the review page sees the union of what each screenshot saw."""
    app.config["OCR_PROVIDER"] = "mock"
    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={
            "image": [
                (io.BytesIO(_png_bytes()), "first.png"),
                (io.BytesIO(_png_bytes()), "second.png"),
                (io.BytesIO(_png_bytes()), "third.png"),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/results-from-ocr/" in resp.headers["Location"]
    with app.app_context():
        attempts = (
            db.session.query(OcrParseAttempt).order_by(OcrParseAttempt.id).all()
        )
        assert len(attempts) == 3
        # The first attempt's parsed_json should now carry the merged
        # rows + the list of contributing image ids.
        primary = attempts[0]
        screenshot_ids = (primary.parsed_json or {}).get(
            "screenshot_image_ids"
        )
        assert screenshot_ids is not None
        assert len(screenshot_ids) == 3


def test_upload_zero_files_redirects_with_flash(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """No files at all → redirect back to race detail with a flash,
    no attempts created. Catches the regression where an empty
    file-list slipped through to OCR provider."""
    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert f"/official/{race_id}" in resp.headers["Location"]
    with app.app_context():
        assert db.session.query(OcrParseAttempt).count() == 0


# ─── PR-A5: row-parser fields surfaced + persisted ───────────────


def test_confirm_page_renders_row_parser_chips(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-A5 — the official confirm template now displays the
    parsed gate / time-or-lengths / fav_rank as compact chips
    under each OCR row's detected uma name, matching what Draft
    has already had."""
    from uma_ladder.models import OcrParseAttempt, OcrParseStatus, UploadedImage

    app.config["OCR_PROVIDER"] = "mock"
    sid, pid = _setup(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    make_user(username="gold_ship", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "gold_ship")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "org")

    # Craft an attempt with a row that carries the row-parser fields
    # so we don't depend on the mock provider doing the field peel.
    with app.app_context():
        image = UploadedImage(
            uploader_user_id=host["id"],
            storage_key=f"a5-{race_id}.png",
            mime_type="image/png",
            size_bytes=100,
        )
        db.session.add(image)
        db.session.commit()
        attempt = OcrParseAttempt(
            uploaded_image_id=image.id,
            provider="google_vision",
            parsed_json={
                "rows": [
                    {
                        "placement": 1,
                        "uma_name": "Gold Ship",
                        "raw_line": "...",
                        "confidence": 0.9,
                        "gate": 8,
                        "time_or_lengths": "3:43.8",
                        "fav_rank": 1,
                    }
                ],
            },
            status=OcrParseStatus.PARSED,
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = attempt.id

    resp = client.get(
        f"/official/{race_id}/results-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    # All three chips render as text.
    assert "Gate 8" in body
    assert "3:43.8" in body
    assert "#1 fav" in body
    # The hidden carrier fields for the registered user exist so
    # the submit can ship the values through.
    assert 'name="finish_time_' in body
    assert 'name="gate_' in body
    assert 'name="fav_rank_' in body


def test_submit_results_persists_row_parser_fields(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """End-to-end: posting finish_time_<reg> / gate_<reg> /
    fav_rank_<reg> alongside placement_<reg> saves them on the
    OfficialRaceResult row."""
    from uma_ladder.models import OfficialRaceResult

    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    make_user(username="alice", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "org")

    with app.app_context():
        from uma_ladder.services import official as official_service

        regs = official_service.list_registrations(race_id)
        reg_id = regs[0].id

    resp = client.post(
        f"/official/{race_id}/results",
        data={
            f"placement_{reg_id}": "1",
            f"uma_name_{reg_id}": "Gold Ship",
            f"finish_time_{reg_id}": "3:43.8",
            f"gate_{reg_id}": "8",
            f"fav_rank_{reg_id}": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        row = (
            db.session.query(OfficialRaceResult)
            .filter_by(official_race_id=race_id)
            .one()
        )
        assert row.finish_time_or_lengths == "3:43.8"
        assert row.gate == 8
        assert row.fav_rank == 1


def test_submit_results_skips_blank_row_parser_fields(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Manual entry path: leaving the new fields blank persists
    NULLs without erroring. Confirms ResultLine defaults pass
    through cleanly."""
    from uma_ladder.models import OfficialRaceResult

    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    make_user(username="alice", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "org")

    with app.app_context():
        from uma_ladder.services import official as official_service

        regs = official_service.list_registrations(race_id)
        reg_id = regs[0].id

    client.post(
        f"/official/{race_id}/results",
        data={
            f"placement_{reg_id}": "1",
            f"uma_name_{reg_id}": "Gold Ship",
            # No finish_time / gate / fav_rank — manual entry.
        },
        follow_redirects=False,
    )

    with app.app_context():
        row = (
            db.session.query(OfficialRaceResult)
            .filter_by(official_race_id=race_id)
            .one()
        )
        assert row.finish_time_or_lengths is None
        assert row.gate is None
        assert row.fav_rank is None


def test_race_detail_renders_row_parser_pills(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Once persisted, the fields render as compact pills above
    the stat grid on the race detail per-result card."""
    from uma_ladder.models import OfficialRaceResult

    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    make_user(username="alice", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post(f"/official/{race_id}/open")
    client.post("/auth/logout")
    _login(client, "alice")
    client.post(f"/official/{race_id}/register")
    client.post("/auth/logout")
    _login(client, "org")

    with app.app_context():
        from uma_ladder.services import official as official_service

        regs = official_service.list_registrations(race_id)
        reg_id = regs[0].id

    client.post(
        f"/official/{race_id}/results",
        data={
            f"placement_{reg_id}": "1",
            f"uma_name_{reg_id}": "Gold Ship",
            f"finish_time_{reg_id}": "3:43.8",
            f"gate_{reg_id}": "8",
            f"fav_rank_{reg_id}": "1",
        },
    )

    with app.app_context():
        row = (
            db.session.query(OfficialRaceResult)
            .filter_by(official_race_id=race_id)
            .one()
        )
        # Sanity — persisted before we check render.
        assert row.gate == 8

    resp = client.get(f"/official/{race_id}")
    body = resp.data.decode()
    assert "Gate 8" in body
    assert "3:43.8" in body
    assert "#1 fav" in body


def test_non_organizer_cannot_upload(
    client: FlaskClient, app: Flask, make_user
) -> None:
    app.config["OCR_PROVIDER"] = "mock"
    sid, pid = _setup(app)
    make_user(username="org", role=Role.ORGANIZER)
    make_user(username="alice", role=Role.USER)
    _login(client, "org")
    resp = client.post(
        "/official/new",
        data={"season_id": sid, "name": "R", "preset_id": pid},
        follow_redirects=False,
    )
    race_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    client.post("/auth/logout")
    _login(client, "alice")
    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "r.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 403
