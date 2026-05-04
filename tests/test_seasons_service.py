from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from uma_ladder.models import SeasonStatus
from uma_ladder.services import seasons as seasons_service


def test_get_active_season_returns_none_when_empty(app: Flask) -> None:
    with app.app_context():
        assert seasons_service.get_active_season() is None


def test_active_season_within_window(app: Flask) -> None:
    with app.app_context():
        now = datetime.now(UTC)
        seasons_service.create_season(
            name="S1",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
        )
        s = seasons_service.get_active_season()
        assert s is not None
        assert s.name == "S1"


def test_inactive_status_excluded(app: Flask) -> None:
    with app.app_context():
        now = datetime.now(UTC)
        seasons_service.create_season(
            name="S1",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.PLANNED,
        )
        assert seasons_service.get_active_season() is None


def test_create_season_validates_window(app: Flask) -> None:
    with app.app_context():
        now = datetime.now(UTC)
        with pytest.raises(ValueError):
            seasons_service.create_season(
                name="bad", starts_at=now, ends_at=now - timedelta(days=1)
            )
