"""Admin Season Management UI — replaces the CLI-only season creation
path so organisers don't need shell access to spin up a new season."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import Role, Season, SeasonStatus
from uma_ladder.services import seasons as seasons_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ---------- service-level tests ----------


def test_set_status_validates_value(app: Flask) -> None:
    with app.app_context():
        season = seasons_service.create_season(
            name="S",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(days=10),
            status=SeasonStatus.PLANNED,
        )
        with pytest.raises(seasons_service.SeasonError):
            seasons_service.set_status(season.id, "garbage")


def test_set_status_transitions_freely(app: Flask) -> None:
    """No state machine — admin can move between any pair of statuses.
    The status flag is informational, not transactional."""
    with app.app_context():
        season = seasons_service.create_season(
            name="S",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(days=10),
            status=SeasonStatus.PLANNED,
        )
        seasons_service.set_status(season.id, SeasonStatus.ACTIVE.value)
        seasons_service.set_status(season.id, SeasonStatus.ARCHIVED.value)
        seasons_service.set_status(season.id, SeasonStatus.PLANNED.value)
        assert db.session.get(Season, season.id).status == SeasonStatus.PLANNED


def test_update_season_validates_date_order(app: Flask) -> None:
    with app.app_context():
        season = seasons_service.create_season(
            name="S",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(days=10),
        )
        with pytest.raises(seasons_service.SeasonError):
            seasons_service.update_season(
                season.id,
                starts_at=datetime.now(UTC) + timedelta(days=20),
            )


def test_update_season_patch_semantics(app: Flask) -> None:
    """Only fields passed as non-None are written; others stay put."""
    with app.app_context():
        original_name = "Original"
        season = seasons_service.create_season(
            name=original_name,
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(days=10),
        )
        seasons_service.update_season(
            season.id, official_enabled=False
        )
        fresh = db.session.get(Season, season.id)
        assert fresh.name == original_name  # unchanged
        assert fresh.official_enabled is False
        assert fresh.draft_enabled is True  # unchanged


def test_get_season_raises_on_missing(app: Flask) -> None:
    with app.app_context(), pytest.raises(seasons_service.SeasonNotFoundError):
        seasons_service.get_season(99999)


# ---------- HTTP layer ----------


def test_non_admin_cannot_access_season_routes(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    for path in ("/admin/seasons", "/admin/seasons/new"):
        resp = client.get(path)
        assert resp.status_code == 403, f"{path} should be 403 for non-admin"


def test_admin_can_create_season(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    _login(client, "adm")
    resp = client.post(
        "/admin/seasons/new",
        data={
            "name": "Spring 2026",
            "starts_at": "2026-03-01T00:00",
            "ends_at": "2026-05-31T23:59",
            "status": SeasonStatus.PLANNED.value,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        seasons = list(db.session.scalars(
            db.session.query(Season).filter_by(name="Spring 2026").statement
        ))
        assert len(seasons) == 1
        assert seasons[0].status == SeasonStatus.PLANNED


def test_admin_can_change_season_status(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        season = seasons_service.create_season(
            name="S",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(days=10),
            status=SeasonStatus.PLANNED,
        )
        season_id = season.id
    _login(client, "adm")
    resp = client.post(
        f"/admin/seasons/{season_id}/status",
        data={"status": SeasonStatus.ACTIVE.value},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(Season, season_id).status == SeasonStatus.ACTIVE


def test_admin_can_edit_season_metadata(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        season = seasons_service.create_season(
            name="Old name",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(days=10),
        )
        season_id = season.id
    _login(client, "adm")
    resp = client.post(
        f"/admin/seasons/{season_id}",
        data={
            "name": "New name",
            "starts_at": "2026-01-01T00:00",
            "ends_at": "2026-12-31T23:59",
            # `official_enabled` checkbox NOT submitted → False
            "draft_enabled": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        fresh = db.session.get(Season, season_id)
        assert fresh.name == "New name"
        assert fresh.official_enabled is False
        assert fresh.draft_enabled is True


def test_seasons_list_renders_active_marker(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        seasons_service.create_season(
            name="ActiveS",
            starts_at=datetime.now(UTC) - timedelta(days=1),
            ends_at=datetime.now(UTC) + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
    _login(client, "adm")
    resp = client.get("/admin/seasons")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "ActiveS" in body
    assert "Active:" in body  # the header active-season hint


def test_admin_index_shows_seasons_tile(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    _login(client, "adm")
    resp = client.get("/admin/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Manage seasons" in body
    # No active season seeded — header should reflect that.
    assert "No active season" in body
