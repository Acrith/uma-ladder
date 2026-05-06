from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from werkzeug.datastructures import FileStorage

from uma_ladder.models import OcrParseStatus
from uma_ladder.services import ocr as ocr_service
from uma_ladder.services.ocr_google_vision import (
    FakeVisionTransport,
    GoogleVisionOcrProvider,
    VisionResponse,
    _parse_annotation,
    build_google_vision_provider,
    set_vision_transport,
)


def _png_bytes() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d76368686800000005000170d80b240000000049"
        "454e44ae426082"
    )


def _file_storage() -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(_png_bytes()),
        filename="race.png",
        content_type="image/png",
    )


def _word(text: str, x: int, y: int, h: int = 20, confidence: float = 0.95) -> dict:
    return {
        "symbols": [{"text": ch} for ch in text],
        "boundingBox": {
            "vertices": [
                {"x": x, "y": y},
                {"x": x + 10 * len(text), "y": y},
                {"x": x + 10 * len(text), "y": y + h},
                {"x": x, "y": y + h},
            ]
        },
        "confidence": confidence,
    }


def _annotation(words_per_row: list[list[dict]]) -> dict[str, Any]:
    return {
        "text": "fixture",
        "pages": [
            {
                "blocks": [
                    {
                        "paragraphs": [
                            {"words": [w for row in words_per_row for w in row]}
                        ]
                    }
                ]
            }
        ],
    }


# ---------- _parse_annotation ----------


def test_empty_annotation_returns_empty_rows() -> None:
    parse = _parse_annotation({})
    assert parse.rows == []


def test_clusters_words_by_y_into_rows() -> None:
    ann = _annotation(
        [
            [_word("1", 10, 10), _word("Special", 50, 10), _word("Week", 130, 10)],
            [_word("2", 10, 60), _word("Gold", 50, 60), _word("Ship", 110, 60)],
        ]
    )
    parse = _parse_annotation(ann)
    assert len(parse.rows) == 2
    assert parse.rows[0]["placement"] == 1
    assert parse.rows[0]["uma_name"] == "Special Week"
    assert parse.rows[1]["placement"] == 2
    assert parse.rows[1]["uma_name"] == "Gold Ship"


def test_first_token_not_an_int_in_range_keeps_full_line() -> None:
    ann = _annotation(
        [[_word("Random", 10, 10), _word("Header", 80, 10)]]
    )
    parse = _parse_annotation(ann)
    assert parse.rows[0]["placement"] is None
    assert parse.rows[0]["uma_name"] == "Random Header"


def test_first_token_too_large_for_placement_is_none() -> None:
    # 99 is outside [1, 30].
    ann = _annotation([[_word("99", 10, 10), _word("foo", 50, 10)]])
    parse = _parse_annotation(ann)
    assert parse.rows[0]["placement"] is None


def test_words_sorted_left_to_right_within_row() -> None:
    # Insert words out of x-order; parser must sort them.
    ann = _annotation(
        [[_word("Week", 130, 10), _word("1", 10, 10), _word("Special", 50, 10)]]
    )
    parse = _parse_annotation(ann)
    assert parse.rows[0]["placement"] == 1
    assert parse.rows[0]["uma_name"] == "Special Week"


def test_overall_confidence_averaged() -> None:
    ann = _annotation(
        [[_word("1", 10, 10, confidence=1.0), _word("Foo", 50, 10, confidence=0.6)]]
    )
    parse = _parse_annotation(ann)
    assert parse.confidence["overall"] == pytest.approx(0.8, abs=0.01)


# ---------- Provider with FakeVisionTransport ----------


def test_provider_requires_api_key() -> None:
    with pytest.raises(RuntimeError):
        GoogleVisionOcrProvider(api_key="")


def test_provider_calls_transport_and_returns_parse(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(_png_bytes())

    transport = FakeVisionTransport(
        VisionResponse(
            ok=True,
            body={
                "responses": [
                    {
                        "fullTextAnnotation": _annotation(
                            [
                                [
                                    _word("1", 10, 10),
                                    _word("Tokai", 50, 10),
                                    _word("Teio", 120, 10),
                                ]
                            ]
                        )
                    }
                ]
            },
            status_code=200,
            error=None,
        )
    )
    provider = GoogleVisionOcrProvider(api_key="K", transport=transport)
    parse = provider.parse(img)

    assert len(transport.calls) == 1
    url, body = transport.calls[0]
    assert "key=K" in url
    assert "requests" in body
    assert parse.rows[0]["placement"] == 1
    assert parse.rows[0]["uma_name"] == "Tokai Teio"


def test_provider_handles_empty_responses_array(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(_png_bytes())
    transport = FakeVisionTransport(
        VisionResponse(ok=True, body={"responses": []}, status_code=200, error=None)
    )
    parse = GoogleVisionOcrProvider(api_key="K", transport=transport).parse(img)
    assert parse.rows == []


def test_provider_raises_on_api_error_field(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(_png_bytes())
    transport = FakeVisionTransport(
        VisionResponse(
            ok=True,
            body={"responses": [{"error": {"code": 7, "message": "denied"}}]},
            status_code=200,
            error=None,
        )
    )
    with pytest.raises(RuntimeError) as exc:
        GoogleVisionOcrProvider(api_key="K", transport=transport).parse(img)
    assert "denied" in str(exc.value)


def test_provider_raises_on_transport_failure(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(_png_bytes())
    transport = FakeVisionTransport(
        VisionResponse(ok=False, body=None, status_code=500, error="boom")
    )
    with pytest.raises(RuntimeError) as exc:
        GoogleVisionOcrProvider(api_key="K", transport=transport).parse(img)
    assert "boom" in str(exc.value)


# ---------- Integration with services/ocr ----------


def test_get_provider_google_vision_requires_api_key(app: Flask) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "google_vision"
        app.config["GOOGLE_VISION_API_KEY"] = None
        with pytest.raises(RuntimeError) as exc:
            ocr_service.get_provider()
        assert "GOOGLE_VISION_API_KEY" in str(exc.value)


def test_get_provider_google_vision_with_key(app: Flask) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "google_vision"
        app.config["GOOGLE_VISION_API_KEY"] = "K"
        provider = ocr_service.get_provider()
        assert isinstance(provider, GoogleVisionOcrProvider)


def test_run_parse_records_failed_status_when_provider_raises(
    app: Flask, make_user
) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        app.config["OCR_PROVIDER"] = "google_vision"
        app.config["GOOGLE_VISION_API_KEY"] = "K"
        set_vision_transport(
            FakeVisionTransport(
                VisionResponse(ok=False, body=None, status_code=500, error="boom")
            )
        )
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        assert attempt.status == OcrParseStatus.FAILED
        assert "boom" in (attempt.error_message or "")


def test_run_parse_with_fake_transport_succeeds(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        app.config["OCR_PROVIDER"] = "google_vision"
        app.config["GOOGLE_VISION_API_KEY"] = "K"
        set_vision_transport(
            FakeVisionTransport(
                VisionResponse(
                    ok=True,
                    body={
                        "responses": [
                            {
                                "fullTextAnnotation": _annotation(
                                    [
                                        [
                                            _word("1", 10, 10),
                                            _word("Special", 50, 10),
                                            _word("Week", 130, 10),
                                        ],
                                        [
                                            _word("2", 10, 60),
                                            _word("Gold", 50, 60),
                                            _word("Ship", 110, 60),
                                        ],
                                    ]
                                )
                            }
                        ]
                    },
                    status_code=200,
                    error=None,
                )
            )
        )
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        assert attempt.status == OcrParseStatus.PARSED
        rows = attempt.parsed_json["rows"]
        assert [r["placement"] for r in rows] == [1, 2]
        assert rows[0]["uma_name"] == "Special Week"


def test_build_factory_called_through_get_provider(app: Flask) -> None:
    """Smoke: the lazy factory wires set_vision_transport into the provider."""
    with app.app_context():
        app.config["OCR_PROVIDER"] = "google_vision"
        app.config["GOOGLE_VISION_API_KEY"] = "K"
        canned = FakeVisionTransport(
            VisionResponse(ok=True, body={"responses": []}, status_code=200, error=None)
        )
        set_vision_transport(canned)
        provider = build_google_vision_provider()
        assert provider.transport is canned


# ---------- Stat-screen parsing ----------


def test_extracts_stats_from_stat_screen_layout() -> None:
    """A typical Uma stat screen lists Speed/Stamina/Power/Guts/Wisdom
    each with a number on the same row. Parser pairs them up."""
    ann = _annotation(
        [
            [_word("Speed", 10, 10), _word("1100", 100, 10)],
            [_word("Stamina", 10, 50), _word("900", 100, 50)],
            [_word("Power", 10, 90), _word("1000", 100, 90)],
            [_word("Guts", 10, 130), _word("600", 100, 130)],
            [_word("Wisdom", 10, 170), _word("800", 100, 170)],
        ]
    )
    parse = _parse_annotation(ann)
    assert parse.stats == {
        "speed": 1100,
        "stamina": 900,
        "power": 1000,
        "guts": 600,
        "wisdom": 800,
    }


def test_stats_fall_through_to_next_line_when_label_row_has_no_number() -> None:
    """Some game UIs put the label above the value rather than beside it."""
    ann = _annotation(
        [
            [_word("Speed", 10, 10)],
            [_word("1100", 10, 50)],
            [_word("Stamina", 10, 90)],
            [_word("900", 10, 130)],
        ]
    )
    parse = _parse_annotation(ann)
    assert parse.stats == {"speed": 1100, "stamina": 900}


def test_stats_recognise_wit_alias_for_wisdom() -> None:
    ann = _annotation(
        [[_word("Wit", 10, 10), _word("750", 100, 10)]]
    )
    parse = _parse_annotation(ann)
    assert parse.stats == {"wisdom": 750}


def test_skill_candidates_skip_stat_rows_and_placements() -> None:
    """Stat label/value rows + placement rows shouldn't pollute the
    skill candidate list. Real skill-name rows pass through."""
    ann = _annotation(
        [
            [_word("Speed", 10, 10), _word("1100", 100, 10)],
            [_word("Warning", 10, 50), _word("Shot!", 100, 50)],
            [_word("Hot", 10, 90), _word("Blooded", 50, 90), _word("Amigo", 150, 90)],
            [_word("1", 10, 130), _word("Special", 40, 130), _word("Week", 130, 130)],
            [_word("3500", 10, 170)],  # pure-number row
        ]
    )
    parse = _parse_annotation(ann)
    assert "Warning Shot!" in parse.skills
    assert "Hot Blooded Amigo" in parse.skills
    # Placement / pure-number / stat rows excluded.
    assert not any("Special" in s for s in parse.skills)
    assert not any("3500" in s for s in parse.skills)
    assert not any("Speed" in s for s in parse.skills)


def test_skill_candidates_dedupe_repeats() -> None:
    ann = _annotation(
        [
            [_word("Warning", 10, 10), _word("Shot!", 100, 10)],
            [_word("Warning", 10, 50), _word("Shot!", 100, 50)],
        ]
    )
    parse = _parse_annotation(ann)
    assert parse.skills == ["Warning Shot!"]


def test_skill_candidates_drop_overlong_lines() -> None:
    """Misclustered paragraph-length lines aren't useful skill candidates."""
    long_text = "x" * 100
    ann = _annotation([[_word(long_text, 10, 10)]])
    parse = _parse_annotation(ann)
    assert long_text not in parse.skills


def test_placement_screen_parse_unchanged_by_stat_extraction() -> None:
    """Sanity: a pure result-summary screenshot still produces the
    same row output as before. Stats are empty (no labels detected)
    and skills are empty too, since every line begins with a placement
    digit and gets filtered out as a non-skill row."""
    ann = _annotation(
        [
            [_word("1", 10, 10), _word("Special", 50, 10), _word("Week", 130, 10)],
            [_word("2", 10, 60), _word("Gold", 50, 60), _word("Ship", 110, 60)],
        ]
    )
    parse = _parse_annotation(ann)
    assert parse.rows[0]["placement"] == 1
    assert parse.rows[0]["uma_name"] == "Special Week"
    assert parse.stats == {}
    assert parse.skills == []


# ---------- PR-I1 follow-up: orphan-followup merge ----------


def test_orphan_lines_after_placement_are_absorbed_into_uma_name() -> None:
    """When a placement row is followed by non-epithet lines without
    a placement, those lines get absorbed into the placement row's
    raw text. The structured-extraction pass (PR-I3) then peels a
    clean `uma_name` out of that raw text; the full concat is
    preserved as `raw_uma_name`."""
    ann = _annotation(
        [
            # Player 1 — placement row, then the player line below.
            # Matches the real game UI ordering (player segment is
            # always last in the entrant block).
            [_word("1", 10, 10), _word("Special", 50, 10), _word("Week", 130, 10)],
            [_word("Yuuta", 50, 60), _word("No.", 110, 60), _word("1", 150, 60), _word("Fav", 175, 60)],
            # Player 2 starts a new placement row.
            [_word("2", 10, 200), _word("Gold", 50, 200), _word("Ship", 130, 200)],
        ]
    )
    parse = _parse_annotation(ann)
    assert len(parse.rows) == 2
    p1 = parse.rows[0]
    assert p1["placement"] == 1
    # PR-I3: clean uma_name extracted from the merged raw text;
    # player_name extracted from the trailing "Yuuta No. 1 Fav" line.
    assert p1["uma_name"] == "Special Week"
    assert p1["player_name"] == "Yuuta"
    # Full merged concat preserved under raw_uma_name.
    assert "Yuuta" in p1["raw_uma_name"]
    p2 = parse.rows[1]
    assert p2["placement"] == 2
    assert p2["uma_name"] == "Gold Ship"


def test_absorption_caps_at_three_followups() -> None:
    """A runaway tail (e.g. footer / total lines below the last
    entrant) should not all merge into the last placement row."""
    ann = _annotation(
        [
            [_word("1", 10, 10), _word("Special", 50, 10), _word("Week", 130, 10)],
            [_word("epithet", 50, 60)],
            [_word("stats", 50, 110)],
            [_word("more", 50, 160)],
            [_word("footer", 50, 210)],  # 4th orphan — should not merge
            [_word("totals", 50, 260)],  # 5th orphan — should not merge
        ]
    )
    parse = _parse_annotation(ann)
    # 1 placement row + the leftover orphans (2 of them after the cap).
    assert parse.rows[0]["placement"] == 1
    absorbed = parse.rows[0]["uma_name"]
    assert "Special Week" in absorbed
    assert "epithet" in absorbed
    assert "stats" in absorbed
    assert "more" in absorbed
    # Cap reached — these stay as their own rows.
    assert "footer" not in absorbed
    assert "totals" not in absorbed
    leftover_names = [r["uma_name"] for r in parse.rows[1:]]
    assert "footer" in " ".join(leftover_names)
    assert "totals" in " ".join(leftover_names)


def test_ordinal_placements_are_recognised() -> None:
    """Uma Musume's result-summary screen renders placements as
    ordinals — "1st", "2nd", "3rd", "4th"... — not bare digits.
    Plain integers must keep working too (legacy / official-race
    screenshots already in production)."""
    ann = _annotation(
        [
            [_word("1st", 10, 10), _word("Gold", 50, 10), _word("Ship", 130, 10)],
            [_word("2nd", 10, 60), _word("Biwa", 50, 60), _word("Hayahide", 110, 60)],
            [_word("3rd", 10, 110), _word("Tamamo", 50, 110), _word("Cross", 150, 110)],
            [_word("4th", 10, 160), _word("Daiwa", 50, 160), _word("Scarlet", 130, 160)],
            # Plain digit still works for any non-ordinal screenshot.
            [_word("5", 10, 210), _word("Mejiro", 50, 210), _word("McQueen", 130, 210)],
        ]
    )
    parse = _parse_annotation(ann)
    assert [r["placement"] for r in parse.rows] == [1, 2, 3, 4, 5]
    assert parse.rows[0]["uma_name"] == "Gold Ship"
    assert parse.rows[1]["uma_name"] == "Biwa Hayahide"


def test_rank_column_header_does_not_pollute_uma_name() -> None:
    """The result screen has a "RANK" column label between entrants.
    The merge pass must skip it so we don't render "Gold Ship Yuuta
    No. 1 Fav RANK" as a single uma name."""
    ann = _annotation(
        [
            [_word("1st", 10, 10), _word("Gold", 50, 10), _word("Ship", 130, 10)],
            [_word("Yuuta", 50, 60), _word("No.", 110, 60), _word("1", 150, 60), _word("Fav", 175, 60)],
            [_word("RANK", 10, 110)],  # column header — must NOT merge
            [_word("2nd", 10, 160), _word("Biwa", 50, 160), _word("Hayahide", 110, 160)],
        ]
    )
    parse = _parse_annotation(ann)
    assert len(parse.rows) == 2
    p1 = parse.rows[0]
    assert p1["placement"] == 1
    # PR-I3: clean uma_name + raw_uma_name diagnostic concat.
    assert p1["uma_name"] == "Gold Ship"
    assert "Yuuta" in p1["raw_uma_name"]
    assert "RANK" not in p1["raw_uma_name"]


def test_epithet_line_attaches_to_next_entrant_not_previous() -> None:
    """Mirrors the real Uma Musume result screen — three entrants,
    each rendered as [skill-rank epithet][placement+uma][player info].
    Without epithet-aware routing the forward merge stole the next
    entrant's epithet onto the previous entrant. With routing, each
    entrant gets its OWN epithet."""
    ann = _annotation(
        [
            # Entrant 1: epithet ABOVE its placement row.
            [_word("SS", 10, 10), _word("Unpredictable", 40, 10), _word("End", 175, 10)],
            [_word("1st", 10, 60), _word("8", 50, 60), _word("Gold", 80, 60), _word("Ship", 130, 60)],
            [_word("Yuuta", 50, 110), _word("No.", 110, 110), _word("1", 150, 110), _word("Fav", 175, 110)],
            # Entrant 2: epithet ABOVE its placement row (sandwiched
            # between entrant 1's player line and entrant 2's main row).
            [_word("SS", 10, 200), _word("Victory", 40, 200), _word("Derived", 110, 200), _word("Pace", 200, 200)],
            [_word("2nd", 10, 250), _word("9", 50, 250), _word("Biwa", 80, 250), _word("Hayahide", 130, 250)],
            [_word("Acrith", 50, 300), _word("No.", 110, 300), _word("3", 150, 300), _word("Fav", 175, 300)],
            # Entrant 3.
            [_word("S", 10, 400), _word("White", 30, 400), _word("Lightning", 90, 400)],
            [_word("3rd", 10, 450), _word("3", 50, 450), _word("Tamamo", 80, 450), _word("Cross", 160, 450)],
            [_word("Yuuta", 50, 500), _word("No.", 110, 500), _word("4", 150, 500), _word("Fav", 175, 500)],
        ]
    )
    parse = _parse_annotation(ann)
    placement_rows = [r for r in parse.rows if r.get("placement") is not None]
    assert [r["placement"] for r in placement_rows] == [1, 2, 3]

    # PR-I3: epithet now lives in its own structured field rather
    # than being concatenated into uma_name. Each entrant carries
    # its OWN epithet — that's the routing fix this test guards.
    p1 = placement_rows[0]
    assert p1["uma_name"] == "Gold Ship"
    assert "Unpredictable" in p1["epithet"]
    assert "Victory Derived" not in (p1.get("epithet") or "")

    p2 = placement_rows[1]
    assert p2["uma_name"] == "Biwa Hayahide"
    assert "Victory Derived" in p2["epithet"]
    assert "White Lightning" not in (p2.get("epithet") or "")

    p3 = placement_rows[2]
    assert p3["uma_name"] == "Tamamo Cross"
    assert "White Lightning" in p3["epithet"]


def test_tightens_punctuation_spacing() -> None:
    """Vision tokenises punctuation as standalone words, so cluster
    join produces spaces around `:` `!` `.` etc. Tighten them back so
    "3 : 43.8" reads as "3:43.8" and "Lightning ! End" as "Lightning! End"."""
    from uma_ladder.services.ocr_google_vision import _tighten_punctuation

    assert _tighten_punctuation("Hello !") == "Hello!"
    assert _tighten_punctuation("Now That's White Lightning ! End") == (
        "Now That's White Lightning! End"
    )
    assert _tighten_punctuation("3 : 43.8") == "3:43.8"
    assert _tighten_punctuation("No . 1 Fav") == "No. 1 Fav"
    assert _tighten_punctuation("foo , bar ; baz .") == "foo, bar; baz."
    # Non-numeric colons (sentence-style) stay unchanged.
    assert _tighten_punctuation("Subject: foo bar") == "Subject: foo bar"
    # Empty / None.
    assert _tighten_punctuation("") == ""


def test_punctuation_tightening_applies_in_real_parse() -> None:
    """End-to-end: a placement row produced via _parse_annotation
    should have already-tightened punctuation in uma_name."""
    ann = _annotation(
        [
            [
                _word("1st", 10, 10),
                _word("8", 50, 10),
                _word("Gold", 80, 10),
                _word("Ship", 130, 10),
                _word("3", 200, 10),
                _word(":", 215, 10),
                _word("43.8", 230, 10),
            ],
        ]
    )
    parse = _parse_annotation(ann)
    # PR-I3: time_or_lengths is now its own structured field;
    # punctuation-tightening still applies before extraction.
    row = parse.rows[0]
    assert row.get("time_or_lengths") == "3:43.8"
    assert "3 : 43.8" not in (row.get("raw_uma_name") or "")


def test_parse_placement_row_fields_extracts_clean_uma_name() -> None:
    """Round-trip the canonical merged row shapes back to clean
    structured fields. The driving signal: form gets uma_name="Gold
    Ship", not the whole "SS Unpredictable End 8 Gold Ship 3:43.8 …"
    blob."""
    from uma_ladder.services.ocr_google_vision import _parse_placement_row_fields

    # 1st place — finishing time
    f1 = _parse_placement_row_fields(
        "SS Unpredictable End 8 Gold Ship 3:43.8 Yuuta No. 1 Fav"
    )
    assert f1["uma_name"] == "Gold Ship"
    assert f1["gate"] == 8
    assert f1["time_or_lengths"] == "3:43.8"
    assert f1["player_name"] == "Yuuta"
    assert f1["fav_rank"] == 1
    assert f1["epithet_rank"] == "SS"
    assert f1["epithet"] == "Unpredictable End"

    # 2nd place — lengths-back gap "3 1/2 L"
    f2 = _parse_placement_row_fields(
        "SS Victory Derived Pace 9 Biwa Hayahide 3 1/2 L Acrith No. 3 Fav"
    )
    assert f2["uma_name"] == "Biwa Hayahide"
    assert f2["gate"] == 9
    assert f2["time_or_lengths"] == "3 1/2 L"
    assert f2["player_name"] == "Acrith"
    assert f2["fav_rank"] == 3

    # 3rd place — short gap "1/2 L"
    f3 = _parse_placement_row_fields(
        "S Now That's White Lightning! End 3 Tamamo Cross 1/2 L Yuuta No. 4 Fav"
    )
    assert f3["uma_name"] == "Tamamo Cross"
    assert f3["gate"] == 3
    assert f3["time_or_lengths"] == "1/2 L"
    assert f3["player_name"] == "Yuuta"


def test_parse_placement_row_fields_handles_partial_data() -> None:
    """When extraction finds only some fields the rest stay absent
    and uma_name still produces a sensible value."""
    from uma_ladder.services.ocr_google_vision import _parse_placement_row_fields

    # Just an uma name, no surrounding metadata.
    f = _parse_placement_row_fields("Special Week")
    assert f["uma_name"] == "Special Week"
    assert "gate" not in f
    assert "time_or_lengths" not in f
    assert "player_name" not in f


def test_parse_annotation_emits_structured_fields_per_row() -> None:
    """End-to-end: a real parse run produces rows whose `uma_name`
    is the clean uma name and whose structured fields land alongside."""
    ann = _annotation(
        [
            [_word("SS", 10, 10), _word("Unpredictable", 40, 10), _word("End", 175, 10)],
            [_word("1st", 10, 60), _word("8", 50, 60), _word("Gold", 80, 60), _word("Ship", 130, 60), _word("3", 200, 60), _word(":", 215, 60), _word("43.8", 230, 60)],
            [_word("Yuuta", 50, 110), _word("No.", 110, 110), _word("1", 150, 110), _word("Fav", 175, 110)],
        ]
    )
    parse = _parse_annotation(ann)
    placement_rows = [r for r in parse.rows if r.get("placement") is not None]
    assert len(placement_rows) == 1
    row = placement_rows[0]
    assert row["placement"] == 1
    assert row["uma_name"] == "Gold Ship"
    assert row["gate"] == 8
    assert row["time_or_lengths"] == "3:43.8"
    assert row["player_name"] == "Yuuta"
    assert row["fav_rank"] == 1
    assert row["epithet_rank"] == "SS"
    assert row["epithet"] == "Unpredictable End"
    # The unmerged concatenation is preserved as raw_uma_name for
    # the review form's diagnostic line.
    assert "Gold Ship" in row["raw_uma_name"]
    assert "Yuuta" in row["raw_uma_name"]


def test_pre_placement_orphans_are_kept_not_dropped() -> None:
    """Header chrome (rows above the first placement) survives the
    merge pass — it lives as its own non-placement row so the route
    layer can hide it under "Other detected text" rather than
    silently dropping it."""
    ann = _annotation(
        [
            [_word("Result", 10, 10), _word("Summary", 80, 10)],  # pre-placement header
            [_word("1", 10, 100), _word("Special", 50, 100), _word("Week", 130, 100)],
        ]
    )
    parse = _parse_annotation(ann)
    placements = [r for r in parse.rows if r.get("placement") is not None]
    others = [r for r in parse.rows if r.get("placement") is None]
    assert len(placements) == 1
    assert len(others) == 1
    assert "Result" in others[0]["uma_name"]
