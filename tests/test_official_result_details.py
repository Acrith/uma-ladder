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
    # All three saved skills appear (none dropped). Mock OCR returns the
    # same set as before, so dedupe must collapse them — there should be
    # no duplicate skill_name input.
    assert body.count('value="Warning Shot!"') == 1
    assert body.count('value="Accelerator X"') == 1
    assert body.count('value="Made-up Skill"') == 1


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
