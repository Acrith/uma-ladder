from __future__ import annotations

import time

import pytest
from flask import Flask

from uma_ladder.services import auth as auth_service


def test_register_then_authenticate(app: Flask) -> None:
    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(username="Alice", password="password123")
        )
        # username normalized
        assert user.username == "alice"

        same = auth_service.authenticate("alice", "password123")
        assert same.id == user.id


def test_register_rejects_duplicate(app: Flask) -> None:
    with app.app_context():
        auth_service.register_user(
            auth_service.RegistrationRequest(username="alice", password="password123")
        )
        with pytest.raises(auth_service.UsernameTakenError):
            auth_service.register_user(
                auth_service.RegistrationRequest(username="ALICE", password="password123")
            )


def test_authenticate_wrong_password(app: Flask) -> None:
    with app.app_context():
        auth_service.register_user(
            auth_service.RegistrationRequest(username="alice", password="password123")
        )
        with pytest.raises(auth_service.InvalidCredentialsError):
            auth_service.authenticate("alice", "WRONG")


def test_authenticate_unknown_user(app: Flask) -> None:
    with app.app_context(), pytest.raises(auth_service.InvalidCredentialsError):
        auth_service.authenticate("ghost", "password123")


def test_reset_token_round_trip(app: Flask) -> None:
    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(username="alice", password="password123")
        )
        token = auth_service.issue_reset_token(app.config["SECRET_KEY"], user)
        loaded = auth_service.consume_reset_token(app.config["SECRET_KEY"], token)
        assert loaded.id == user.id


def test_reset_token_expires(app: Flask) -> None:
    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(username="alice", password="password123")
        )
        token = auth_service.issue_reset_token(app.config["SECRET_KEY"], user)
        time.sleep(2.1)
        with pytest.raises(auth_service.InvalidResetTokenError):
            auth_service.consume_reset_token(
                app.config["SECRET_KEY"], token, max_age_seconds=1
            )


def test_reset_token_tampered(app: Flask) -> None:
    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(username="alice", password="password123")
        )
        token = auth_service.issue_reset_token(app.config["SECRET_KEY"], user)
        with pytest.raises(auth_service.InvalidResetTokenError):
            auth_service.consume_reset_token(
                app.config["SECRET_KEY"], token + "junk"
            )


# ---------- PR-J10 — login lockout ----------


def test_failed_logins_increment_counter(app: Flask) -> None:
    """Each wrong-password attempt bumps failed_login_count + sets
    failed_login_at, but doesn't lock until MAX_FAILED_LOGINS."""
    from uma_ladder.extensions import db
    from uma_ladder.models import User

    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(
                username="alice", password="password123"
            )
        )
        for _ in range(auth_service.MAX_FAILED_LOGINS - 1):
            with pytest.raises(auth_service.InvalidCredentialsError):
                auth_service.authenticate("alice", "WRONG")
        refreshed = db.session.get(User, user.id)
        assert (
            refreshed.failed_login_count
            == auth_service.MAX_FAILED_LOGINS - 1
        )
        assert refreshed.failed_login_at is not None
        # Still allowed to keep trying — the right password still works.
        auth_service.authenticate("alice", "password123")
        refreshed = db.session.get(User, user.id)
        assert refreshed.failed_login_count == 0
        assert refreshed.failed_login_at is None


def test_lockout_after_max_failures(app: Flask) -> None:
    """The MAX_FAILED_LOGINS-th wrong attempt triggers a lockout —
    subsequent attempts fail with RateLimitedError BEFORE the
    password is even checked."""
    with app.app_context():
        auth_service.register_user(
            auth_service.RegistrationRequest(
                username="alice", password="password123"
            )
        )
        for _ in range(auth_service.MAX_FAILED_LOGINS):
            with pytest.raises(auth_service.InvalidCredentialsError):
                auth_service.authenticate("alice", "WRONG")

        # Even the CORRECT password is now rejected by the lockout —
        # this is the proof that the gate fires before password
        # verification.
        with pytest.raises(auth_service.RateLimitedError) as exc_info:
            auth_service.authenticate("alice", "password123")
        assert exc_info.value.retry_after_seconds > 0


def test_lockout_self_clears_after_window(
    app: Flask, monkeypatch
) -> None:
    """If the lockout window elapses, the next attempt re-enters
    the unlocked path. Simulated by rewinding failed_login_at past
    the LOCKOUT_DURATION."""
    from uma_ladder.extensions import db
    from uma_ladder.models import User

    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(
                username="alice", password="password123"
            )
        )
        for _ in range(auth_service.MAX_FAILED_LOGINS):
            with pytest.raises(auth_service.InvalidCredentialsError):
                auth_service.authenticate("alice", "WRONG")

        # Rewind the timestamp past the cooldown to fake the wait.
        from datetime import UTC, datetime, timedelta

        refreshed = db.session.get(User, user.id)
        refreshed.failed_login_at = datetime.now(UTC) - (
            auth_service.LOCKOUT_DURATION + timedelta(seconds=1)
        )
        db.session.commit()

        # Correct password must now succeed and reset the counter.
        ok = auth_service.authenticate("alice", "password123")
        assert ok.id == user.id
        refreshed = db.session.get(User, user.id)
        assert refreshed.failed_login_count == 0


def test_lockout_does_not_apply_to_unknown_user(app: Flask) -> None:
    """No row to lock against — unknown usernames must always raise
    plain InvalidCredentials, never RateLimited. Otherwise an
    attacker could probe whether a username exists by watching
    for the lockout response shape."""
    with app.app_context():
        for _ in range(auth_service.MAX_FAILED_LOGINS + 5):
            with pytest.raises(auth_service.InvalidCredentialsError):
                auth_service.authenticate("ghost", "anything")


def test_correct_password_resets_counter(app: Flask) -> None:
    """A successful login mid-streak zeroes both fields so the
    next failed attempt starts fresh — important so a single
    fat-fingered session doesn't carry baggage forever."""
    from uma_ladder.extensions import db
    from uma_ladder.models import User

    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(
                username="alice", password="password123"
            )
        )
        for _ in range(3):
            with pytest.raises(auth_service.InvalidCredentialsError):
                auth_service.authenticate("alice", "WRONG")
        refreshed = db.session.get(User, user.id)
        assert refreshed.failed_login_count == 3

        auth_service.authenticate("alice", "password123")

        refreshed = db.session.get(User, user.id)
        assert refreshed.failed_login_count == 0
        assert refreshed.failed_login_at is None


def test_failed_login_after_window_starts_fresh(app: Flask) -> None:
    """If the cooldown elapsed naturally (no lockout was triggered,
    just a stale failed_login_at), a new failure resets the counter
    to 1 rather than continuing to climb."""
    from uma_ladder.extensions import db
    from uma_ladder.models import User

    with app.app_context():
        user = auth_service.register_user(
            auth_service.RegistrationRequest(
                username="alice", password="password123"
            )
        )
        for _ in range(3):
            with pytest.raises(auth_service.InvalidCredentialsError):
                auth_service.authenticate("alice", "WRONG")

        from datetime import UTC, datetime, timedelta

        refreshed = db.session.get(User, user.id)
        refreshed.failed_login_at = datetime.now(UTC) - (
            auth_service.LOCKOUT_DURATION + timedelta(seconds=1)
        )
        db.session.commit()

        with pytest.raises(auth_service.InvalidCredentialsError):
            auth_service.authenticate("alice", "WRONG")

        refreshed = db.session.get(User, user.id)
        assert refreshed.failed_login_count == 1
