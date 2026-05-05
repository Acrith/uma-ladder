"""Profile avatar uploads + race history rendering."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftMatch,
    DraftMatchStatus,
    DraftRaceResult,
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
    UploadedImage,
    UserProfile,
)
from uma_ladder.models.enums import PresetSource, UploadPurpose
from uma_ladder.services import profiles as profiles_service


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_avatar_upload_persists_to_profile(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Posting a file to /profiles/me sets profile.avatar_url to the
    served avatar URL and stores the image with purpose=AVATAR."""
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    resp = client.post(
        "/profiles/me",
        data={"avatar_image": (io.BytesIO(_png_bytes()), "me.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        profile = db.session.query(UserProfile).first()
        assert profile is not None
        assert profile.avatar_url is not None
        assert "/profiles/avatars/" in profile.avatar_url
        images = (
            db.session.query(UploadedImage)
            .filter_by(purpose=UploadPurpose.AVATAR)
            .all()
        )
        assert len(images) == 1


def test_avatar_upload_wins_over_url_field(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """When both URL and upload are submitted, the upload wins."""
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    client.post(
        "/profiles/me",
        data={
            "avatar_url": "https://example.com/old.png",
            "avatar_image": (io.BytesIO(_png_bytes()), "new.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    with app.app_context():
        profile = db.session.query(UserProfile).first()
        # Stored URL is the served-avatar route, not the URL field.
        assert profile.avatar_url is not None
        assert "/profiles/avatars/" in profile.avatar_url
        assert "example.com" not in profile.avatar_url


def test_serve_avatar_serves_uploaded_image(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    client.post(
        "/profiles/me",
        data={"avatar_image": (io.BytesIO(_png_bytes()), "me.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        image = (
            db.session.query(UploadedImage)
            .filter_by(purpose=UploadPurpose.AVATAR)
            .first()
        )
        image_id = image.id
    resp = client.get(f"/profiles/avatars/{image_id}")
    assert resp.status_code == 200
    assert resp.data.startswith(b"\x89PNG")


def test_serve_avatar_refuses_non_avatar_uploads(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The avatar serve endpoint must reject images uploaded for other
    purposes — otherwise an OCR screenshot would leak through here."""
    make_user(username="alice", role=Role.USER)
    with app.app_context():
        # Manually persist an OCR_RESULT image and then try to fetch via
        # the avatar route.
        img = UploadedImage(
            uploader_user_id=1,
            storage_key="dummy.png",
            original_filename="x.png",
            mime_type="image/png",
            size_bytes=10,
            purpose=UploadPurpose.OCR_RESULT,
        )
        db.session.add(img)
        db.session.commit()
        image_id = img.id
    _login(client, "alice")
    resp = client.get(f"/profiles/avatars/{image_id}")
    assert resp.status_code == 404


def test_history_combines_draft_and_official_in_recency_order(
    app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=10),
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

        # An older draft result.
        match = DraftMatch(
            season_id=s.id,
            host_user_id=user["id"],
            join_code="ABC12345",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
            selected_preset_id=p.id,
        )
        db.session.add(match)
        db.session.commit()
        draft_result = DraftRaceResult(
            draft_match_id=match.id,
            user_id=user["id"],
            placement=1,
        )
        draft_result.created_at = now - timedelta(days=2)
        db.session.add(draft_result)

        # A newer official result.
        race = OfficialRace(
            season_id=s.id,
            organizer_user_id=user["id"],
            name="Spring Cup",
            preset_id=p.id,
            status=OfficialRaceStatus.COMPLETED,
        )
        db.session.add(race)
        db.session.commit()
        off_result = OfficialRaceResult(
            official_race_id=race.id,
            user_id=user["id"],
            placement=3,
            uma_name="Special Week",
        )
        off_result.created_at = now - timedelta(hours=1)
        db.session.add(off_result)
        db.session.commit()

        history = profiles_service.list_recent_history_for_user(user["id"])
        assert len(history) == 2
        # Newer (official) first.
        assert history[0].kind == "official"
        assert history[0].title == "Spring Cup"
        assert history[0].placement == 3
        assert history[1].kind == "draft"
        assert history[1].placement == 1


def test_public_profile_renders_history_section(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
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
            organizer_user_id=user["id"],
            name="Spring Cup",
            preset_id=p.id,
            status=OfficialRaceStatus.COMPLETED,
        )
        db.session.add(race)
        db.session.commit()
        db.session.add(
            OfficialRaceResult(
                official_race_id=race.id,
                user_id=user["id"],
                placement=1,
                uma_name="Special Week",
            )
        )
        db.session.commit()

    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Recent races" in body
    assert "Spring Cup" in body
    assert "#1" in body
