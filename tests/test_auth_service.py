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
