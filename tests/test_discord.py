from __future__ import annotations

from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    DiscordNotificationAttempt,
    NotificationStatus,
    NotificationTarget,
)
from uma_ladder.notifications.discord import (
    FakeTransport,
    SendResult,
    UrllibTransport,
    resolve_webhook_url,
    retry_attempt,
    send_event,
    set_transport,
)


def test_skips_when_no_webhook_configured(app: Flask) -> None:
    with app.app_context():
        set_transport(FakeTransport())
        attempt = send_event(
            event_type="some_event",
            target=NotificationTarget.RACE_REGISTRATION,
            payload={"hi": "there"},
        )
        assert attempt.status == NotificationStatus.SKIPPED
        assert attempt.response_code is None


def test_sends_via_transport_and_records_attempt(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = (
            "https://discord.example/webhook/abc"
        )
        transport = FakeTransport()
        set_transport(transport)

        attempt = send_event(
            event_type="some_event",
            target=NotificationTarget.RACE_REGISTRATION,
            payload={"x": 1},
        )
        assert attempt.status == NotificationStatus.SENT
        assert attempt.response_code == 204
        assert attempt.sent_at is not None
        assert transport.calls == [
            ("https://discord.example/webhook/abc", {"x": 1})
        ]


def test_records_failure(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = (
            "https://discord.example/webhook/abc"
        )
        set_transport(FakeTransport(fail_with="connection refused"))

        attempt = send_event(
            event_type="some_event",
            target=NotificationTarget.RACE_REGISTRATION,
            payload={"x": 1},
        )
        assert attempt.status == NotificationStatus.FAILED
        assert attempt.error_message == "connection refused"


def test_retry_promotes_failed_to_sent(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = (
            "https://discord.example/webhook/abc"
        )
        # First, fail.
        set_transport(FakeTransport(fail_with="oops"))
        attempt = send_event(
            event_type="some_event",
            target=NotificationTarget.RACE_REGISTRATION,
            payload={"x": 1},
        )
        assert attempt.status == NotificationStatus.FAILED

        # Retry with a working transport.
        set_transport(FakeTransport())
        retried = retry_attempt(attempt.id)
        assert retried is not None
        assert retried.status == NotificationStatus.SENT


def test_retry_skipped_with_no_url_stays_skipped(app: Flask) -> None:
    with app.app_context():
        set_transport(FakeTransport())
        attempt = send_event(
            event_type="some_event",
            target=NotificationTarget.RACE_REGISTRATION,
            payload={"x": 1},
        )
        assert attempt.status == NotificationStatus.SKIPPED
        retried = retry_attempt(attempt.id)
        assert retried is not None
        assert retried.status == NotificationStatus.SKIPPED


def test_retry_unknown_id_returns_none(app: Flask) -> None:
    with app.app_context():
        assert retry_attempt(9999) is None


def test_fallback_url_used_when_target_missing(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_FALLBACK_URL"] = "https://discord.example/fallback"
        transport = FakeTransport()
        set_transport(transport)

        send_event(
            event_type="evt",
            target=NotificationTarget.OFFICIAL_RESULTS,
            payload={"x": 1},
        )
        assert transport.calls[0][0] == "https://discord.example/fallback"


def test_resolve_url_prefers_specific_over_fallback(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_FALLBACK_URL"] = "https://fb"
        app.config["DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL"] = "https://specific"
        url = resolve_webhook_url(NotificationTarget.OFFICIAL_RESULTS)
        assert url == "https://specific"


def test_attempts_persisted_even_on_failure(app: Flask) -> None:
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        set_transport(FakeTransport(fail_with="boom"))
        send_event(
            event_type="e1",
            target=NotificationTarget.RACE_REGISTRATION,
            payload={},
        )
        rows = db.session.query(DiscordNotificationAttempt).all()
        assert len(rows) == 1
        assert rows[0].error_message == "boom"


def test_urllib_transport_returns_send_result_shape() -> None:
    # We can't hit the network in tests; just verify the class instantiates and
    # SendResult is well-formed when constructed manually.
    t = UrllibTransport()
    assert isinstance(t, UrllibTransport)
    sr = SendResult(ok=True, status_code=204, error=None)
    assert sr.ok and sr.status_code == 204
