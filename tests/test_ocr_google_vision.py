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
    """When a placement row is followed by lines that don't carry a
    placement, those lines get absorbed into the placement row's
    uma_name. Mirrors how Uma Musume's result-summary screen renders
    a single race entrant across 2-3 visual lines (uma name, epithet,
    stats)."""
    ann = _annotation(
        [
            # Player 1: placement digit + uma name on row 1, epithet
            # on row 2, stats blob on row 3.
            [_word("1", 10, 10), _word("Special", 50, 10), _word("Week", 130, 10)],
            [_word("[Princess", 50, 60), _word("of", 130, 60), _word("Pink]", 165, 60)],
            [_word("S+", 50, 110), _word("speed", 80, 110)],
            # Player 2 starts a new placement row.
            [_word("2", 10, 200), _word("Gold", 50, 200), _word("Ship", 130, 200)],
        ]
    )
    parse = _parse_annotation(ann)
    # Two race entrants, not five rows.
    assert len(parse.rows) == 2
    p1 = parse.rows[0]
    assert p1["placement"] == 1
    assert "Special Week" in p1["uma_name"]
    assert "[Princess of Pink]" in p1["uma_name"]
    assert "S+ speed" in p1["uma_name"]
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
