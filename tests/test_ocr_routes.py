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


# ---------- PR-J4 — opponent of a linked draft match can view ----------


def test_serve_image_allows_opponent_of_linked_match(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Regression target for the user-reported gap: opponents could
    not see screenshots their match-mate uploaded. After PR-J4, the
    confirmed-attempt link grants both participants view access; an
    unrelated user is still 403."""
    from datetime import UTC, datetime, timedelta

    from uma_ladder.models import (
        DraftMatch,
        DraftMatchStatus,
        Season,
        SeasonStatus,
    )
    from uma_ladder.services import ocr as ocr_service

    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    host = make_user(username="host", password="password123")
    opp = make_user(username="opp", password="password123")
    make_user(username="snoop", password="password123")

    # Host uploads + parses a screenshot, then we manually link a
    # confirmed attempt to a draft match where opp is a participant.
    _login(client, "host", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        image = db.session.query(UploadedImage).first()
        attempt = (
            db.session.query(OcrParseAttempt)
            .filter_by(uploaded_image_id=image.id)
            .first()
        )
        attempt.status = OcrParseStatus.PARSED
        db.session.commit()
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
        match = DraftMatch(
            season_id=s.id,
            host_user_id=host["id"],
            opponent_user_id=opp["id"],
            join_code="J4VIEW",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
        )
        db.session.add(match)
        db.session.commit()
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=host["id"],
            draft_match_id=match.id,
        )
        image_id = image.id

    # Opponent (didn't upload, isn't admin) — allowed by virtue of
    # being a match participant.
    client.post("/auth/logout")
    _login(client, "opp", "password123")
    resp = client.get(f"/ocr/uploads/{image_id}")
    assert resp.status_code == 200

    # Stranger — still 403.
    client.post("/auth/logout")
    _login(client, "snoop", "password123")
    resp = client.get(f"/ocr/uploads/{image_id}")
    assert resp.status_code == 403


def test_serve_image_admin_keeps_access(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Admin override on serve_image is independent of the J4 link."""
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    make_user(username="root", password="password123", role=Role.ADMIN)

    _login(client, "alice", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        image_id = db.session.query(UploadedImage).first().id

    client.post("/auth/logout")
    _login(client, "root", "password123")
    resp = client.get(f"/ocr/uploads/{image_id}")
    assert resp.status_code == 200


def test_serve_image_senior_organizer_can_view(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Senior organizers moderate matches per docs/permissions.md
    and need OCR access to adjudicate disputes — they shouldn't
    have to chain through admin to see screenshots."""
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    make_user(username="senior", password="password123", role=Role.SENIOR_ORGANIZER)

    _login(client, "alice", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        image_id = db.session.query(UploadedImage).first().id

    client.post("/auth/logout")
    _login(client, "senior", "password123")
    resp = client.get(f"/ocr/uploads/{image_id}")
    assert resp.status_code == 200


def test_serve_image_organizer_still_blocked(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Plain organizers (rank 1) are not match moderators — they
    should NOT see other people's screenshots without a participant
    link."""
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    make_user(username="org", password="password123", role=Role.ORGANIZER)

    _login(client, "alice", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        image_id = db.session.query(UploadedImage).first().id

    client.post("/auth/logout")
    _login(client, "org", "password123")
    resp = client.get(f"/ocr/uploads/{image_id}")
    assert resp.status_code == 403


def test_serve_image_superadmin_can_view(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Superadmins inherit `has_at_least('senior_organizer')` via
    the rank ladder, so this is covered by the same check — make it
    explicit so a future role-rank refactor can't quietly regress."""
    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
    make_user(username="alice", password="password123")
    make_user(username="root", password="password123", role=Role.SUPERADMIN)

    _login(client, "alice", "password123")
    client.post(
        "/ocr/upload",
        data={"image": (io.BytesIO(_png()), "race.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        image_id = db.session.query(UploadedImage).first().id

    client.post("/auth/logout")
    _login(client, "root", "password123")
    resp = client.get(f"/ocr/uploads/{image_id}")
    assert resp.status_code == 200
