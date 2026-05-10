from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder import create_app
from uma_ladder.extensions import db
from uma_ladder.services.auth import RegistrationRequest, register_user


@pytest.fixture
def app() -> Iterator[Flask]:
    fd, path = tempfile.mkstemp(prefix="uma_ladder_test_", suffix=".sqlite")
    os.close(fd)
    app = create_app("testing")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{path}"
    with app.app_context():
        db.create_all()
        # PR-P2 — seed the achievements catalogue. db.create_all()
        # only mirrors the model schema; data migrations don't run,
        # so without this every achievements-related test would
        # see an empty catalogue.
        from uma_ladder.services.achievements import ensure_starter_seed

        ensure_starter_seed()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(path)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def make_user(app: Flask):
    """Factory that creates a user inside an app context and returns it detached-by-id."""

    def _make(username: str = "alice", password: str = "password123", role: str = "user"):
        with app.app_context():
            user = register_user(
                RegistrationRequest(username=username, password=password, role=role)
            )
            return {"id": user.id, "username": user.username, "password": password, "role": role}

    return _make
