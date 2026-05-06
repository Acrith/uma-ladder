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


def test_avatar_upload_resizes_large_image(
    client: FlaskClient, app: Flask, make_user, tmp_path
) -> None:
    """A 2048x2048 input should land on disk at ≤256 on the longer side."""
    from PIL import Image

    big_path = tmp_path / "big.png"
    Image.new("RGB", (2048, 2048), color=(0, 200, 200)).save(big_path)
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    with open(big_path, "rb") as fh:
        client.post(
            "/profiles/me",
            data={"avatar_image": (fh, "big.png")},
            content_type="multipart/form-data",
        )
    with app.app_context():
        image = (
            db.session.query(UploadedImage)
            .filter_by(purpose=UploadPurpose.AVATAR)
            .first()
        )
        from uma_ladder.services import ocr as ocr_service
        path = ocr_service.image_path(image)
        with Image.open(path) as resized:
            assert max(resized.size) <= 256


def test_remove_avatar_clears_url(
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
        profile = db.session.query(UserProfile).first()
        assert profile.avatar_url is not None

    resp = client.post("/profiles/me/avatar/remove", follow_redirects=False)
    assert resp.status_code == 302
    with app.app_context():
        profile = db.session.query(UserProfile).first()
        assert profile.avatar_url is None


def test_remove_avatar_requires_login(client: FlaskClient) -> None:
    resp = client.post("/profiles/me/avatar/remove", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


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


def test_elo_summary_returns_default_for_user_with_no_matches(
    app: Flask, make_user
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
        db.session.add(s)
        db.session.commit()
        from uma_ladder.services import draft as draft_service
        summary = draft_service.elo_summary_for_user(user["id"])
        assert summary is not None
        assert summary.rating == 1000  # DEFAULT_RATING
        assert summary.last_delta == 0
        assert summary.match_count == 0


def test_elo_summary_reflects_latest_change(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    opp = make_user(username="bob", role=Role.USER)
    with app.app_context():
        from uma_ladder.models import DraftEloChange
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        # Two real DraftMatch parents — FK constraints (PR-G4) require
        # the parent row to exist before child elo-change rows insert.
        from uma_ladder.services import draft as draft_service

        m1 = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=user["id"],
                umas_per_player=2, preset_pool="custom",
            )
        )
        m2 = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=user["id"],
                umas_per_player=2, preset_pool="custom",
            )
        )
        db.session.add_all([
            DraftEloChange(
                draft_match_id=m1.id,
                season_id=s.id,
                user_id=user["id"],
                opponent_user_id=opp["id"],
                rating_before=1000,
                rating_after=1016,
                delta=16,
                outcome=1.0,
            ),
            DraftEloChange(
                draft_match_id=m2.id,
                season_id=s.id,
                user_id=user["id"],
                opponent_user_id=opp["id"],
                rating_before=1016,
                rating_after=1003,
                delta=-13,
                outcome=0.0,
            ),
        ])
        db.session.commit()
        from uma_ladder.services import draft as draft_service
        summary = draft_service.elo_summary_for_user(user["id"])
        assert summary.rating == 1003
        assert summary.last_delta == -13
        assert summary.match_count == 2


def test_elo_summary_returns_none_when_no_active_season(
    app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        from uma_ladder.services import draft as draft_service
        # No season seeded — get_active_season returns None.
        assert draft_service.elo_summary_for_user(user["id"]) is None


def test_public_profile_renders_elo_card(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", role=Role.USER)
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Draft Elo now lives in the hero stat tile grid.
    assert "Draft Elo" in body
    assert "1000" in body
    # Tile copy: "Provisional · no matches yet" (rephrased from old card).
    assert "Provisional" in body
    assert "no matches yet" in body


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

        page = profiles_service.list_recent_history_for_user(user["id"])
        assert page.total == 2
        assert len(page.entries) == 2
        # Newer (official) first.
        assert page.entries[0].kind == "official"
        assert page.entries[0].title == "Spring Cup"
        assert page.entries[0].placement == 3
        assert page.entries[1].kind == "draft"
        assert page.entries[1].placement == 1
        assert page.pages == 1


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
    # Split "Recent · Official" / "Recent · Draft" cards in the new layout.
    assert "Recent · Official" in body
    assert "Spring Cup" in body
    assert "#1" in body


def _seed_many_official_results(
    app: Flask, user_id: int, n: int
) -> None:
    """Helper: create N completed OfficialRaceResult rows for a user.
    Each result lives on its own race, since (race_id, user_id) is
    uniquely constrained."""
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
        for i in range(n):
            race = OfficialRace(
                season_id=s.id,
                organizer_user_id=user_id,
                name=f"Race #{i}",
                preset_id=p.id,
                status=OfficialRaceStatus.COMPLETED,
            )
            db.session.add(race)
            db.session.flush()
            r = OfficialRaceResult(
                official_race_id=race.id,
                user_id=user_id,
                placement=1,
                uma_name=f"Uma {i}",
            )
            r.created_at = now - timedelta(minutes=i)
            db.session.add(r)
        db.session.commit()


def test_history_pagination_boundaries(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    _seed_many_official_results(app, user["id"], n=12)
    with app.app_context():
        page1 = profiles_service.list_recent_history_for_user(
            user["id"], page=1, page_size=5
        )
        assert page1.total == 12
        assert len(page1.entries) == 5
        assert page1.pages == 3
        page2 = profiles_service.list_recent_history_for_user(
            user["id"], page=2, page_size=5
        )
        assert len(page2.entries) == 5
        page3 = profiles_service.list_recent_history_for_user(
            user["id"], page=3, page_size=5
        )
        assert len(page3.entries) == 2
        # Page out of range returns empty (still a valid HistoryPage).
        page99 = profiles_service.list_recent_history_for_user(
            user["id"], page=99, page_size=5
        )
        assert page99.entries == []
        assert page99.total == 12


def test_history_kind_filter_changes_total(
    app: Flask, make_user
) -> None:
    """`total` reflects the filtered set, not the global count — that's
    what the template's "(X results)" header relies on."""
    user = make_user(username="alice", role=Role.USER)
    _seed_many_official_results(app, user["id"], n=3)

    with app.app_context():
        # Add one draft result so the unfiltered total is 4.
        from uma_ladder.models import DraftMatch, DraftMatchStatus, DraftRaceResult
        match = DraftMatch(
            season_id=1,
            host_user_id=user["id"],
            join_code="ABCDEF12",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
        )
        db.session.add(match)
        db.session.commit()
        db.session.add(
            DraftRaceResult(
                draft_match_id=match.id,
                user_id=user["id"],
                placement=1,
            )
        )
        db.session.commit()

        all_kinds = profiles_service.list_recent_history_for_user(user["id"])
        assert all_kinds.total == 4

        official_only = profiles_service.list_recent_history_for_user(
            user["id"], kind="official"
        )
        assert official_only.total == 3
        assert all(e.kind == "official" for e in official_only.entries)

        draft_only = profiles_service.list_recent_history_for_user(
            user["id"], kind="draft"
        )
        assert draft_only.total == 1
        assert draft_only.entries[0].kind == "draft"


def test_history_invalid_kind_treated_as_all(
    app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    _seed_many_official_results(app, user["id"], n=2)
    with app.app_context():
        page = profiles_service.list_recent_history_for_user(
            user["id"], kind="garbage"
        )
        assert page.total == 2  # treated as no filter


def test_public_profile_view_all_link_to_kind_filtered_view(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The Stage-1 layout dropped the inline filter-pill / pagination
    controls in favour of split top-5 cards. When there are more
    results than fit, the card surfaces a "View all →" link that
    deep-links to the same route with ?kind= set."""
    user = make_user(username="alice", role=Role.USER)
    _seed_many_official_results(app, user["id"], n=12)
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Card shows top 5 of 12 with a kind-filtered "View all" deep link.
    assert "Last 5 of 12" in body
    assert "kind=official" in body
    assert "View all" in body


def test_draft_history_groups_multi_uma_match_into_single_entry(
    app: Flask, make_user
) -> None:
    """A 2v2 draft match means each player owns 2 DraftRaceResult
    rows. Profile race history is per-MATCH, not per-uma — so the
    match should appear ONCE, with the user's best (lowest) placement
    of their two umas. Without this dedup the same match shows twice
    in the timeline."""
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

        match = DraftMatch(
            season_id=s.id,
            host_user_id=user["id"],
            join_code="ABCD1234",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
            selected_preset_id=p.id,
        )
        db.session.add(match)
        db.session.commit()
        # User owns two umas in the match — placements 1 and 4.
        for placement in (1, 4):
            r = DraftRaceResult(
                draft_match_id=match.id,
                user_id=user["id"],
                placement=placement,
            )
            r.created_at = now - timedelta(hours=2)
            db.session.add(r)
        db.session.commit()

        page = profiles_service.list_recent_history_for_user(user["id"])
        # Exactly one history entry — not two.
        assert page.total == 1
        assert len(page.entries) == 1
        # The kept placement is the BEST (lowest) of the two.
        assert page.entries[0].placement == 1
        assert page.entries[0].kind == "draft"
