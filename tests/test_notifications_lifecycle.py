"""Notifications wired into official-race + draft-match lifecycle.

The dev/test environment has no webhook URLs configured, so each
notification call writes a DiscordNotificationAttempt row with
status=SKIPPED. That's enough to assert the right event_type fired —
we're testing wiring, not transport.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from uma_ladder.extensions import db
from uma_ladder.models import (
    DiscordNotificationAttempt,
    NotificationEvent,
    NotificationStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import draft as draft_service
from uma_ladder.services import official as official_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _attempts_of(event_type: str) -> list[DiscordNotificationAttempt]:
    return list(
        db.session.scalars(
            select(DiscordNotificationAttempt).where(
                DiscordNotificationAttempt.event_type == event_type
            )
        )
    )


def _setup_official(app: Flask, organizer_id: int) -> int:
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
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id,
                organizer_user_id=organizer_id,
                name="R",
                preset_id=p.id,
                max_players=12,
                notes=None,
            )
        )
        return race.id


def test_open_registration_fires_published_notification(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    race_id = _setup_official(app, org["id"])
    with app.app_context():
        official_service.open_registration(race_id)
        attempts = _attempts_of(NotificationEvent.OFFICIAL_RACE_PUBLISHED)
        assert len(attempts) == 1
        # Re-opening (close → open again) is NOT a publish.
        official_service.close_registration(race_id)
        official_service.open_registration(race_id)
        assert len(_attempts_of(NotificationEvent.OFFICIAL_RACE_PUBLISHED)) == 1


def test_cancel_race_fires_cancelled_notification_with_affected_users(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    alice = make_user(username="alice", role=Role.USER)
    race_id = _setup_official(app, org["id"])
    with app.app_context():
        official_service.open_registration(race_id)
        official_service.register(race_id, alice["id"])
        official_service.cancel_race(race_id, by_user_id=org["id"])
        attempts = _attempts_of(NotificationEvent.OFFICIAL_RACE_CANCELLED)
        assert len(attempts) == 1
        # The embed payload should mention the actor and the affected
        # registered user.
        payload = attempts[0].payload_json or {}
        embed_text = str(payload)
        assert "org" in embed_text  # cancelled_by_username
        assert "alice" in embed_text  # affected registration


def test_remove_registration_fires_notification(app: Flask, make_user) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    alice = make_user(username="alice", role=Role.USER)
    race_id = _setup_official(app, org["id"])
    with app.app_context():
        official_service.open_registration(race_id)
        reg = official_service.register(race_id, alice["id"])
        official_service.remove_registration(
            race_id, reg.id, by_user_id=org["id"]
        )
        attempts = _attempts_of(
            NotificationEvent.OFFICIAL_REGISTRATION_REMOVED
        )
        assert len(attempts) == 1
        payload_text = str(attempts[0].payload_json or {})
        assert "alice" in payload_text


def test_idempotent_remove_registration_does_not_double_fire(
    app: Flask, make_user
) -> None:
    """Re-calling remove_registration on an already-cancelled row is a
    no-op — must not produce a second notification."""
    org = make_user(username="org", role=Role.ORGANIZER)
    alice = make_user(username="alice", role=Role.USER)
    race_id = _setup_official(app, org["id"])
    with app.app_context():
        official_service.open_registration(race_id)
        reg = official_service.register(race_id, alice["id"])
        official_service.remove_registration(
            race_id, reg.id, by_user_id=org["id"]
        )
        official_service.remove_registration(
            race_id, reg.id, by_user_id=org["id"]
        )
        assert (
            len(_attempts_of(NotificationEvent.OFFICIAL_REGISTRATION_REMOVED))
            == 1
        )


def test_cancel_draft_match_fires_notification(app: Flask, make_user) -> None:
    host = make_user(username="hostie", role=Role.USER)
    org = make_user(username="org", role=Role.ORGANIZER)
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
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        draft_service.cancel_match(match.id, by_user_id=org["id"])
        attempts = _attempts_of(NotificationEvent.DRAFT_MATCH_CANCELLED)
        assert len(attempts) == 1
        assert "org" in str(attempts[0].payload_json or {})


def test_notification_attempts_marked_skipped_without_webhook(
    app: Flask, make_user
) -> None:
    """Sanity-check: with no webhook URL configured, attempts land as
    SKIPPED rather than failing the calling service."""
    org = make_user(username="org", role=Role.ORGANIZER)
    race_id = _setup_official(app, org["id"])
    with app.app_context():
        official_service.open_registration(race_id)
        attempt = _attempts_of(NotificationEvent.OFFICIAL_RACE_PUBLISHED)[0]
        assert attempt.status == NotificationStatus.SKIPPED
