"""Track strengths — combined Official + Draft win rate breakdown
grouped by surface and distance category."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftMatch,
    DraftMatchStatus,
    DraftRaceResult,
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import profiles as profiles_service


def _ensure_season(app: Flask) -> Season:
    s = (
        db.session.query(Season).first()
    )
    if s:
        return s
    now = datetime.now(UTC)
    s = Season(
        name="S",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    return s


def _make_preset(
    app: Flask, *, name: str, surface: str, category: str
) -> RacePreset:
    distance_for = {"Sprint": 1200, "Mile": 1600, "Medium": 2000, "Long": 2400}
    p = RacePreset(
        source=PresetSource.G1_IMPORT,
        name=name,
        venue="Tokyo",
        surface=surface,
        distance_meters=distance_for[category],
        distance_category=category,
        direction="Left",
        course_variant=None,
        max_runners=18,
        enabled=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _seed_official(
    app: Flask, user_id: int, preset: RacePreset, placements: list[int]
) -> None:
    season = _ensure_season(app)
    for p in placements:
        race = OfficialRace(
            season_id=season.id,
            organizer_user_id=user_id,
            name=f"R-{preset.id}-{p}",
            preset_id=preset.id,
            status=OfficialRaceStatus.COMPLETED,
        )
        db.session.add(race)
        db.session.flush()
        db.session.add(
            OfficialRaceResult(
                official_race_id=race.id,
                user_id=user_id,
                placement=p,
                uma_name="x",
            )
        )
    db.session.commit()


def _seed_draft(
    app: Flask, user_id: int, preset: RacePreset, placements: list[int]
) -> None:
    season = _ensure_season(app)
    for i, p in enumerate(placements):
        match = DraftMatch(
            season_id=season.id,
            host_user_id=user_id,
            join_code=f"D{preset.id:02d}{i:02d}AB",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
            selected_preset_id=preset.id,
        )
        db.session.add(match)
        db.session.flush()
        db.session.add(
            DraftRaceResult(
                draft_match_id=match.id,
                user_id=user_id,
                placement=p,
            )
        )
    db.session.commit()


# ---------- service-level ----------


def test_no_data_returns_empty_zeros(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        ts = profiles_service.track_strengths_for_user(user["id"])
        assert ts.total_races == 0
        assert ts.by_distance == []
        assert ts.by_surface == []
        assert ts.best_distance is None
        assert ts.best_surface is None


def test_combines_official_and_draft(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MileTurf", surface="Turf", category="Mile")
        # 2 official Mile/Turf wins + 1 loss
        _seed_official(app, user["id"], mile_turf, [1, 1, 5])
        # 1 draft Mile/Turf win + 1 loss
        _seed_draft(app, user["id"], mile_turf, [1, 2])
        ts = profiles_service.track_strengths_for_user(user["id"])
        assert ts.total_races == 5
        # Single bucket each.
        assert len(ts.by_distance) == 1
        assert ts.by_distance[0].label == "Mile"
        assert ts.by_distance[0].races == 5
        assert ts.by_distance[0].wins == 3
        assert ts.by_distance[0].win_rate == 0.6
        assert len(ts.by_surface) == 1
        assert ts.by_surface[0].label == "Turf"


def test_best_picks_above_threshold(app: Flask, make_user) -> None:
    """With min_races=3, a 3-race bucket eligible, a 2-race bucket not."""
    user = make_user(username="bob", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MileTurf", surface="Turf", category="Mile")
        sprint_dirt = _make_preset(
            app, name="SprintDirt", surface="Dirt", category="Sprint"
        )
        # Mile/Turf: 3 races, 1 win → 33%
        _seed_official(app, user["id"], mile_turf, [1, 4, 5])
        # Sprint/Dirt: 2 races, 2 wins → 100% but below threshold
        _seed_official(app, user["id"], sprint_dirt, [1, 1])
        ts = profiles_service.track_strengths_for_user(user["id"])
        assert ts.best_distance is not None
        assert ts.best_distance.label == "Mile"
        assert ts.best_surface is not None
        assert ts.best_surface.label == "Turf"
        # Sprint/Dirt buckets visible in breakdowns even though not eligible.
        labels = [b.label for b in ts.by_distance]
        assert "Sprint" in labels


def test_best_tiebreak_prefers_more_races(app: Flask, make_user) -> None:
    """Two eligible buckets at identical win rate — pick the one with
    more races (less variance)."""
    user = make_user(username="tie", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        long_turf = _make_preset(app, name="LT", surface="Turf", category="Long")
        # Both at 50% win rate, but Long has more races.
        _seed_official(app, user["id"], mile_turf, [1, 5, 1, 5])  # 4 races, 2 wins
        _seed_official(
            app, user["id"], long_turf, [1, 5, 1, 5, 1, 5]
        )  # 6 races, 3 wins
        ts = profiles_service.track_strengths_for_user(user["id"])
        assert ts.best_distance is not None
        assert ts.best_distance.label == "Long"


def test_distance_order_is_canonical(app: Flask, make_user) -> None:
    """Breakdowns render in Sprint/Mile/Medium/Long order regardless
    of when buckets were populated."""
    user = make_user(username="order", role=Role.USER)
    with app.app_context():
        long_turf = _make_preset(app, name="LT", surface="Turf", category="Long")
        sprint_turf = _make_preset(app, name="ST", surface="Turf", category="Sprint")
        # Populate Long before Sprint.
        _seed_official(app, user["id"], long_turf, [1, 2, 3])
        _seed_official(app, user["id"], sprint_turf, [1, 2, 3])
        ts = profiles_service.track_strengths_for_user(user["id"])
        labels = [b.label for b in ts.by_distance]
        assert labels == ["Sprint", "Long"]


# ---------- HTTP rendering ----------


def test_card_renders_when_data_exists(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MileTurf", surface="Turf", category="Mile")
        _seed_official(app, user["id"], mile_turf, [1, 2, 3])
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Track strengths" in body
    assert "Best distance" in body
    assert "Mile" in body
    assert "Turf" in body
    assert "33% WR" in body  # 1 win out of 3 races


def test_card_hidden_when_no_races(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="empty", role=Role.USER)
    resp = client.get("/profiles/empty")
    body = resp.data.decode()
    assert "Track strengths" not in body
