"""PR-Q7 — privacy policy page wiring.

Mirrors test_changelog.py — smokes the route, the file presence, the
footer link, and a 404 path when the source file is missing.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient


def test_privacy_route_renders(client: FlaskClient) -> None:
    resp = client.get("/privacy")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Section headers from PRIVACY.md.
    assert "Privacy policy" in body
    assert "What we collect" in body
    assert "Your rights under GDPR" in body
    # Contact email is the GDPR address users actually need.
    assert "r.krawczak@protonmail.com" in body


def test_privacy_route_is_public(client: FlaskClient) -> None:
    """Page must be reachable without auth so prospective signups
    can read it before creating an account."""
    resp = client.get("/privacy", follow_redirects=False)
    assert resp.status_code == 200


def test_privacy_route_when_file_missing_returns_404(
    client: FlaskClient,
) -> None:
    """If PRIVACY.md is ever lost in a deploy, we'd rather 404
    than render an empty card."""
    import uma_ladder.dashboard.routes as dashboard_routes

    real_root = Path(dashboard_routes.current_app.root_path).parent
    src = real_root / "PRIVACY.md"
    backup = src.read_bytes()
    src.unlink()
    try:
        resp = client.get("/privacy")
        assert resp.status_code == 404
    finally:
        src.write_bytes(backup)


def test_footer_links_to_privacy(client: FlaskClient) -> None:
    """Footer link is how anonymous visitors actually find the page."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "/privacy" in body
    assert "Privacy" in body


def test_privacy_file_exists_at_repo_root(app: Flask) -> None:
    """Smoke the assumption the route hardcodes — file at repo root."""
    repo_root = Path(app.root_path).parent
    assert (repo_root / "PRIVACY.md").exists()
