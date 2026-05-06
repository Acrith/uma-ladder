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
        official_service.open_registration(race.id)  # also fires "published"
        official_service.set_room_code(race.id, "ROOM-1")

        # Find the room-code call specifically (publish event also fires).
        room_code_calls = [
            (url, payload) for url, payload in transport.calls
            if "Room code" in str(payload)
        ]
        assert len(room_code_calls) == 1
        assert "ROOM-1" in str(room_code_calls[0][1])


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
    """notify=False on set_room_code suppresses *only* the room-code
    notification — the race-published event from open_registration is
    a separate broadcast and still fires."""
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

        # No "Room code" payload should have been sent.
        assert not any(
            "Room code" in str(payload) for _, payload in transport.calls
        )


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
        # Two attempts now: open_registration's publish + set_room_code's
        # room-code event. Both should be marked FAILED by the broken
        # transport — and crucially, the race state still updated.
        assert len(rows) == 2
        assert all(r.status == NotificationStatus.FAILED for r in rows)


# ---------- dynamic timestamp + track-info helpers ----------


def test_ts_emits_discord_timestamp_tag() -> None:
    from datetime import UTC, datetime

    from uma_ladder.notifications.services import _ts

    dt = datetime(2026, 5, 10, 15, 44, 0, tzinfo=UTC)
    assert _ts(dt, style="F") == f"<t:{int(dt.timestamp())}:F>"
    # Relative variant appends the :R tag.
    out = _ts(dt, style="F", relative=True)
    assert ":F>" in out and ":R>" in out


def test_ts_handles_none() -> None:
    from uma_ladder.notifications.services import _ts

    assert _ts(None) == "—"


def test_format_track_returns_none_for_missing_preset() -> None:
    from uma_ladder.notifications.services import _format_track

    assert _format_track(None) is None


def test_publish_embed_includes_track_and_dynamic_timestamp(
    app: Flask,
) -> None:
    """The Race Published embed must:
       1. carry a Track field built from the preset
       2. render scheduled_at as a Discord <t:unix:F> tag (not ISO)
    """
    from datetime import timedelta

    from uma_ladder.models import PresetSource, RacePreset
    from uma_ladder.notifications.discord import FakeTransport, set_transport

    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)

        preset = RacePreset(
            source=PresetSource.G1_IMPORT,
            name="Tokyo Yushun",
            grade="G1",
            venue="Tokyo",
            surface="Turf",
            distance_meters=2400,
            distance_category="Medium",
            direction="Left",
            course_variant=None,
            max_runners=18,
            enabled=True,
        )
        db.session.add(preset)
        db.session.commit()

        s = _season()
        org = _user("org", role=Role.ORGANIZER)
        scheduled_at = datetime.now(UTC) + timedelta(days=4)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id,
                name="Spring G1",
                organizer_user_id=org,
                preset_id=preset.id,
                scheduled_at=scheduled_at,
                race_season="Spring",
                weather="Cloudy",
                ground_condition="Good",
            )
        )
        official_service.open_registration(race.id)

        publish_payload = next(
            p for _, p in transport.calls if "Race published" in str(p)
        )
        body = str(publish_payload)
        # Track field carries preset name + venue/distance/surface.
        assert "Tokyo Yushun" in body
        assert "2400m" in body
        assert "Turf" in body
        # Scheduled rendered as <t:unix:F> tag, not ISO.
        epoch = int(scheduled_at.timestamp())
        assert f"<t:{epoch}:F>" in body
        # Conditions field present.
        assert "Spring" in body and "Cloudy" in body and "Good" in body
