"""ocr-test CLI command + step-page failure UI.

The CLI is a smoke-test tool for verifying the configured OCR
provider against local screenshots without spinning up the UI flow.
The failure UI renders inline on the step page when an OcrParseAttempt
lands in FAILED state, so the user can fall back to manual entry
without losing context.
"""

from __future__ import annotations

import io
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage

from uma_ladder.extensions import db
from uma_ladder.models import OcrParseAttempt, OcrParseStatus, Role
from uma_ladder.services import ocr as ocr_service


def _png_bytes() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d76368686800000005000170d80b240000000049"
        "454e44ae426082"
    )


def _login(
    client: FlaskClient, username: str, password: str = "password123"
) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------- CLI ----------


def test_ocr_test_cli_runs_against_mock_provider(
    app: Flask, tmp_path: Path
) -> None:
    """Click's CliRunner invokes the registered command exactly like
    the real `flask uma ocr-test` invocation."""
    from click.testing import CliRunner

    img = tmp_path / "shot.png"
    img.write_bytes(_png_bytes())

    runner = CliRunner()
    cli = app.cli
    result = runner.invoke(
        cli,
        ["uma", "ocr-test", str(img), "--provider", "mock"],
    )
    assert result.exit_code == 0, result.output
    # Mock provider's known output appears.
    assert "MockUma A" in result.output
    assert "rows:       3" in result.output
    assert "skills:     3" in result.output
    assert "stats:" in result.output


def test_ocr_test_cli_does_not_write_to_db(
    app: Flask, tmp_path: Path
) -> None:
    """Smoke tool must not pollute the DB — running it shouldn't create
    UploadedImage / OcrParseAttempt rows."""
    from click.testing import CliRunner

    img = tmp_path / "shot.png"
    img.write_bytes(_png_bytes())

    runner = CliRunner()
    runner.invoke(
        app.cli,
        ["uma", "ocr-test", str(img), "--provider", "mock"],
    )
    with app.app_context():
        from uma_ladder.models import UploadedImage
        assert db.session.query(UploadedImage).count() == 0
        assert db.session.query(OcrParseAttempt).count() == 0


def test_ocr_test_cli_handles_provider_exception(
    app: Flask, tmp_path: Path, monkeypatch
) -> None:
    """If the provider raises, the command exits 1 and prints the
    error to stderr — not a stack-trace crash."""
    from click.testing import CliRunner

    img = tmp_path / "shot.png"
    img.write_bytes(_png_bytes())

    class BoomProvider:
        name = "boom"

        def parse(self, *_args, **_kwargs):
            raise RuntimeError("simulated provider failure")

    # Register + use via --provider override.
    ocr_service.register_provider("boom", BoomProvider)

    runner = CliRunner()
    result = runner.invoke(
        app.cli,
        ["uma", "ocr-test", str(img), "--provider", "boom"],
    )
    assert result.exit_code == 1
    assert "simulated provider failure" in (result.output + (result.stderr_bytes or b"").decode())


# ---------- Step-page failure UI ----------


def _seed_failed_attempt_for_official_results(
    app: Flask, race_id: int, error: str = "vision rejected: image too large"
) -> int:
    """Insert an UploadedImage + a FAILED OcrParseAttempt as if a real
    upload had hit the route and the provider raised."""
    with app.app_context():
        from uma_ladder.models import UploadedImage
        img = UploadedImage(
            uploader_user_id=None,
            storage_key="failed.png",
            original_filename="failed.png",
            mime_type="image/png",
            size_bytes=10,
        )
        db.session.add(img)
        db.session.flush()
        attempt = OcrParseAttempt(
            uploaded_image_id=img.id,
            provider="google_vision",
            status=OcrParseStatus.FAILED,
            error_message=error,
            parsed_json=None,
            confidence_json=None,
            raw_text=None,
        )
        db.session.add(attempt)
        db.session.commit()
        return attempt.id


def _build_completed_race(app: Flask, organizer_id: int) -> int:
    """Get to a state where /results-from-ocr is reachable."""
    from datetime import UTC, datetime, timedelta

    from uma_ladder.models import (
        OfficialRace,
        OfficialRaceStatus,
        RacePreset,
        Season,
        SeasonStatus,
    )
    from uma_ladder.models.enums import PresetSource

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
            organizer_user_id=organizer_id,
            name="R",
            preset_id=p.id,
            status=OfficialRaceStatus.ROOM_CODE_AVAILABLE,
        )
        db.session.add(race)
        db.session.commit()
        return race.id


def test_step_page_renders_inline_failure_banner(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """A FAILED attempt produces an inline rose-bordered banner with
    the upstream error_message, not a redirect to detail."""
    org = make_user(username="org", role=Role.ORGANIZER)
    race_id = _build_completed_race(app, org["id"])
    attempt_id = _seed_failed_attempt_for_official_results(
        app, race_id, error="Image too large for Vision API"
    )

    _login(client, "org")
    resp = client.get(
        f"/official/{race_id}/results-from-ocr/{attempt_id}"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "OCR failed" in body
    assert "Image too large for Vision API" in body
    assert "Fall back to manual entry" in body


def test_step_page_silent_when_attempt_succeeded(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Successful PARSED attempts don't show the failure banner."""
    app.config["OCR_PROVIDER"] = "mock"
    org = make_user(username="org", role=Role.ORGANIZER)
    race_id = _build_completed_race(app, org["id"])

    _login(client, "org")
    resp = client.post(
        f"/official/{race_id}/results-screenshot",
        data={"image": (io.BytesIO(_png_bytes()), "ok.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "OCR failed" not in body


def test_run_parse_writes_failed_attempt_when_provider_raises(
    app: Flask, make_user
) -> None:
    """services.ocr.run_parse should land FAILED with a captured
    error_message rather than propagating, so the step page has
    something to render."""
    user = make_user(username="alice", role=Role.USER)

    class BoomProvider:
        name = "boom"

        def parse(self, *_args, **_kwargs):
            raise RuntimeError("provider exploded")

    with app.app_context():
        from uma_ladder.models import UploadedImage
        img = UploadedImage(
            uploader_user_id=user["id"],
            storage_key="real-upload.png",
            mime_type="image/png",
            size_bytes=10,
        )
        db.session.add(img)
        db.session.commit()
        attempt = ocr_service.run_parse(img, provider=BoomProvider())
        assert attempt.status == OcrParseStatus.FAILED
        assert "provider exploded" in (attempt.error_message or "")


# silence linter: FileStorage is imported in case we extend tests later
_ = FileStorage
