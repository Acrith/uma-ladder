"""Display surfaces must not headline an empty season.

Regression cover for the state found on prod 2026-07-27: 2026 Summer
had opened with a single race while 2026 Spring held a completed
10-race league, so the landing page, the rankings default and every
profile hero rendered the near-empty season — the reigning champion's
profile read "0 / 0 / 0 · 1 race this season".

``get_active_season`` keeps answering "which season do new results
belong to"; ``get_headline_season`` answers "which season do we show".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceStatus,
    OfficialRaceVisibility,
    RacePreset,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import seasons as seasons_service
from uma_ladder.services.auth import RegistrationRequest, register_user


def _make_season(app: Flask, name: str, *, days_ago: int) -> int:
    """Both seasons are ACTIVE and both bracket `now` — that's exactly
    the prod state, where the older season was never closed."""
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name=name,
            starts_at=now - timedelta(days=days_ago),
            ends_at=now + timedelta(days=89),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


def _make_preset(app: Flask) -> int:
    with app.app_context():
        p = RacePreset(
            source=PresetSource.G1_IMPORT,
            name="Nakayama G1",
            venue="Nakayama",
            surface="Turf",
            distance_meters=2200,
            distance_category="Medium",
            direction="Right",
            course_variant="Outer",
            max_runners=18,
            enabled=True,
        )
        db.session.add(p)
        db.session.commit()
        return p.id


def _make_user(app: Flask, username: str) -> int:
    with app.app_context():
        user = register_user(
            RegistrationRequest(username=username, password="hunter2hunter2")
        )
        return user.id


def _seed_result(
    app: Flask, *, season_id: int, preset_id: int, user_id: int, points: int
) -> None:
    with app.app_context():
        race = OfficialRace(
            season_id=season_id,
            name="InyanyaCup",
            organizer_user_id=user_id,
            preset_id=preset_id,
            status=OfficialRaceStatus.COMPLETED,
            visibility=OfficialRaceVisibility.PUBLIC,
        )
        db.session.add(race)
        db.session.flush()
        db.session.add(
            OfficialRaceResult(
                official_race_id=race.id,
                user_id=user_id,
                placement=1,
                points=points,
            )
        )
        db.session.commit()


@pytest.fixture
def two_seasons(app: Flask):
    """Older season holds the results; newer season is empty and wins
    `get_active_season` on its later starts_at."""
    old_id = _make_season(app, "2026 Spring", days_ago=60)
    new_id = _make_season(app, "2026 Summer", days_ago=1)
    preset_id = _make_preset(app)
    user_id = _make_user(app, "champion")
    _seed_result(app, season_id=old_id, preset_id=preset_id, user_id=user_id, points=73)
    return {"old": old_id, "new": new_id, "user": user_id}


def test_active_season_still_prefers_the_newest(app: Flask, two_seasons) -> None:
    """Unchanged: new results belong to the season that just opened."""
    with app.app_context():
        assert seasons_service.get_active_season().id == two_seasons["new"]


def test_headline_season_falls_back_to_the_season_with_results(
    app: Flask, two_seasons
) -> None:
    with app.app_context():
        assert seasons_service.get_headline_season().id == two_seasons["old"]


def test_headline_season_prefers_active_once_it_has_results(
    app: Flask, two_seasons
) -> None:
    preset_id = _make_preset(app)
    _seed_result(
        app,
        season_id=two_seasons["new"],
        preset_id=preset_id,
        user_id=two_seasons["user"],
        points=10,
    )
    with app.app_context():
        assert seasons_service.get_headline_season().id == two_seasons["new"]


def test_headline_season_is_the_active_one_when_nothing_has_results(
    app: Flask,
) -> None:
    _make_season(app, "2026 Spring", days_ago=60)
    new_id = _make_season(app, "2026 Summer", days_ago=1)
    with app.app_context():
        assert seasons_service.get_headline_season().id == new_id


def test_headline_season_is_none_without_any_season(app: Flask) -> None:
    with app.app_context():
        assert seasons_service.get_headline_season() is None


def test_dashboard_ladder_shows_the_season_with_results(
    client: FlaskClient, two_seasons
) -> None:
    body = client.get("/").get_data(as_text=True)
    assert "champion" in body
    # Ladder card is labelled with the season it actually shows...
    assert "2026 Spring" in body
    # ...while the Active-season tile keeps naming the live one.
    assert "2026 Summer" in body


def test_rankings_defaults_to_the_season_with_results(
    client: FlaskClient, two_seasons
) -> None:
    body = client.get("/rankings/").get_data(as_text=True)
    assert "champion" in body
    assert "73" in body


def test_rankings_still_honours_an_explicit_empty_season(
    client: FlaskClient, two_seasons
) -> None:
    """The fallback is a default, not an override — asking for the
    empty season explicitly must still render it empty."""
    body = client.get(f"/rankings/?season={two_seasons['new']}").get_data(as_text=True)
    assert "champion" not in body


def test_anonymous_landing_shows_site_activity(
    client: FlaskClient, two_seasons
) -> None:
    """A stranger sees site-wide activity in the season strip — never
    personal tiles rendered as em-dashes."""
    body = client.get("/").get_data(as_text=True)
    # two_seasons seeds exactly one completed result + one user.
    assert "1 race run" in body
    assert "1 trainer" in body
    assert "Your matches" not in body
