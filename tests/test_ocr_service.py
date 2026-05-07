from __future__ import annotations

import io
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.datastructures import FileStorage

from uma_ladder.extensions import db
from uma_ladder.models import (
    OcrParseAttempt,
    OcrParseStatus,
    OfficialRaceResult,
)
from uma_ladder.services import ocr as ocr_service


def _png_bytes() -> bytes:
    # smallest valid PNG: 1x1 red pixel
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d76368686800000005000170d80b240000000049"
        "454e44ae426082"
    )


def _file_storage(name: str = "race.png", data: bytes | None = None) -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(data or _png_bytes()),
        filename=name,
        content_type="image/png",
    )


def test_manual_provider_returns_empty_parse(app: Flask) -> None:
    p = ocr_service.ManualOcrProvider().parse(Path("/nope"))
    assert p.rows == []
    assert p.raw_text is None


def test_mock_provider_returns_fixed_rows(app: Flask) -> None:
    p = ocr_service.MockOcrProvider().parse(Path("/nope"))
    assert [r["placement"] for r in p.rows] == [1, 2, 3]
    assert p.raw_text == "MOCK OCR OUTPUT"


def test_get_provider_default_is_manual(app: Flask) -> None:
    with app.app_context():
        provider = ocr_service.get_provider()
        assert isinstance(provider, ocr_service.ManualOcrProvider)


def test_get_provider_respects_config(app: Flask) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        assert isinstance(ocr_service.get_provider(), ocr_service.MockOcrProvider)


def test_get_provider_unknown_raises(app: Flask) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "nope"
        with pytest.raises(RuntimeError):
            ocr_service.get_provider()


def test_save_uploaded_image_writes_file_and_row(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        assert image.id is not None
        assert image.size_bytes > 0
        assert image.original_filename == "race.png"
        assert ocr_service.image_path(image).exists()


def test_save_rejects_bad_extension(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context(), pytest.raises(ocr_service.OcrError):
        ocr_service.save_uploaded_image(
            _file_storage(name="evil.exe"),
            uploader_user_id=info["id"],
        )


def test_save_rejects_oversized(app: Flask, make_user, monkeypatch) -> None:
    info = make_user(username="alice", password="password123")
    monkeypatch.setattr(ocr_service, "_MAX_BYTES", 10)
    with app.app_context(), pytest.raises(ocr_service.OcrError):
        ocr_service.save_uploaded_image(
            _file_storage(data=b"x" * 100), uploader_user_id=info["id"]
        )


def test_run_parse_with_mock_returns_parsed(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        assert attempt.status == OcrParseStatus.PARSED
        assert attempt.parsed_json["rows"][0]["placement"] == 1


def test_run_parse_records_failure(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")

    class BoomProvider(ocr_service.OcrProvider):
        name = "boom"

        def parse(self, image_path):  # noqa: ARG002
            raise RuntimeError("boom")

    with app.app_context():
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image, provider=BoomProvider())
        assert attempt.status == OcrParseStatus.FAILED
        assert attempt.error_message == "boom"


def test_confirm_parse_writes_no_results_on_its_own(app: Flask, make_user) -> None:
    """The key invariant from PROJECT_INTENTIONS §13: OCR confirmation does not
    silently write race results."""
    info = make_user(username="alice", password="password123")
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        assert db.session.query(OfficialRaceResult).count() == 0
        ocr_service.confirm_parse(
            attempt.id, confirmed_by_user_id=info["id"]
        )
        assert db.session.query(OfficialRaceResult).count() == 0
        confirmed = db.session.get(OcrParseAttempt, attempt.id)
        assert confirmed.status == OcrParseStatus.CONFIRMED
        assert confirmed.confirmed_by_user_id == info["id"]


def test_confirm_parse_stores_edits(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=info["id"],
            edited_rows=[{"placement": 1, "uma_name": "Edited"}],
        )
        confirmed = db.session.get(OcrParseAttempt, attempt.id)
        assert confirmed.parsed_json == {
            "rows": [{"placement": 1, "uma_name": "Edited"}]
        }


def test_confirm_unknown_attempt_raises(app: Flask) -> None:
    with app.app_context(), pytest.raises(ocr_service.OcrError):
        ocr_service.confirm_parse(99999, confirmed_by_user_id=1)


# ---------- PR-J4 — draft-match link + screenshot lookup ----------


def _make_draft_match(app: Flask, host_id: int, opp_id: int | None = None) -> int:
    """Return id of a minimal DraftMatch row tied to a real Season."""
    from datetime import UTC, datetime, timedelta

    from uma_ladder.models import (
        DraftMatch,
        DraftMatchStatus,
        Season,
        SeasonStatus,
    )

    with app.app_context():
        s = db.session.query(Season).first()
        if s is None:
            now = datetime.now(UTC)
            s = Season(
                name="S",
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=10),
                status=SeasonStatus.ACTIVE,
            )
            db.session.add(s)
            db.session.commit()
        m = DraftMatch(
            season_id=s.id,
            host_user_id=host_id,
            opponent_user_id=opp_id,
            join_code=f"JC{host_id}-{opp_id}",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
        )
        db.session.add(m)
        db.session.commit()
        return m.id


def test_confirm_parse_stamps_draft_match_id(
    app: Flask, make_user
) -> None:
    """When the draft route confirms an OCR parse, the attempt should
    record which match it seeded so the completed-match card can
    look the screenshots up later."""
    info = make_user(username="alice", password="password123")
    match_id = _make_draft_match(app, host_id=info["id"])
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=info["id"],
            draft_match_id=match_id,
        )
        confirmed = db.session.get(OcrParseAttempt, attempt.id)
        assert confirmed.draft_match_id == match_id


def test_get_draft_match_screenshots_single_attempt(
    app: Flask, make_user
) -> None:
    info = make_user(username="alice", password="password123")
    match_id = _make_draft_match(app, host_id=info["id"])
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=info["id"],
            draft_match_id=match_id,
        )
        images = ocr_service.get_draft_match_screenshots(match_id)
        assert [i.id for i in images] == [image.id]


def test_get_draft_match_screenshots_multi_attempt_dedupes(
    app: Flask, make_user
) -> None:
    """Multi-screenshot uploads stash the extra image ids on the
    primary attempt's parsed_json. The lookup should walk both
    sources, preserve upload order, and dedupe."""
    info = make_user(username="alice", password="password123")
    match_id = _make_draft_match(app, host_id=info["id"])
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        primary_img = ocr_service.save_uploaded_image(
            _file_storage(name="a.png"), uploader_user_id=info["id"]
        )
        extra_img = ocr_service.save_uploaded_image(
            _file_storage(name="b.png"), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(primary_img)
        new_pj = dict(attempt.parsed_json or {})
        new_pj["screenshot_image_ids"] = [primary_img.id, extra_img.id]
        attempt.parsed_json = new_pj
        db.session.commit()
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=info["id"],
            draft_match_id=match_id,
        )
        images = ocr_service.get_draft_match_screenshots(match_id)
        # Primary first, extra second, no dupes.
        assert [i.id for i in images] == [primary_img.id, extra_img.id]


def test_get_draft_match_screenshots_skips_unconfirmed(
    app: Flask, make_user
) -> None:
    """Failed/parsed-but-not-confirmed attempts must not leak onto
    the completed-match card. Only confirmed ones count."""
    info = make_user(username="alice", password="password123")
    match_id = _make_draft_match(app, host_id=info["id"])
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=info["id"]
        )
        attempt = ocr_service.run_parse(image)
        # Stamp the link manually but don't confirm.
        attempt.draft_match_id = match_id
        db.session.commit()
        assert ocr_service.get_draft_match_screenshots(match_id) == []


def test_user_can_view_image_allows_opponent(
    app: Flask, make_user
) -> None:
    """PR-J4 — opponent of the linked match can view a screenshot
    they did NOT upload. The legacy uploader-only rule blocked this
    and was the immediate motivation for the helper."""
    host = make_user(username="host", password="x")
    opp = make_user(username="opp", password="x")
    stranger = make_user(username="snoop", password="x")
    match_id = _make_draft_match(app, host_id=host["id"], opp_id=opp["id"])
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        image = ocr_service.save_uploaded_image(
            _file_storage(), uploader_user_id=host["id"]
        )
        attempt = ocr_service.run_parse(image)
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=host["id"],
            draft_match_id=match_id,
        )
        assert ocr_service.user_can_view_image(image.id, opp["id"]) is True
        assert (
            ocr_service.user_can_view_image(image.id, stranger["id"])
            is False
        )


def test_user_can_view_image_allows_opponent_for_extra_screenshots(
    app: Flask, make_user
) -> None:
    """Extra images in a multi-screenshot batch live only on the
    primary attempt's parsed_json. Opponents must still be able to
    view them — the helper walks both sources."""
    host = make_user(username="host", password="x")
    opp = make_user(username="opp", password="x")
    match_id = _make_draft_match(app, host_id=host["id"], opp_id=opp["id"])
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        primary = ocr_service.save_uploaded_image(
            _file_storage(name="a.png"), uploader_user_id=host["id"]
        )
        extra = ocr_service.save_uploaded_image(
            _file_storage(name="b.png"), uploader_user_id=host["id"]
        )
        attempt = ocr_service.run_parse(primary)
        new_pj = dict(attempt.parsed_json or {})
        new_pj["screenshot_image_ids"] = [primary.id, extra.id]
        attempt.parsed_json = new_pj
        db.session.commit()
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=host["id"],
            draft_match_id=match_id,
        )
        assert ocr_service.user_can_view_image(extra.id, opp["id"]) is True
