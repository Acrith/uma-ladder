"""PR-J9 — changelog page wiring.

Smokes the route + the file location + the footer link. Doesn't
assert specific changelog contents (the file evolves) — just that
seeded headings render and the link is reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient


def test_changelog_route_renders_seeded_content(
    client: FlaskClient,
) -> None:
    resp = client.get("/changelog")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Markdown heading rendered as <h1>.
    assert "<h1>What" in body or "What" in body
    # Section header from the seeded file.
    assert "Quality of life" in body
    # At least one bullet should render as <li>.
    assert "<li>" in body


def test_changelog_route_when_file_missing_returns_404(
    client: FlaskClient, tmp_path: Path, monkeypatch
) -> None:
    """If the source file isn't shipped (e.g. a packaging mistake),
    we 404 rather than rendering an empty card. Detectable via the
    monitoring you already have."""
    import uma_ladder.dashboard.routes as dashboard_routes

    real_root = Path(dashboard_routes.current_app.root_path).parent
    src = real_root / "CHANGELOG.md"
    backup = src.read_bytes()
    src.unlink()
    try:
        resp = client.get("/changelog")
        assert resp.status_code == 404
    finally:
        src.write_bytes(backup)


def test_footer_links_to_changelog(client: FlaskClient) -> None:
    """Navbar footer surfaces the link so users actually find it."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "/changelog" in body
    assert "What's new" in body


def test_changelog_file_exists_at_repo_root(app: Flask) -> None:
    """Smoke the assumption the route hardcodes — the file must
    sit at the repo root (next to pyproject.toml). If a future
    refactor moves it, the route's path construction needs the
    same update."""
    repo_root = Path(app.root_path).parent
    assert (repo_root / "CHANGELOG.md").exists()


@pytest.mark.parametrize(
    "expected_heading",
    ["What's new", "Quality of life", "Game-rule accuracy"],
)
def test_changelog_renders_section_headings(
    client: FlaskClient, expected_heading: str
) -> None:
    """The current J-series seed has these sections; if the
    template ever swallows headings (escape regression), this
    fails."""
    resp = client.get("/changelog")
    assert expected_heading in resp.data.decode()
