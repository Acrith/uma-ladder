"""Discord webhook transport + send-and-log helper.

Design notes:
- The transport is injectable so tests use a FakeTransport and never hit the
  network.
- `send_event` always writes a `DiscordNotificationAttempt` row, even when
  the webhook URL is missing (status=skipped) — so admins can see why a
  notification didn't fire.
- Sending is synchronous in MVP. RQ/Celery comes later if needed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import (
    DiscordNotificationAttempt,
    NotificationStatus,
    NotificationTarget,
)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status_code: int | None
    error: str | None


class WebhookTransport(ABC):
    @abstractmethod
    def post(self, url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> SendResult:
        ...


class UrllibTransport(WebhookTransport):
    """Default production transport using stdlib urllib."""

    def post(self, url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> SendResult:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return SendResult(ok=200 <= resp.status < 300, status_code=resp.status, error=None)
        except urllib.error.HTTPError as exc:
            return SendResult(ok=False, status_code=exc.code, error=str(exc))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return SendResult(ok=False, status_code=None, error=str(exc))


class FakeTransport(WebhookTransport):
    """Records calls in-memory; configurable to fail."""

    def __init__(self, *, fail_with: str | None = None, status_code: int = 204) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_with = fail_with
        self.status_code = status_code

    def post(self, url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> SendResult:
        self.calls.append((url, payload))
        if self.fail_with is not None:
            return SendResult(ok=False, status_code=None, error=self.fail_with)
        return SendResult(ok=True, status_code=self.status_code, error=None)


_TARGET_CONFIG_KEYS: dict[str, str] = {
    NotificationTarget.RACE_REGISTRATION: "DISCORD_WEBHOOK_RACE_REGISTRATION_URL",
    NotificationTarget.OFFICIAL_RESULTS: "DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL",
    NotificationTarget.DRAFT_RESULTS: "DISCORD_WEBHOOK_DRAFT_RESULTS_URL",
    NotificationTarget.ADMIN_AUDIT: "DISCORD_WEBHOOK_ADMIN_AUDIT_URL",
    NotificationTarget.FALLBACK: "DISCORD_WEBHOOK_FALLBACK_URL",
}


def resolve_webhook_url(target: str) -> str | None:
    """Look up the URL for a logical target. Falls back to FALLBACK url."""
    direct_key = _TARGET_CONFIG_KEYS.get(target)
    if direct_key:
        url = current_app.config.get(direct_key)
        if url:
            return url
    fallback = current_app.config.get(_TARGET_CONFIG_KEYS[NotificationTarget.FALLBACK])
    return fallback or None


def get_transport() -> WebhookTransport:
    """Return the configured transport, defaulting to UrllibTransport."""
    transport = current_app.extensions.get("uma_ladder.discord_transport")
    if transport is None:
        transport = UrllibTransport()
        current_app.extensions["uma_ladder.discord_transport"] = transport
    return transport


def set_transport(transport: WebhookTransport) -> None:
    """Override the transport (used by tests)."""
    current_app.extensions["uma_ladder.discord_transport"] = transport


def send_event(
    *,
    event_type: str,
    target: str,
    payload: dict[str, Any],
) -> DiscordNotificationAttempt:
    """Send a Discord webhook payload and record the attempt.

    Always writes a DB row; never raises on transport failures (returns the
    attempt with status=failed instead).
    """
    attempt = DiscordNotificationAttempt(
        event_type=event_type,
        target_name=target,
        payload_json=payload,
        status=NotificationStatus.PENDING,
    )
    db.session.add(attempt)
    db.session.commit()

    url = resolve_webhook_url(target)
    if not url:
        attempt.status = NotificationStatus.SKIPPED
        attempt.error_message = f"no webhook URL configured for target {target!r}"
        db.session.commit()
        return attempt

    result = get_transport().post(url, payload)
    attempt.response_code = result.status_code
    attempt.error_message = result.error
    if result.ok:
        attempt.status = NotificationStatus.SENT
        attempt.sent_at = datetime.now(UTC)
    else:
        attempt.status = NotificationStatus.FAILED
    db.session.commit()
    return attempt


def retry_attempt(attempt_id: int) -> DiscordNotificationAttempt | None:
    """Retry a failed attempt. Updates the row in place."""
    attempt = db.session.get(DiscordNotificationAttempt, attempt_id)
    if attempt is None:
        return None
    if attempt.status not in (NotificationStatus.FAILED, NotificationStatus.SKIPPED):
        return attempt
    url = resolve_webhook_url(attempt.target_name)
    if not url:
        attempt.status = NotificationStatus.SKIPPED
        attempt.error_message = f"no webhook URL configured for target {attempt.target_name!r}"
        db.session.commit()
        return attempt
    result = get_transport().post(url, attempt.payload_json)
    attempt.response_code = result.status_code
    attempt.error_message = result.error
    if result.ok:
        attempt.status = NotificationStatus.SENT
        attempt.sent_at = datetime.now(UTC)
    else:
        attempt.status = NotificationStatus.FAILED
    db.session.commit()
    return attempt
