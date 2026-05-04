from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    NotificationStatus,
    OfficialRaceStatus,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.notifications.discord import FakeTransport, set_transport
from uma_ladder.services import official as official_service
from uma_ladder.services.auth import RegistrationRequest, register_user


def _season() -> Season:
    now = datetime.now(UTC)
    s = Season(
        name="S1",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=89),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    return s


def _user(name: str, role: str = Role.USER) -> int:
    u = register_user(RegistrationRequest(username=name, password="password123", role=role))
    return u.id


def test_official_set_room_code_fires_notification(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)

        s = _season()
        org = _user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="Test", organizer_user_id=org
            )
        )
        official_service.open_registration(race.id)
        official_service.set_room_code(race.id, "ROOM-1")

        assert len(transport.calls) == 1
        url, payload = transport.calls[0]
        assert "ROOM-1" in str(payload)


def test_official_results_fires_notification(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)

        s = _season()
        org = _user("org", role=Role.ORGANIZER)
        a = _user("alice")
        b = _user("bob")
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="Test", organizer_user_id=org
            )
        )
        official_service.open_registration(race.id)
        official_service.register(race.id, a)
        official_service.register(race.id, b)
        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=a, placement=1),
                official_service.ResultLine(user_id=b, placement=2),
            ],
            confirmed_by_user_id=org,
        )

        assert len(transport.calls) == 1
        # Top 5 contains alice
        assert b"alice" in str(transport.calls[0][1]).encode() or "alice" in str(
            transport.calls[0][1]
        )


def test_notify_off_via_kwarg(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)

        s = _season()
        org = _user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="Test", organizer_user_id=org
            )
        )
        official_service.open_registration(race.id)
        official_service.set_room_code(race.id, "ROOM-1", notify=False)

        assert transport.calls == []


def test_failure_does_not_break_caller(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        set_transport(FakeTransport(fail_with="boom"))

        s = _season()
        org = _user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="Test", organizer_user_id=org
            )
        )
        official_service.open_registration(race.id)
        # Must not raise even though transport fails.
        official_service.set_room_code(race.id, "ROOM-1")
        assert race.status == OfficialRaceStatus.ROOM_CODE_AVAILABLE

        from uma_ladder.models import DiscordNotificationAttempt

        rows = db.session.query(DiscordNotificationAttempt).all()
        assert len(rows) == 1
        assert rows[0].status == NotificationStatus.FAILED
