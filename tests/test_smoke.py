from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder import create_app


def test_factory_returns_flask_app() -> None:
    app = create_app("testing")
    assert isinstance(app, Flask)
    assert app.config["TESTING"] is True


def test_healthz_returns_ok(client: FlaskClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/auth/",
        "/profiles/",
        "/official/",
        "/draft/",
        "/presets/",
        "/ocr/",
    ],
)
def test_blueprint_routes_respond(client: FlaskClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_production_config_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from uma_ladder.config import ConfigError, get_config

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError):
        get_config("production")
