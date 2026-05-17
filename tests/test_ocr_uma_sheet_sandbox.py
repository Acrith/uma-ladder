"""PR-OCR1 — Uma-sheet sandbox tests.

Smokes the new /ocr/uma-sheet/upload + /ocr/uma-sheet/<id> debug
surface. Real extraction logic lives in services.ocr_google_vision
and has its own tests; this file only covers the sandbox wiring:
permission gating, attempt creation, view rendering, purpose
tagging.
"""

from __future__ import annotations

import io

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import OcrParseAttempt, Role, UploadedImage
from uma_ladder.models.enums import UploadPurpose


def _png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d76368686800000005000170d80b240000000049"
        "454e44ae426082"
    )


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ─── Permission gating ───────────────────────────────────────────


def test_sandbox_upload_requires_admin_anonymous(
    client: FlaskClient,
) -> None:
    """Anonymous visitor gets the 401 the decorator emits."""
    resp = client.get("/ocr/uma-sheet/upload", follow_redirects=False)
    assert resp.status_code == 401


def test_sandbox_upload_rejects_regular_user(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Plain users see 403 — sandbox is admin-only."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/ocr/uma-sheet/upload", follow_redirects=False)
    assert resp.status_code == 403


def test_sandbox_view_requires_admin(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    # 404 vs 403: route enforces admin before looking the row up, so
    # even a nonexistent id rejects on permission grounds.
    resp = client.get("/ocr/uma-sheet/9999", follow_redirects=False)
    assert resp.status_code == 403


# ─── Upload + parse flow ─────────────────────────────────────────


def test_sandbox_upload_renders_form_for_admin(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")
    resp = client.get("/ocr/uma-sheet/upload")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Uma sheet OCR" in body
    assert "Upload" in body


def test_sandbox_upload_creates_attempt_tagged_uma_sheet(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """POST runs through the same save_uploaded_image + run_parse
    pipeline as the race-result flow, but tags the upload as
    UMA_SHEET so the sandbox can be filtered later."""
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")

    resp = client.post(
        "/ocr/uma-sheet/upload",
        data={"image": (io.BytesIO(_png()), "uma.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/ocr/uma-sheet/" in resp.headers["Location"]
    with app.app_context():
        image = db.session.query(UploadedImage).one()
        assert image.purpose == UploadPurpose.UMA_SHEET
        attempt = db.session.query(OcrParseAttempt).one()
        assert attempt.uploaded_image_id == image.id


def test_sandbox_view_renders_mock_parse(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Sandbox view shows extracted stats + skills from the mock
    provider so we can verify the rendering path end-to-end."""
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")

    client.post(
        "/ocr/uma-sheet/upload",
        data={"image": (io.BytesIO(_png()), "uma.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        attempt_id = db.session.query(OcrParseAttempt).one().id

    resp = client.get(f"/ocr/uma-sheet/{attempt_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Stats panel shows each of the 5 labels.
    for label in ("speed", "stamina", "power", "guts", "wisdom"):
        assert label in body.lower()
    # MockOcrProvider's sample stats — at least one of them should
    # appear as text on the page (1100 = speed).
    assert "1100" in body
    # Sandbox-specific UI strings.
    assert "Uma sheet" in body
    assert "Raw OCR text" in body


def test_sandbox_view_404_for_missing_attempt(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")
    resp = client.get("/ocr/uma-sheet/9999")
    assert resp.status_code == 404


# ─── Index tile ──────────────────────────────────────────────────


def test_ocr_index_shows_sandbox_link_for_admin(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="root", password="password123", role=Role.ADMIN)
    _login(client, "root", "password123")
    resp = client.get("/ocr/")
    assert resp.status_code == 200
    assert "Uma sheet sandbox" in resp.data.decode()


def test_ocr_index_hides_sandbox_link_from_regular_user(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/ocr/")
    assert resp.status_code == 200
    assert "Uma sheet sandbox" not in resp.data.decode()
