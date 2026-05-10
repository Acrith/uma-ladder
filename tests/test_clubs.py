"""Tests for the first-class Club entity (PR-M1).

Covers:
- ``services.clubs.upsert_club_from_trainer`` — happy path,
  idempotence, name refresh on rename, no-op when trainer has no
  circle_id.
- ``services.profiles.sync_club_id_from_trainer`` integration —
  setting club_id also writes the Club row.
- ``/clubs/<int:circle_id>`` page — happy path with members,
  no-roster ingested-only club, 404 unknown id, falls back to
  generic header when only the roster exists with no Club row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import Club, UserProfile
from uma_ladder.services import clubs as clubs_service
from uma_ladder.services import profiles as profiles_service
from uma_ladder.services.auth import RegistrationRequest, register_user

# ─── Fake TrainerSummary ─────────────────────────────────────────


@dataclass(frozen=True)
class FakeTrainer:
    """Duck-typed stand-in for ``services.uma_moe.TrainerSummary``.
    The Club upsert only reads ``circle_id`` and ``circle_name`` — no
    need to construct a full TrainerSummary in tests."""

    circle_id: int | None
    circle_name: str | None = None


# ─── upsert_club_from_trainer ────────────────────────────────────


def test_upsert_creates_club_from_trainer(app: Flask) -> None:
    with app.app_context():
        club = clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=12345, circle_name="Cyber Cygnets")
        )
        assert club is not None
        assert club.circle_id == 12345
        assert club.name == "Cyber Cygnets"
        assert club.cached_at is not None

        # And it lands in the DB at the canonical primary key.
        row = db.session.get(Club, 12345)
        assert row is not None
        assert row.name == "Cyber Cygnets"


def test_upsert_returns_none_when_trainer_has_no_circle(app: Flask) -> None:
    """User without a club → upstream returns circle_id=None.
    No Club row should be created (would be a row keyed on None or 0)."""
    with app.app_context():
        result = clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=None, circle_name=None)
        )
        assert result is None
        assert db.session.query(Club).count() == 0


def test_upsert_returns_none_when_trainer_is_none(app: Flask) -> None:
    """Caller passed None — e.g. uma.moe fetch failed. No-op."""
    with app.app_context():
        assert clubs_service.upsert_club_from_trainer(None) is None
        assert db.session.query(Club).count() == 0


def test_upsert_is_idempotent_on_unchanged_name(app: Flask) -> None:
    """Same name + fresh row → no commit-noise. ``cached_at`` should
    NOT advance on every call; we only refresh it when the row is
    older than a day or the name changed."""
    with app.app_context():
        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=42, circle_name="Same Club")
        )
        row = db.session.get(Club, 42)
        assert row is not None
        original_cached_at = row.cached_at

        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=42, circle_name="Same Club")
        )
        db.session.refresh(row)
        assert row.cached_at == original_cached_at  # untouched


def test_upsert_refreshes_when_name_changes(app: Flask) -> None:
    with app.app_context():
        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=42, circle_name="Old Name")
        )
        row_before = db.session.get(Club, 42)
        assert row_before is not None
        before_cached_at = row_before.cached_at

        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=42, circle_name="New Name")
        )
        row_after = db.session.get(Club, 42)
        assert row_after is not None
        assert row_after.name == "New Name"
        # cached_at moved forward when the name changed.
        assert row_after.cached_at >= before_cached_at


def test_upsert_refreshes_when_row_is_stale(app: Flask) -> None:
    """Even if the name hasn't changed, an old cached_at gets bumped
    so the user-visible 'cached' timestamp isn't a year out of date."""
    with app.app_context():
        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=42, circle_name="Stable")
        )
        row = db.session.get(Club, 42)
        assert row is not None
        # Simulate a row stamped 2 days ago.
        row.cached_at = datetime.now(UTC) - timedelta(days=2)
        db.session.commit()

        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=42, circle_name="Stable")
        )
        db.session.refresh(row)
        # SQLite drops tzinfo on read — normalize before subtracting.
        cached_at = row.cached_at
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=UTC)
        assert (datetime.now(UTC) - cached_at).total_seconds() < 60


# ─── sync_club_id_from_trainer integration ───────────────────────


def test_sync_club_id_also_writes_club_row(app: Flask) -> None:
    """The user-side mirror update + the metadata cache upsert
    happen together, so a single profile-view fetch populates both."""
    with app.app_context():
        user = register_user(
            RegistrationRequest(username="ada", password="password123")
        )
        profile = profiles_service.get_or_create_profile(user)
        profiles_service.sync_club_id_from_trainer(
            profile, FakeTrainer(circle_id=999, circle_name="Star Club")
        )

        # User mirror
        db.session.refresh(profile)
        assert profile.club_id == 999
        # Metadata cache
        club = db.session.get(Club, 999)
        assert club is not None
        assert club.name == "Star Club"


def test_sync_club_id_clears_mirror_but_still_upserts_for_known_circle(
    app: Flask,
) -> None:
    """Edge case: user leaves their club. Mirror clears to None;
    the trainer summary now has circle_id=None so no Club upsert
    fires. Existing Club rows stay (no cascade), since other users
    in the same club still depend on it."""
    with app.app_context():
        user = register_user(
            RegistrationRequest(username="bob", password="password123")
        )
        profile = profiles_service.get_or_create_profile(user)
        # Initial state: in club 100
        profiles_service.sync_club_id_from_trainer(
            profile, FakeTrainer(circle_id=100, circle_name="Original")
        )
        assert db.session.get(Club, 100) is not None

        # Now they leave (upstream circle_id is None)
        profiles_service.sync_club_id_from_trainer(
            profile, FakeTrainer(circle_id=None, circle_name=None)
        )
        db.session.refresh(profile)
        assert profile.club_id is None
        # Club row persists — other members may still reference it.
        assert db.session.get(Club, 100) is not None


# ─── /clubs/<int:circle_id> page ─────────────────────────────────


def test_club_page_renders_club_with_roster(
    app: Flask, client: FlaskClient
) -> None:
    """Happy path: Club row + at least one member with the matching
    UserProfile.club_id renders the roster card."""
    with app.app_context():
        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=777, circle_name="Test Squad")
        )
        user = register_user(
            RegistrationRequest(username="cleo", password="password123")
        )
        profile = profiles_service.get_or_create_profile(user)
        profile.club_id = 777
        db.session.commit()

    body = client.get("/clubs/777").data.decode()
    assert "Test Squad" in body
    assert "cleo" in body
    assert "/profiles/cleo" in body
    # uma.moe link survives as the secondary "View on" affordance
    assert "uma.moe/circles/777" in body


def test_club_page_404_when_unknown_and_no_members(
    app: Flask, client: FlaskClient
) -> None:
    """A circle_id we've never ingested AND with no members in our
    DB is a 404 — we have no signal it exists."""
    resp = client.get("/clubs/123456789")
    assert resp.status_code == 404


def test_club_page_renders_when_only_roster_exists(
    app: Flask, client: FlaskClient
) -> None:
    """Edge case: a UserProfile.club_id mirror exists for a circle
    we haven't yet upserted a Club row for. Page should render with
    the generic 'Club #<id>' header rather than 404."""
    with app.app_context():
        user = register_user(
            RegistrationRequest(username="dora", password="password123")
        )
        profile = profiles_service.get_or_create_profile(user)
        profile.club_id = 555
        db.session.commit()
        # Note: NO Club row inserted.
        assert db.session.get(Club, 555) is None

    body = client.get("/clubs/555").data.decode()
    # Falls back to "Club #555" when no name cached.
    assert "Club #555" in body
    # Roster still renders.
    assert "dora" in body


def test_club_page_renders_empty_roster_when_no_members(
    app: Flask, client: FlaskClient
) -> None:
    """Club row exists but no member has it as their club_id —
    roster card switches to the empty state, page itself is 200."""
    with app.app_context():
        clubs_service.upsert_club_from_trainer(
            FakeTrainer(circle_id=888, circle_name="Empty")
        )

    resp = client.get("/clubs/888")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Empty" in body
    assert "No registered members yet" in body


# ─── Profile + race chips repointed at /clubs/<id> ───────────────


def test_public_profile_club_chip_links_to_internal_clubs_page(
    app: Flask, client: FlaskClient
) -> None:
    """PR-M1 — the Club tile on /profiles/<u> now points at the
    internal /clubs/<id> page, not directly at uma.moe (which the
    Club page itself surfaces as a secondary 'View on uma.moe').
    Regression guard so we don't accidentally revert during cleanup."""
    with app.app_context():
        # Seed uma_moe_cache with a payload that gives the trainer a
        # circle_name + circle_id, which the public profile reads.
        import json as _json

        from uma_ladder.models import UmaMoeCache

        user = register_user(
            RegistrationRequest(username="elsa", password="password123")
        )
        profile = profiles_service.get_or_create_profile(user)
        profile.friend_code = "111111111111"
        db.session.commit()

        cache = UmaMoeCache(
            friend_code="111111111111",
            fetched_at=datetime.now(UTC),
            status=200,
            payload_json=_json.dumps(
                {
                    "trainer": {"name": "Elsa"},
                    "circle": {"name": "Linked Club", "circle_id": 4242},
                }
            ),
        )
        db.session.add(cache)
        db.session.commit()

    body = client.get("/profiles/elsa").data.decode()
    assert "Linked Club" in body
    # Internal link present
    assert "/clubs/4242" in body
    # The bare external uma.moe link is NOT how we link from the
    # profile page anymore (the Club page itself carries it).
    assert "https://uma.moe/circles/4242" not in body


def test_sync_club_id_idempotent_does_not_recommit_when_unchanged(
    app: Flask,
) -> None:
    """Cheap regression: calling the sync twice with the same trainer
    payload mustn't crash or duplicate Club rows."""
    with app.app_context():
        user = register_user(
            RegistrationRequest(username="finn", password="password123")
        )
        profile = profiles_service.get_or_create_profile(user)
        trainer = FakeTrainer(circle_id=314, circle_name="Pi")
        profiles_service.sync_club_id_from_trainer(profile, trainer)
        profiles_service.sync_club_id_from_trainer(profile, trainer)

        assert (
            db.session.query(Club).filter_by(circle_id=314).count() == 1
        )
        assert (
            db.session.query(UserProfile)
            .filter_by(user_id=user.id, club_id=314)
            .count()
            == 1
        )
