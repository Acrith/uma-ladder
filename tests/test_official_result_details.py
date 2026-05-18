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


# ─── PR-OCR6: tier dropdown + inherited badge + position-aware matcher ─


def test_confirm_page_renders_tier_dropdown(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-OCR6 — when the parsed candidate has multiple tier
    variants (◎/○/×), the confirm template renders a <select>
    listing all of them so the organiser picks which tier was
    actually in the screenshot."""
    from uma_ladder.models import OcrParseAttempt, OcrParseStatus, UploadedImage
    from uma_ladder.models.enums import UploadPurpose

    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    with app.app_context():
        # Three tier variants of Right-Handed.
        db.session.add_all(
            [
                UmaSkill(
                    gametora_id=20011,
                    name_en="Right-Handed ◎",
                    enabled=True,
                    is_inherited=False,
                ),
                UmaSkill(
                    gametora_id=20012,
                    name_en="Right-Handed ○",
                    enabled=True,
                    is_inherited=False,
                ),
                UmaSkill(
                    gametora_id=20013,
                    name_en="Right-Handed ×",
                    enabled=True,
                    is_inherited=False,
                ),
            ]
        )
        image = UploadedImage(
            uploader_user_id=host["id"],
            storage_key=f"test-rh-{result_id}.png",
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
                "rows": [
                    {"raw_line": "Skills Inspiration Career Info"},
                    {"raw_line": "Right-Handed"},
                    {"raw_line": "Close"},
                ],
                "stats": {},
                "skills": [],
            },
            confidence_json={},
            status=OcrParseStatus.PARSED,
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = attempt.id

    resp = client.get(
        f"/official/{race_id}/results/{result_id}"
        f"/details-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    # A <select> element should render with all three tier
    # options. Look for both the dropdown opening tag and each
    # option value.
    assert "<select" in body
    assert 'name="skill_name_0"' in body
    assert 'value="Right-Handed ◎"' in body
    assert 'value="Right-Handed ○"' in body
    assert 'value="Right-Handed ×"' in body


def test_confirm_page_renders_inherited_badge(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-OCR6 — when the parsed candidate is_inherited=True (a
    later-slot occurrence of an ult name), the confirm template
    renders an `inherited` badge next to the input so the
    organiser knows the slot is a gene-version."""
    from uma_ladder.models import OcrParseAttempt, OcrParseStatus, UploadedImage
    from uma_ladder.models.enums import UploadPurpose

    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    with app.app_context():
        # Inherited-dupe pair for Anchors Aweigh!.
        db.session.add_all(
            [
                UmaSkill(
                    gametora_id=30001,
                    name_en="Anchors Aweigh!",
                    enabled=True,
                    is_inherited=False,
                ),
                UmaSkill(
                    gametora_id=30002,
                    name_en="Anchors Aweigh!",
                    enabled=True,
                    is_inherited=True,
                ),
            ]
        )
        image = UploadedImage(
            uploader_user_id=host["id"],
            storage_key=f"test-aa-{result_id}.png",
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
                "rows": [
                    {"raw_line": "Skills Inspiration Career Info"},
                    {"raw_line": "Anchors Aweigh!"},
                    {"raw_line": "Anchors Aweigh!"},
                    {"raw_line": "Close"},
                ],
                "stats": {},
                "skills": [],
            },
            confidence_json={},
            status=OcrParseStatus.PARSED,
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = attempt.id

    resp = client.get(
        f"/official/{race_id}/results/{result_id}"
        f"/details-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    # Both rows render — first is innate (no badge), second is
    # inherited (badge present somewhere on the page).
    assert ">inherited<" in body or 'inherited\n' in body or "inherited" in body
    # Catch the actual badge span — title attribute is distinctive.
    assert "Inherited (gene-version)" in body


def test_match_skill_names_inherited_position_rule(
    app: Flask,
) -> None:
    """PR-OCR6 — the save-side matcher prefers non-inherited at
    first occurrence and inherited at subsequent occurrences. So
    saving ['Anchors Aweigh!', 'Anchors Aweigh!'] resolves to two
    different UmaSkill ids."""
    from uma_ladder.services import official as official_service

    with app.app_context():
        innate = UmaSkill(
            gametora_id=30100,
            name_en="Anchors Aweigh!",
            enabled=True,
            is_inherited=False,
        )
        inherited = UmaSkill(
            gametora_id=30101,
            name_en="Anchors Aweigh!",
            enabled=True,
            is_inherited=True,
        )
        db.session.add_all([innate, inherited])
        db.session.commit()
        matches = official_service._match_skill_names(
            ["Anchors Aweigh!", "Anchors Aweigh!"]
        )
        # Two results.
        assert len(matches) == 2
        # First is the non-inherited (innate unique).
        assert matches[0][1] == innate.id
        # Second is the inherited (gene-version).
        assert matches[1][1] == inherited.id


def test_match_skill_names_tier_variants_resolve_directly(
    app: Flask,
) -> None:
    """When the form posts the full tier-suffixed name (the
    dropdown picks emit ``Right-Handed ○`` etc.), the matcher
    resolves directly to that exact catalog row."""
    from uma_ladder.services import official as official_service

    with app.app_context():
        double_circle = UmaSkill(
            gametora_id=30200,
            name_en="Right-Handed ◎",
            enabled=True,
            is_inherited=False,
        )
        single_circle = UmaSkill(
            gametora_id=30201,
            name_en="Right-Handed ○",
            enabled=True,
            is_inherited=False,
        )
        db.session.add_all([double_circle, single_circle])
        db.session.commit()
        matches = official_service._match_skill_names(
            ["Right-Handed ◎", "Right-Handed ○"]
        )
        assert matches[0][1] == double_circle.id
        assert matches[1][1] == single_circle.id


# ─── PR-A1: aptitudes round-trip ─────────────────────────────────


def test_aptitudes_pre_fill_from_sheet_extractor(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-A1 — the confirm page renders aptitude selectors with the
    values the sheet extractor parsed pre-selected. Driven by the
    Tamamo fixture which has all three aptitude rows populated."""
    from uma_ladder.models import OcrParseAttempt, OcrParseStatus, UploadedImage
    from uma_ladder.models.enums import UploadPurpose

    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    with app.app_context():
        image = UploadedImage(
            uploader_user_id=host["id"],
            storage_key=f"apt-test-{result_id}.png",
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
        db.session.commit()
        attempt_id = attempt.id

    resp = client.get(
        f"/official/{race_id}/results/{result_id}"
        f"/details-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    # Aptitude selects render with the extractor's values selected.
    # Tamamo fixture: Turf=A Dirt=F (track), Sprint=G Mile=B
    # Medium=A Long=A (distance), Front=G Pace=A Late=A End=A (style).
    import re

    def selected_for(field: str) -> str | None:
        m = re.search(
            rf'<select name="aptitude_{field}"[^>]*>.*?<option value="([^"]*)"\s+selected>',
            body,
            re.DOTALL,
        )
        return m.group(1) if m else None

    assert selected_for("track_turf") == "A"
    assert selected_for("track_dirt") == "F"
    assert selected_for("distance_sprint") == "G"
    assert selected_for("distance_mile") == "B"
    assert selected_for("distance_medium") == "A"
    assert selected_for("distance_long") == "A"
    assert selected_for("style_front") == "G"
    assert selected_for("style_pace") == "A"
    assert selected_for("style_late") == "A"
    assert selected_for("style_end") == "A"


def test_aptitudes_save_round_trip(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """POST aptitude_<category>_<slot> fields → persisted on the
    result row → re-rendered on the race detail page as badge text."""
    from uma_ladder.models import OfficialRaceResult

    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details",
        data={
            "speed": "1100",
            "aptitude_track_turf": "A",
            "aptitude_track_dirt": "F",
            "aptitude_distance_sprint": "G",
            "aptitude_distance_mile": "B",
            "aptitude_distance_medium": "S",
            "aptitude_distance_long": "A",
            "aptitude_style_front": "G",
            "aptitude_style_pace": "A",
            "aptitude_style_late": "A",
            "aptitude_style_end": "A",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        row = db.session.get(OfficialRaceResult, result_id)
        assert row.aptitudes == {
            "track": {"turf": "A", "dirt": "F"},
            "distance": {
                "sprint": "G",
                "mile": "B",
                "medium": "S",
                "long": "A",
            },
            "style": {
                "front": "G",
                "pace": "A",
                "late": "A",
                "end": "A",
            },
        }

    # Race detail page renders the aptitudes — verify at least one
    # badge surface shows up.
    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # The "Track" / "Distance" / "Style" category labels appear in
    # the per-result card now.
    assert "Track" in body
    # PR-A3: aptitude S = utx_ico_statusrank_07.png with title="Aptitude S".
    assert "utx_ico_statusrank_07.png" in body
    assert 'title="Aptitude S"' in body


def test_aptitudes_empty_field_omits_slot(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Submitting a blank value for an aptitude slot leaves it out
    of the stored dict — partial OCR runs save what they have."""
    from uma_ladder.models import OfficialRaceResult

    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    client.post(
        f"/official/{race_id}/results/{result_id}/details",
        data={
            "speed": "1100",
            "aptitude_track_turf": "A",
            "aptitude_track_dirt": "",  # blank — should be skipped
            # distance slots entirely absent
            "aptitude_style_front": "G",
        },
        follow_redirects=False,
    )

    with app.app_context():
        row = db.session.get(OfficialRaceResult, result_id)
        assert row.aptitudes == {
            "track": {"turf": "A"},
            "style": {"front": "G"},
        }


def test_uma_score_pre_fills_from_sheet_header(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-A6 — the per-result confirm page renders the parsed
    `uma_score` from the sheet header into the Uma score input."""
    from uma_ladder.models import OcrParseAttempt, OcrParseStatus, UploadedImage
    from uma_ladder.models.enums import UploadPurpose

    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    with app.app_context():
        image = UploadedImage(
            uploader_user_id=host["id"],
            storage_key=f"a6-prefill-{result_id}.png",
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
        db.session.commit()
        attempt_id = attempt.id

    resp = client.get(
        f"/official/{race_id}/results/{result_id}"
        f"/details-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    # The sheet extractor reads "17,307 Trainer Yuuta" → uma_score=17307
    # and the confirm page renders that into the input.
    import re

    m = re.search(r'name="uma_score"[^>]*value="(\d+)"', body)
    assert m and m.group(1) == "17307"


def test_uma_score_save_round_trip_renders_rank_glyph(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-A6 — POST uma_score → persisted on the result row →
    re-rendered on the race detail page with the matching rank
    icon next to the uma name."""
    from uma_ladder.models import OfficialRaceResult

    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details",
        data={
            "speed": "1197",
            "uma_score": "17307",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        row = db.session.get(OfficialRaceResult, result_id)
        assert row.uma_score == 17307

    # Race detail page renders 17,307 with the S+ rank icon
    # (idx 15 → ui_statusrank_15.png) next to the uma name.
    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "ui_statusrank_15.png" in body
    assert 'alt="S+"' in body
    # Score renders with thousands separator.
    assert "17,307" in body


def test_uma_score_zero_or_missing_skips_render(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """When no uma_score is saved on the result, the race detail
    page should NOT render a rank glyph — keeps older results
    (no score saved) clean rather than showing a stray G icon."""
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, _result_id = _setup_completed_race(app, host_id=host["id"])

    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # No statusrank glyph for the (uma_score=None) result.
    # We can't blanket-assert no statusrank URLs because stat icons
    # share the same folder — so check the format-with-comma marker
    # the rank_score_value macro uses for the numeric score.
    assert "title=\"Rank " not in body


def test_race_detail_renders_effective_aptitude_stats(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-OCR21 — the per-result stat grid shows an aptitude-
    adjusted "eff" line beneath each modifiable stat (Speed via
    distance apt, Power via surface apt, Wisdom via style apt).
    The two race-context calibration points the doc gave
    (S = +10.25%, B = -19%) are pinned here against a hand-built
    result row."""
    from uma_ladder.models import OfficialRaceResult

    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    with app.app_context():
        row = db.session.get(OfficialRaceResult, result_id)
        row.speed = 1200
        row.power = 1200
        row.wisdom = 1000
        row.strategy = "Pace"
        # Distance S on Medium (race preset distance_category) →
        # Speed × 1.1025 = 1323. Surface B on Turf (race preset
        # surface) → Power × 0.81 = 972. Style C on Pace →
        # Wisdom × 0.75 = 750.
        row.aptitudes = {
            "track":    {"turf": "B", "dirt": "G"},
            "distance": {"sprint": "G", "mile": "A",
                         "medium": "S", "long": "A"},
            "style":    {"front": "A", "pace": "C",
                         "late": "A", "end": "A"},
        }
        db.session.commit()

    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Speed: S on Medium → 1200 → 1323. Power: B on Turf → 972.
    # Wisdom: C on Pace → 750.
    assert "eff 1323" in body
    assert "eff 972" in body
    assert "eff 750" in body
    # The tint hint shows up too — green for the buff, rose for the
    # penalties.
    assert "text-emerald-300" in body
    assert "text-rose-300" in body


def test_race_detail_no_effective_line_without_aptitudes(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """When the result has stats but no aptitudes saved, the
    effective-stat line collapses to em-dashes — we don't invent
    a modifier we don't have data for."""
    from uma_ladder.models import OfficialRaceResult

    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    with app.app_context():
        row = db.session.get(OfficialRaceResult, result_id)
        row.speed = 1200
        row.power = 1100
        # aptitudes left None.
        db.session.commit()

    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # No "eff <number>" lines anywhere on the page.
    import re

    assert not re.search(r"eff \d+", body)


def test_race_detail_grays_out_non_applicable_skill(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-SK4 — when a result has a green skill whose catalog
    condition doesn't match the race (e.g. a Left-Handed skill
    on a Right-Handed track), the chip renders with `opacity-60
    line-through grayscale` and a `title="Doesn't apply..."`
    tooltip. The applies-skill chip stays cyan.
    """
    from uma_ladder.models import (
        OfficialRaceResult,
        OfficialRaceResultSkill,
        SkillCondition,
        UmaSkill,
    )

    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    with app.app_context():
        # Race preset already says Tokyo / Turf / Medium / Left
        # per _setup_completed_race. Make our "Left-Handed" skill
        # apply (matches preset.direction) and "Right-Handed"
        # skill NOT apply (different direction).
        left_skill = UmaSkill(
            gametora_id=200021, name_en="Left-Handed Test ◎",
            is_unique=False, is_inherited=False, enabled=True,
        )
        right_skill = UmaSkill(
            gametora_id=200011, name_en="Right-Handed Test ◎",
            is_unique=False, is_inherited=False, enabled=True,
        )
        db.session.add_all([left_skill, right_skill])
        db.session.commit()
        db.session.add_all([
            SkillCondition(skill_id=left_skill.id, direction="Left", buff_speed=60),
            SkillCondition(skill_id=right_skill.id, direction="Right", buff_speed=60),
        ])
        # Attach both skills to the result.
        result = db.session.get(OfficialRaceResult, result_id)
        db.session.add_all([
            OfficialRaceResultSkill(
                official_race_result_id=result.id,
                skill_id=left_skill.id,
                position=0,
            ),
            OfficialRaceResultSkill(
                official_race_result_id=result.id,
                skill_id=right_skill.id,
                position=1,
            ),
        ])
        db.session.commit()

    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Both skill names render.
    assert "Left-Handed Test ◎" in body
    assert "Right-Handed Test ◎" in body
    # The doesn't-apply title shows somewhere (attached to the
    # Right-Handed chip on this Left-direction race).
    assert "Doesn't apply to this race" in body
    # The cyan styling stays on the applying chip; the rose-free
    # gray treatment shows the rendered template took the
    # inapplicable branch at least once.
    assert "opacity-60" in body
    assert "line-through" in body


def test_race_detail_renders_strategy_chip(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-OCR17 — race detail per-result card surfaces the saved
    `strategy` value (Front / Pace / Late / End) as a chip. The
    field was being saved correctly via PR-OCR13's auto-fill from
    the OCR position keyword, but the detail template never read
    it. Without this chip the user has to open the per-result
    details form to see what strategy was saved."""
    from uma_ladder.models import OfficialRaceResult

    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    with app.app_context():
        row = db.session.get(OfficialRaceResult, result_id)
        row.strategy = "Pace"
        db.session.commit()

    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # "Pace" renders inside the per-result card. Don't constrain on
    # the exact chip markup — the chip() macro is shared and may
    # evolve. Just check the text + the cyan-tone class hint shows.
    assert ">Pace<" in body or "Pace</span>" in body or "Pace" in body
    # Cyan tone is what the confirm-page chip uses too (PR-OCR13).
    assert "text-cyan-300" in body


def test_race_detail_renders_stat_rank_icon(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-A2 — race detail per-result card renders the official
    Cygames status-rank icon for each stat value. Smoke-tests the
    img tag + filename mapping."""
    from uma_ladder.models import OfficialRaceResult

    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])

    with app.app_context():
        row = db.session.get(OfficialRaceResult, result_id)
        # Speed 1200 → index 17 (SS+) → ui_statusrank_17.png.
        row.speed = 1200
        # Stamina 648 → index 10 (B) → ui_statusrank_10.png.
        row.stamina = 648
        db.session.commit()

    resp = client.get(f"/official/{race_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # The two icon filenames appear in the rendered img URLs.
    assert "ui_statusrank_17.png" in body
    assert "ui_statusrank_10.png" in body
    # Alt-text carries the human-readable label for accessibility.
    assert 'alt="SS+"' in body
    assert 'alt="B"' in body


def test_confirm_page_renders_all_screenshots_after_multi_upload(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-OCR10 — when 2 screenshots are uploaded, the confirm page
    renders BOTH images in the source-screenshot strip, not just
    the primary attempt's. The user reported "preview shows only
    one thumbnail after uploading" which was this gap."""
    from uma_ladder.models import OcrParseAttempt, UploadedImage

    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    resp = client.post(
        f"/official/{race_id}/results/{result_id}/details-screenshot",
        data={
            "image": [
                (io.BytesIO(_png_bytes()), "first.png"),
                (io.BytesIO(_png_bytes()), "second.png"),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    confirm_path = resp.headers["Location"]

    with app.app_context():
        image_ids = sorted(
            i.id for i in db.session.query(UploadedImage).all()
        )
        # Sanity: two images were saved.
        assert len(image_ids) == 2
        attempts = (
            db.session.query(OcrParseAttempt)
            .order_by(OcrParseAttempt.id)
            .all()
        )
        assert len(attempts) == 2

    resp = client.get(confirm_path)
    assert resp.status_code == 200
    body = resp.data.decode()
    # Both image-serve URLs should appear on the confirm page.
    for img_id in image_ids:
        assert f"/ocr/uploads/{img_id}" in body
    # And the strip header reflects the count.
    assert "Source screenshots" in body


def test_multi_upload_persists_aptitudes_to_parsed_json(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-OCR9 — fixes the silent-aptitudes-loss bug: when an
    organiser uploads 2 screenshots together, the merged parsed_json
    must carry aptitudes so the confirm-route fallback finds them.

    Previously: route stored stats + skills only → confirm route
    re-extracted from cleared rows → empty aptitudes → form blank.
    """
    from uma_ladder.models import OcrParseAttempt

    app.config["OCR_PROVIDER"] = "mock"
    _seed_skills(app)
    host = make_user(username="org", role=Role.ORGANIZER)
    race_id, result_id = _setup_completed_race(app, host_id=host["id"])
    _login(client, "org")

    # Two uploads — first carries Tamamo's full sheet (with
    # aptitudes), second is a stub. The merged primary attempt's
    # parsed_json must include the aptitudes dict so the confirm
    # page renders the dropdowns pre-selected.
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
    with app.app_context():
        primary = (
            db.session.query(OcrParseAttempt)
            .order_by(OcrParseAttempt.id)
            .first()
        )
        # Even with empty mock-OCR aptitudes (no real sheet),
        # the key MUST be present in parsed_json post-merge — the
        # confirm route's fallback relies on it.
        assert "aptitudes" in (primary.parsed_json or {})


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
