"""Per-result OCR enrichment: stats + skill matching against the
imported UmaSkill catalogue."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceResultSkill,
    OfficialRaceStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
    UmaSkill,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import official as official_service


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _setup_completed_race(app: Flask, host_id: int) -> tuple[int, int]:
    """Create a season + preset + race in COMPLETED state with one result."""
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
        race = OfficialRace(
            season_id=s.id,
            organizer_user_id=host_id,
            name="Done",
            preset_id=p.id,
            status=OfficialRaceStatus.COMPLETED,
        )
        db.session.add(race)
        db.session.commit()
        result = OfficialRaceResult(
            official_race_id=race.id,
            user_id=host_id,
            placement=1,
            uma_name="Special Week",
        )
        db.session.add(result)
        db.session.commit()
        return race.id, result.id


def _seed_skills(app: Flask) -> None:
    """Seed a couple of skills so the matcher has something to find."""
    with app.app_context():
        db.session.add_all([
            UmaSkill(
                gametora_id=10071,
                name_en="Warning Shot!",
                is_unique=True,
                is_inherited=False,
                enabled=True,
            ),
            UmaSkill(
                gametora_id=10081,
                name_en="Accelerator X",
                is_unique=True,
                is_inherited=False,
                enabled=True,
            ),
        ])
        db.session.commit()


# PR-OCR4 — integration test: the per-result confirm page now runs
# the Uma-sheet extractor over the parsed rows. Mirrors the
# 21-line Tamamo Cross OCR dump that drives the extractor tests.
_TAMAMO_ROWS: list[dict] = [
    {"raw_line": "Umamusume Details", "placement": None, "uma_name": None},
    {"raw_line": "St [ Fast as Lightning ]", "placement": None, "uma_name": None},
    {"raw_line": "RANK Tamamo Cross", "placement": None, "uma_name": None},
    {"raw_line": "Epithet", "placement": None, "uma_name": None},
    {"raw_line": "Now That's White Lightning!", "placement": None, "uma_name": None},
    {"raw_line": "17,307 Trainer Yuuta", "placement": None, "uma_name": None},
    {"raw_line": "Speed ♥ Stamina Power Guts Wit", "placement": None, "uma_name": None},
    {"raw_line": "1197 1070 1105 553 638", "placement": None, "uma_name": None},
    {"raw_line": "Track Turf A Dirt F", "placement": None, "uma_name": None},
    {"raw_line": "Distance Sprint G Mile B Medium A Long A", "placement": None, "uma_name": None},
    {"raw_line": "Style Front G Pace A Late A End A", "placement": None, "uma_name": None},
    {"raw_line": "Save As Practice", "placement": None, "uma_name": None},
    {"raw_line": "Partner", "placement": None, "uma_name": None},
    {"raw_line": "Skills Inspiration Career Info", "placement": None, "uma_name": None},
    {"raw_line": "Anchors Aweigh!", "placement": None, "uma_name": None},
    {"raw_line": "Close", "placement": None, "uma_name": None},
]


def test_per_result_confirm_uses_sheet_extractor(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Race-result extractor put 1197 in EVERY stat cell on Uma sheets.
    After PR-OCR4 the per-result confirm page runs the sheet extractor
    on the attempt's parsed rows and renders the positionally-paired
    values (1197 / 1070 / 1105 / 553 / 638)."""
    from uma_ladder.models import OcrParseAttempt, OcrParseStatus, UploadedImage
    from uma_ladder.models.enums import UploadPurpose

    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    with app.app_context():
        image = UploadedImage(
            uploader_user_id=host["id"],
            storage_key=f"test-tamamo-{result_id}.png",
            original_filename="tamamo.png",
            mime_type="image/png",
            size_bytes=100,
            purpose=UploadPurpose.OCR_RESULT,
        )
        db.session.add(image)
        db.session.commit()
        attempt = OcrParseAttempt(
            uploaded_image_id=image.id,
            provider="google_vision",
            raw_text="",
            parsed_json={
                "rows": _TAMAMO_ROWS,
                "stats": {},
                "skills": [],
            },
            confidence_json={},
            status=OcrParseStatus.PARSED,
        )
        db.session.add(attempt)
        # Seed Anchors Aweigh! so the catalogue scan can resolve it.
        db.session.add(
            UmaSkill(
                gametora_id=20001,
                name_en="Anchors Aweigh!",
                is_unique=True,
                is_inherited=False,
                enabled=True,
            )
        )
        db.session.commit()
        attempt_id = attempt.id

    resp = client.get(
        f"/official/{race_id}/results/{result_id}"
        f"/details-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()

    # Stats inputs render with positionally-matched values from the
    # sheet extractor. The critical one is stamina=1070 — under the
    # old race-result extractor every cell got the same first int
    # (1197) and stamina would have rendered as 1197 too.
    import re

    speed_v = re.search(r'name="speed"[^>]*value="(\d+)"', body)
    stamina_v = re.search(r'name="stamina"[^>]*value="(\d+)"', body)
    power_v = re.search(r'name="power"[^>]*value="(\d+)"', body)
    guts_v = re.search(r'name="guts"[^>]*value="(\d+)"', body)
    wisdom_v = re.search(r'name="wisdom"[^>]*value="(\d+)"', body)
    assert speed_v and speed_v.group(1) == "1197"
    assert stamina_v and stamina_v.group(1) == "1070"
    assert power_v and power_v.group(1) == "1105"
    assert guts_v and guts_v.group(1) == "553"
    assert wisdom_v and wisdom_v.group(1) == "638"

    # The catalogue-matched skill should pre-populate a skill_name_ input.
    skill_inputs = re.findall(
        r'<input[^>]*name="skill_name_\d+"[^>]*value="([^"]+)"', body
    )
    assert "Anchors Aweigh!" in skill_inputs


def test_per_result_multi_upload_merges_sheet_extracts(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-OCR5 — picking N screenshots for one result creates N
    attempts; the first attempt's parsed_json gets the union of the
    per-screenshot sheet extracts (skills deduped, stats first-non-
    empty). The confirm route reads from parsed_json's stats/skills
    fallback after sheet re-extraction returns empty over the
    intentionally-cleared rows."""
    from uma_ladder.models import OcrParseAttempt

    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={
            "image": [
                (io.BytesIO(_png_bytes()), "a.png"),
                (io.BytesIO(_png_bytes()), "b.png"),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "details-from-ocr" in resp.headers["Location"]
    with app.app_context():
        attempts = (
            db.session.query(OcrParseAttempt).order_by(OcrParseAttempt.id).all()
        )
        # Two attempts created, one per file.
        assert len(attempts) == 2
        primary = attempts[0]
        # First attempt's parsed_json carries the merge marker
        # (list of contributing screenshot image ids).
        screenshot_ids = (primary.parsed_json or {}).get(
            "screenshot_image_ids"
        )
        assert screenshot_ids is not None
        assert len(screenshot_ids) == 2
        # Rows were cleared so the confirm route's re-extract pass
        # returns empty and the fallback picks up our pre-merged
        # stats / skills.
        assert (primary.parsed_json or {}).get("rows") == []


def test_per_result_single_upload_unchanged(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Posting one file should NOT trigger the merge path — the
    redirect goes to the single attempt's confirm URL and parsed_json
    keeps its rows intact (no merge marker, no clearing)."""
    from uma_ladder.models import OcrParseAttempt

    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "single.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        attempts = db.session.query(OcrParseAttempt).all()
        assert len(attempts) == 1
        primary = attempts[0]
        # Rows from the original parse survive — not cleared.
        assert (primary.parsed_json or {}).get("rows") is not None
        # No multi-screenshot marker.
        assert "screenshot_image_ids" not in (primary.parsed_json or {})


def test_per_result_confirm_falls_back_when_sheet_extractor_empty(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """If the upload isn't a sheet (mock provider in tests, or a
    real race-result screenshot in prod), the sheet extractor
    returns nothing — and we fall back to the original
    run_parse output. Catches the regression that would have
    broken the existing per-result confirm flow."""
    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "race.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    attempt_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    resp = client.get(
        f"/official/{race_id}/results/{result_id}"
        f"/details-from-ocr/{attempt_id}"
    )
    body = resp.data.decode()
    # Mock provider's stats survive the fallback path.
    assert 'value="1100"' in body
    # And its skill candidates make it through too.
    assert "Warning Shot!" in body


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------- service-level tests ----------


def test_match_skill_names_fuzzy_handles_typography_variants(app: Flask) -> None:
    """OCR commonly mis-reads punctuation and decorative glyphs. The
    fuzzy fallback strips non-alphanumerics so an em-dash for a hyphen
    or a missing ☆ doesn't drop the skill on the floor."""
    with app.app_context():
        db.session.add_all([
            UmaSkill(
                gametora_id=10141,
                name_en="Hot Blooded ☆ Amigo",
                is_unique=True,
                is_inherited=False,
                enabled=True,
            ),
            UmaSkill(
                gametora_id=10241,
                name_en="Victory Kiss ☆",
                is_unique=True,
                is_inherited=False,
                enabled=True,
            ),
        ])
        db.session.commit()
        matches = official_service._match_skill_names(
            [
                "hot blooded amigo",  # missing ☆
                "Hot Blooded — Amigo",  # em-dash instead of ☆
                "VICTORY KISS",  # missing trailing ☆
                "totally not a real skill",
            ]
        )
        ids = [sid for _, sid in matches]
        # First three all resolve to a skill_id; last one falls through.
        assert ids[0] is not None
        assert ids[1] is not None
        assert ids[2] is not None
        assert ids[3] is None


def test_match_skill_names_exact_case_insensitive(app: Flask) -> None:
    _seed_skills(app)
    with app.app_context():
        matches = official_service._match_skill_names(
            ["WARNING SHOT!", "accelerator x", "Made-up Skill"]
        )
        # Order preserved.
        assert [n for n, _ in matches] == [
            "WARNING SHOT!",
            "accelerator x",
            "Made-up Skill",
        ]
        # Two matched, one fell through with skill_id=None.
        assert matches[0][1] is not None
        assert matches[1][1] is not None
        assert matches[2][1] is None


def test_submit_result_details_writes_stats_and_skills(
    app: Flask, make_user
) -> None:
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    with app.app_context():
        update = official_service.ResultDetailsUpdate(
            speed=1100,
            stamina=900,
            power=1000,
            guts=600,
            wisdom=800,
            strategy="Front",
            skill_names=("Warning Shot!", "Made-up Skill"),
        )
        official_service.submit_result_details(
            result_id, update, by_user_id=host["id"]
        )
        result = db.session.get(OfficialRaceResult, result_id)
        assert result.speed == 1100
        assert result.strategy == "Front"
        skills = (
            db.session.query(OfficialRaceResultSkill)
            .filter_by(official_race_result_id=result_id)
            .order_by(OfficialRaceResultSkill.position)
            .all()
        )
        assert len(skills) == 2
        # First matched, second didn't.
        assert skills[0].skill_id is not None
        assert skills[0].skill.name_en == "Warning Shot!"
        assert skills[1].skill_id is None
        assert skills[1].raw_ocr_text == "Made-up Skill"


def test_submit_result_details_replaces_existing_skills(
    app: Flask, make_user
) -> None:
    """Re-running detail submission should atomically replace the prior
    skill list, not double up."""
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    with app.app_context():
        official_service.submit_result_details(
            result_id,
            official_service.ResultDetailsUpdate(skill_names=("Warning Shot!",)),
            by_user_id=host["id"],
        )
        official_service.submit_result_details(
            result_id,
            official_service.ResultDetailsUpdate(skill_names=("Accelerator X",)),
            by_user_id=host["id"],
        )
        skills = (
            db.session.query(OfficialRaceResultSkill)
            .filter_by(official_race_result_id=result_id)
            .all()
        )
        assert len(skills) == 1
        assert skills[0].skill.name_en == "Accelerator X"


# ---------- HTTP layer tests ----------


def test_full_round_trip_via_routes(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Upload screenshot → step page → submit details → row enriched."""
    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    _login(client, "org")
    # Step 1: upload.
    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "stat.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/details-from-ocr/" in resp.headers["Location"]
    attempt_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    # Step 2: step page renders with mock-derived stats + skills.
    resp = client.get(
        f"/official/{race_id}/results/{result_id}/details-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Confirm parsed details" in body
    # Mock provider stats are in inputs as values.
    assert 'value="1100"' in body  # speed
    # Mock provider skill suggestion appears in a skill_name input.
    assert "Warning Shot!" in body

    # Step 3: submit (using exactly the values the step page rendered).
    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details",
        data={
            "speed": "1100",
            "stamina": "900",
            "power": "1000",
            "guts": "600",
            "wisdom": "800",
            "strategy": "Front",
            "skill_name_0": "Warning Shot!",
            "skill_name_1": "Accelerator X",
            "skill_name_2": "Made-up Skill",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        result = db.session.get(OfficialRaceResult, result_id)
        assert result.speed == 1100
        assert result.stamina == 900
        assert result.strategy == "Front"
        skills = (
            db.session.query(OfficialRaceResultSkill)
            .filter_by(official_race_result_id=result_id)
            .order_by(OfficialRaceResultSkill.position)
            .all()
        )
        assert len(skills) == 3
        names = [s.skill.name_en if s.skill else s.raw_ocr_text for s in skills]
        assert names == ["Warning Shot!", "Accelerator X", "Made-up Skill"]
        # The third one didn't match the catalogue.
        assert skills[2].skill_id is None


def test_step_page_merges_existing_with_newly_parsed_skills(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Re-uploading a second screenshot should preserve already-saved
    skills and append the new ones (case-insensitive dedupe). Uma skill
    lists span two screens in-game, so the organiser uploads twice."""
    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    _login(client, "org")
    # First upload + save: persists the mock skills onto the result.
    client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "first.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    # Submit with the same names the mock returns.
    client.post(
        f"/official/{race_id}/results/{result_id}/details",
        data={
            "speed": "1100",
            "skill_name_0": "Warning Shot!",
            "skill_name_1": "Accelerator X",
            "skill_name_2": "Made-up Skill",
        },
        follow_redirects=False,
    )

    # Second upload — the step page should now show the union of saved
    # skills + freshly parsed ones, deduped case-insensitively.
    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "second.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    attempt_id_2 = int(resp.headers["Location"].rsplit("/", 1)[-1])

    resp = client.get(
        f"/official/{race_id}/results/{result_id}/details-from-ocr/{attempt_id_2}"
    )
    body = resp.data.decode()
    # All three saved skills appear in the editable form once each.
    # Count via the input's `name=` attribute rather than raw `value="..."`
    # since the autocomplete datalist also emits <option value="…">
    # entries for catalogue skills, which aren't user-editable.
    import re
    inputs_with_value = re.findall(
        r'<input[^>]*name="skill_name_\d+"[^>]*value="([^"]+)"', body
    )
    assert inputs_with_value.count("Warning Shot!") == 1
    assert inputs_with_value.count("Accelerator X") == 1
    assert inputs_with_value.count("Made-up Skill") == 1


def test_step_page_renders_skill_datalist(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The step page should ship a <datalist id='skill-names'>
    populated from the UmaSkill catalogue, and every editable
    skill_name_<i> input should opt into it via list='skill-names'."""
    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)  # seeds 'Warning Shot!' + 'Accelerator X'
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    _login(client, "org")
    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "stat.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    attempt_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    resp = client.get(
        f"/official/{race_id}/results/{result_id}/details-from-ocr/{attempt_id}"
    )
    body = resp.data.decode()
    # The datalist exists with the seeded catalogue entries.
    assert '<datalist id="skill-names">' in body
    assert '<option value="Warning Shot!">' in body
    assert '<option value="Accelerator X">' in body
    # Every editable input opts into the datalist + disables browser
    # history autocomplete (which would otherwise compete with it).
    import re
    inputs = re.findall(r'<input[^>]*name="skill_name_\d+"[^>]*>', body)
    assert inputs, "expected at least one skill_name input"
    for tag in inputs:
        assert 'list="skill-names"' in tag
        assert 'autocomplete="off"' in tag


def test_step_page_datalist_only_lists_enabled_skills(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """A disabled UmaSkill row shouldn't pollute the typeahead — keeps
    the dropdown focused on the current catalogue."""
    app.config["OCR_PROVIDER"] = "mock"
    host = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context():
        from uma_ladder.models import UmaSkill
        db.session.add_all([
            UmaSkill(
                gametora_id=10071,
                name_en="EnabledOne",
                is_unique=False,
                is_inherited=False,
                enabled=True,
            ),
            UmaSkill(
                gametora_id=10072,
                name_en="DisabledOne",
                is_unique=False,
                is_inherited=False,
                enabled=False,
            ),
        ])
        db.session.commit()

    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")
    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "stat.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    attempt_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    resp = client.get(
        f"/official/{race_id}/results/{result_id}/details-from-ocr/{attempt_id}"
    )
    body = resp.data.decode()
    assert '<option value="EnabledOne">' in body
    assert "DisabledOne" not in body


def test_non_organizer_cannot_upload_details(
    client: FlaskClient, app: Flask, make_user
) -> None:
    app.config["OCR_PROVIDER"] = "mock"
    host = make_user(username="org", role=Role.ORGANIZER)
    make_user(username="alice", role=Role.USER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    _login(client, "alice")
    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "stat.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 403
