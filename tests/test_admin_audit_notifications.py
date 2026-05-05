"""PR-E1: Admin audit log mirrors every privileged action to the
ADMIN_AUDIT Discord webhook channel.

Coexists with the per-action player notifications (race cancel still
posts to the player channel; admin channel gets the full moderation
feed). Failures must stay isolated — a broken admin webhook can never
prevent the audit row from being written."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    DiscordNotificationAttempt,
    NotificationEvent,
    NotificationStatus,
    NotificationTarget,
    Role,
    Season,
    SeasonStatus,
    User,
)
from uma_ladder.models.users import Role as RoleEnum
from uma_ladder.notifications.discord import FakeTransport, set_transport
from uma_ladder.services import admin as admin_service
from uma_ladder.services import admin_audit
from uma_ladder.services import official as official_service


def _ensure_season() -> Season:
    s = db.session.query(Season).first()
    if s:
        return s
    now = datetime.now(UTC)
    s = Season(
        name="S",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    return s


def test_log_action_fires_admin_audit_notification(
    app: Flask, make_user
) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    with app.app_context():
        app.config["DISCORD_WEBHOOK_ADMIN_AUDIT_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)

        admin_audit.log_action(
            actor_user_id=actor["id"],
            action="role_change",
            target_user_id=target["id"],
            before={"role": "user"},
            after={"role": "organizer"},
        )

        assert len(transport.calls) == 1
        url, payload = transport.calls[0]
        assert url == "https://x"
        # Embed contains actor, target, action, before/after.
        body = str(payload)
        assert "adm" in body
        assert "bob" in body
        assert "role_change" in body
        assert "user" in body
        assert "organizer" in body


def test_log_action_writes_attempt_row(
    app: Flask, make_user
) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        app.config["DISCORD_WEBHOOK_ADMIN_AUDIT_URL"] = "https://x"
        set_transport(FakeTransport())

        admin_audit.log_action(
            actor_user_id=actor["id"],
            action="role_change",
            details="manual",
        )

        rows = db.session.query(DiscordNotificationAttempt).all()
        assert len(rows) == 1
        assert rows[0].event_type == NotificationEvent.ADMIN_ACTION
        assert rows[0].target_name == NotificationTarget.ADMIN_AUDIT
        assert rows[0].status == NotificationStatus.SENT


def test_admin_audit_skipped_when_webhook_not_configured(
    app: Flask, make_user
) -> None:
    """No ADMIN_AUDIT URL + no FALLBACK URL → notification row records
    SKIPPED but the audit row itself still commits cleanly."""
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        app.config["DISCORD_WEBHOOK_ADMIN_AUDIT_URL"] = None
        app.config["DISCORD_WEBHOOK_FALLBACK_URL"] = None
        set_transport(FakeTransport())

        row = admin_audit.log_action(
            actor_user_id=actor["id"], action="role_change"
        )
        assert row is not None
        assert row.id is not None

        attempts = db.session.query(DiscordNotificationAttempt).all()
        assert len(attempts) == 1
        assert attempts[0].status == NotificationStatus.SKIPPED


def test_notification_failure_does_not_break_audit_log(
    app: Flask, make_user
) -> None:
    """Even if the Discord webhook blows up, the audit row must
    still exist — the audit table is the source of truth."""
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        app.config["DISCORD_WEBHOOK_ADMIN_AUDIT_URL"] = "https://x"
        set_transport(FakeTransport(fail_with="boom"))

        row = admin_audit.log_action(
            actor_user_id=actor["id"],
            action="role_change",
            details="forced-fail",
        )
        assert row is not None
        assert row.id is not None

        attempts = db.session.query(DiscordNotificationAttempt).all()
        assert len(attempts) == 1
        assert attempts[0].status == NotificationStatus.FAILED


def test_role_change_fires_admin_audit_via_service_layer(
    app: Flask, make_user
) -> None:
    """End-to-end: change_user_role → admin_audit.log_action →
    notify_admin_action. Verifies the full chain wires up correctly
    from a real privileged action (not just a direct log_action call)."""
    actor = make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    with app.app_context():
        app.config["DISCORD_WEBHOOK_ADMIN_AUDIT_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)

        admin_service.change_user_role(
            actor=db.session.get(User, actor["id"]),
            target=db.session.get(User, target["id"]),
            new_role=RoleEnum.ORGANIZER,
        )

        admin_calls = [
            payload for _, payload in transport.calls
            if "Admin" in str(payload)
        ]
        assert len(admin_calls) == 1
        assert "role_change" in str(admin_calls[0])


def test_race_cancel_fires_both_player_and_admin_notifications(
    app: Flask, make_user
) -> None:
    """Race cancel posts to RACE_REGISTRATION (player-facing) AND
    ADMIN_AUDIT (mod-facing) — neither should swallow the other."""
    org = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context():
        app.config["DISCORD_WEBHOOK_ADMIN_AUDIT_URL"] = "https://admin"
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://reg"
        transport = FakeTransport()
        set_transport(transport)

        season = _ensure_season()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id,
                organizer_user_id=org["id"],
                name="ToCancel",
            )
        )
        official_service.cancel_race(race.id, by_user_id=org["id"])

        admin_urls = [url for url, _ in transport.calls if url == "https://admin"]
        reg_urls = [url for url, _ in transport.calls if url == "https://reg"]
        assert len(admin_urls) == 1
        assert len(reg_urls) >= 1
