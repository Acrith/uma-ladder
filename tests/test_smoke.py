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


# ---------- PR-F3 — Tailwind sanity check ----------


def test_warn_when_tailwind_built_but_output_css_missing(
    caplog, tmp_path
) -> None:
    """When TAILWIND_BUILT=1 but output.css is missing, the app
    boots cleanly but logs a loud error so deploy logs make the
    failure obvious. Catches the case where the Docker stage-1
    build silently failed."""
    import logging
    from pathlib import Path

    from uma_ladder import _warn_if_tailwind_built_but_missing

    class _DummyApp:
        config = {"TAILWIND_BUILT": True}
        static_folder = str(tmp_path)
        logger = logging.getLogger("uma_ladder.test_tailwind_missing")

    # No file present.
    assert not (Path(tmp_path) / "css" / "output.css").exists()
    with caplog.at_level(logging.ERROR, logger="uma_ladder.test_tailwind_missing"):
        _warn_if_tailwind_built_but_missing(_DummyApp())
    messages = [r.message for r in caplog.records]
    assert any("TAILWIND_BUILT=1" in m for m in messages)
    assert any("missing or empty" in m for m in messages)


def test_no_warning_when_tailwind_built_false(caplog) -> None:
    """Dev path with TAILWIND_BUILT off uses the CDN fallback —
    no warning even though output.css doesn't exist."""
    import logging

    from uma_ladder import _warn_if_tailwind_built_but_missing

    class _DummyApp:
        config = {"TAILWIND_BUILT": False}
        static_folder = "/nonexistent/dev"
        logger = logging.getLogger("uma_ladder.test_tailwind_off")

    with caplog.at_level(logging.ERROR, logger="uma_ladder.test_tailwind_off"):
        _warn_if_tailwind_built_but_missing(_DummyApp())
    assert caplog.records == []


def test_no_warning_when_output_css_present(caplog, tmp_path) -> None:
    import logging
    from pathlib import Path

    from uma_ladder import _warn_if_tailwind_built_but_missing

    css_dir = Path(tmp_path) / "css"
    css_dir.mkdir(parents=True)
    (css_dir / "output.css").write_text(
        "/* not really CSS but non-empty */"
    )

    class _DummyApp:
        config = {"TAILWIND_BUILT": True}
        static_folder = str(tmp_path)
        logger = logging.getLogger("uma_ladder.test_tailwind_present")

    with caplog.at_level(logging.ERROR, logger="uma_ladder.test_tailwind_present"):
        _warn_if_tailwind_built_but_missing(_DummyApp())
    assert caplog.records == []
