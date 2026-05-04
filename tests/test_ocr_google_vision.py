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
