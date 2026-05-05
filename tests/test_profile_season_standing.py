"""Hero tiles: Official podiums + Season rank.

Reads season_standing_for_user (which reuses season_ladder) to fill
the two new hero tiles. Tile copy varies based on whether there's an
active season + whether the user has any results in it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import official as official_service


def _make_season(app: Flask) -> Season:
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


def _make_preset(app: Flask) -> RacePreset:
    p = (
        db.session.query(RacePreset).first()
    )
    if p:
        return p
    p = RacePreset(
        source=PresetSource.G1_IMPORT,
        name="Tokyo G1",
        venue="Tokyo",
        surface="Turf",
        distance_meters=2000,
        distance_category="Medium",
        direction="Left",
        course_variant=None,
        max_runners=18,
        enabled=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _seed_result(
    app: Flask, *, season: Season, preset: RacePreset, user_id: int, placement: int, points: int
) -> None:
    race = OfficialRace(
        season_id=season.id,
        organizer_user_id=user_id,
        name=f"R-{user_id}-{placement}",
        preset_id=preset.id,
        status=OfficialRaceStatus.COMPLETED,
    )
    db.session.add(race)
    db.session.flush()
    db.session.add(
        OfficialRaceResult(
            official_race_id=race.id,
            user_id=user_id,
            uma_name="x",
            placement=placement,
            points=points,
        )
    )
    db.session.commit()


# ---------- service-level ----------


def test_returns_none_when_user_has_no_results(app: Flask, make_user) -> None:
    user = make_user(username="solo", role=Role.USER)
    with app.app_context():
        season = _make_season(app)
        assert official_service.season_standing_for_user(
            user["id"], season.id
        ) is None


def test_returns_standing_with_correct_rank_and_counts(
    app: Flask, make_user
) -> None:
    """Two players race; the higher-points one ranks #1."""
    alice = make_user(username="alice", role=Role.USER)
    bob = make_user(username="bob", role=Role.USER)
    with app.app_context():
        season = _make_season(app)
        preset = _make_preset(app)
        # alice: 1st + 2nd + 3rd (10+5+3 = 18)
        _seed_result(app, season=season, preset=preset, user_id=alice["id"], placement=1, points=10)
        _seed_result(app, season=season, preset=preset, user_id=alice["id"], placement=2, points=5)
        _seed_result(app, season=season, preset=preset, user_id=alice["id"], placement=3, points=3)
        # bob: 1st + 1st (20)
        _seed_result(app, season=season, preset=preset, user_id=bob["id"], placement=1, points=10)
        _seed_result(app, season=season, preset=preset, user_id=bob["id"], placement=1, points=10)

        alice_standing = official_service.season_standing_for_user(
            alice["id"], season.id
        )
        bob_standing = official_service.season_standing_for_user(
            bob["id"], season.id
        )
        assert bob_standing is not None
        assert alice_standing is not None
        assert bob_standing.rank == 1
        assert alice_standing.rank == 2
        assert bob_standing.total_players == 2
        # alice's podium counts:
        assert alice_standing.top1 == 1
        assert alice_standing.top2 == 1
        assert alice_standing.top3 == 1
        assert alice_standing.races_entered == 3
        assert alice_standing.total_points == 18
        assert bob_standing.top1 == 2


def test_only_counts_target_season(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        now = datetime.now(UTC)
        s1 = Season(
            name="S1",
            starts_at=now - timedelta(days=10),
            ends_at=now - timedelta(days=1),
            status=SeasonStatus.COMPLETED,
        )
        s2 = Season(
            name="S2",
            starts_at=now,
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add_all([s1, s2])
        db.session.commit()
        preset = _make_preset(app)
        # Old season win shouldn't appear in S2's standing.
        _seed_result(app, season=s1, preset=preset, user_id=user["id"], placement=1, points=10)
        assert official_service.season_standing_for_user(
            user["id"], s2.id
        ) is None


# ---------- HTTP rendering ----------


def test_profile_renders_real_standing(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Active season + real results → both tiles show real numbers."""
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        season = _make_season(app)
        preset = _make_preset(app)
        _seed_result(
            app, season=season, preset=preset, user_id=user["id"],
            placement=1, points=10,
        )
        _seed_result(
            app, season=season, preset=preset, user_id=user["id"],
            placement=2, points=5,
        )
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Official podiums tile: "1 / 1 / 0" (1×top1, 1×top2, 0×top3).
    assert "Official podiums" in body
    assert "2 race" in body  # races_entered subtitle
    # Season rank tile: #1 of 1 · 15 pts.
    assert "Season rank" in body
    assert "#1" in body
    assert "of 1" in body
    assert "15 pts" in body


def test_profile_falls_back_to_empty_state_when_no_active_season(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """No active season → both tiles render their 'no active season'
    empty copy without crashing."""
    make_user(username="alice", role=Role.USER)
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Official podiums" in body
    assert "No active season" in body
    assert "#—" in body  # Season rank empty placeholder


def test_profile_shows_zero_podiums_for_active_user_with_no_results(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Active season + user hasn't raced yet → tile shows '0 / 0 / 0'
    + 'Top 1 / 2 / 3 this season' instead of 'No active season'."""
    make_user(username="alice", role=Role.USER)
    with app.app_context():
        _make_season(app)
    resp = client.get("/profiles/alice")
    body = resp.data.decode()
    assert "Top 1 / 2 / 3" in body
    assert "No official races yet" in body
