from __future__ import annotations

import io

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    OcrParseAttempt,
    OcrParseStatus,
    Role,
    UploadedImage,
)


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


def test_index_anonymous(client: FlaskClient) -> None:
    resp = client.get("/ocr/")
    assert resp.status_code == 200


def test_upload_requires_login(client: FlaskClient) -> None:
    resp = client.get("/ocr/upload", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_upload_creates_image_and_attempt(
    client: FlaskClient, app: Flask, make_user
) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")

    resp = client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.query(UploadedImage).count() == 1
        attempt = db.session.query(OcrParseAttempt).first()
        assert attempt is not None
        assert attempt.status == OcrParseStatus.PARSED


def test_attempt_view_requires_owner_or_admin(
    client: FlaskClient, app: Flask, make_user
) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")
    make_user(username="root", password="password123", role=Role.ADMIN)

    _login(client, "alice", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        attempt_id = db.session.query(OcrParseAttempt).first().id

    # bob cannot view alice's attempt
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.get(f"/ocr/attempts/{attempt_id}")
    assert resp.status_code == 403

    # admin can
    client.post("/auth/logout")
    _login(client, "root", "password123")
    resp = client.get(f"/ocr/attempts/{attempt_id}")
    assert resp.status_code == 200


def test_confirm_marks_status(client: FlaskClient, app: Flask, make_user) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        attempt_id = db.session.query(OcrParseAttempt).first().id

    resp = client.post(
        f"/ocr/attempts/{attempt_id}",
        data={
            "row_0_placement": "1",
            "row_0_uma_name": "Edited",
            "row_0_strategy": "Front",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        a = db.session.get(OcrParseAttempt, attempt_id)
        assert a.status == OcrParseStatus.CONFIRMED
        assert a.parsed_json == {
            "rows": [{"placement": 1, "uma_name": "Edited", "strategy": "Front"}]
        }


def test_serve_image_requires_owner(
    client: FlaskClient, app: Flask, make_user
) -> None:
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    _login(client, "alice", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        image_id = db.session.query(UploadedImage).first().id

    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.get(f"/ocr/uploads/{image_id}")
    assert resp.status_code == 403
