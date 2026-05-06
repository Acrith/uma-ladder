"""Track strengths — per-mode breakdown (PR-I5).

Draft side uses match-win-rate: 1v1-team format with a ~50% baseline
makes win rate a meaningful primary signal. Counted per match (not
per uma row), so a 2v2 match where the user owned 2 umas counts
once.

Official side uses podium-rate (placement <= 3): 12-18 player fields
make a strict placement-1 win rate too noisy. Top-3 captures the
"consistently competitive" signal at a comparable scale.

Each side is computed and gated by min_races independently. The
profile card shows draft above official; the hero "Best track" tile
prefers whichever side has more data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftMatch,
    DraftMatchStatus,
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
    s = db.session.query(Season).first()
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
    """Each entry in `placements` becomes one OfficialRaceResult row.
    Podium = placement <= 3 under the new (PR-I5) metric."""
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


def _seed_draft_matches(
    app: Flask,
    user_id: int,
    opp_user_id: int,
    preset: RacePreset,
    *,
    wins: int,
    losses: int,
) -> None:
    """Seeds completed draft matches between `user_id` and
    `opp_user_id`. PR-I5 reads `winner_user_id` from the match (not
    a per-uma DraftRaceResult row), so we don't bother seeding
    result rows here."""
    season = _ensure_season(app)
    for i in range(wins + losses):
        won = i < wins
        match = DraftMatch(
            season_id=season.id,
            host_user_id=user_id,
            opponent_user_id=opp_user_id,
            join_code=f"D{preset.id:03d}{i:03d}",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
            selected_preset_id=preset.id,
            winner_user_id=user_id if won else opp_user_id,
            loser_user_id=opp_user_id if won else user_id,
        )
        db.session.add(match)
    db.session.commit()


# ---------- service-level: empty / shape ----------


def test_no_data_returns_empty_zeros_per_mode(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        ts = profiles_service.track_strengths_for_user(user["id"])
        # Both sides are present; both sides are empty.
        assert ts.draft.total_races == 0
        assert ts.draft.by_distance == []
        assert ts.draft.best_distance is None
        assert ts.official.total_races == 0
        assert ts.official.by_distance == []
        assert ts.official.best_distance is None


# ---------- draft side: match-win-rate, per-match ----------


def test_draft_metric_counts_matches_not_uma_rows(app: Flask, make_user) -> None:
    """A 2v2 match where the user owned 2 umas counts ONCE in track
    strengths — the prior per-row counter inflated the race total."""
    user = make_user(username="alice", role=Role.USER)
    opp = make_user(username="bob", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MileTurf", surface="Turf", category="Mile")
        _seed_draft_matches(
            app, user["id"], opp["id"], mile_turf, wins=3, losses=2
        )
        ts = profiles_service.track_strengths_for_user(user["id"])
        assert ts.draft.total_races == 5
        assert ts.draft.by_distance[0].label == "Mile"
        assert ts.draft.by_distance[0].races == 5
        assert ts.draft.by_distance[0].wins == 3
        assert ts.draft.by_distance[0].win_rate == 0.6


def test_draft_picks_best_via_min_races(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    opp = make_user(username="bob", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        sprint_dirt = _make_preset(app, name="SD", surface="Dirt", category="Sprint")
        # Mile/Turf: 4 matches, 1 win → 25%, eligible.
        _seed_draft_matches(app, user["id"], opp["id"], mile_turf, wins=1, losses=3)
        # Sprint/Dirt: 2 matches, 2 wins → 100%, NOT eligible (below min).
        _seed_draft_matches(app, user["id"], opp["id"], sprint_dirt, wins=2, losses=0)
        ts = profiles_service.track_strengths_for_user(user["id"])
        # Best picks the eligible bucket even though its rate is lower.
        assert ts.draft.best_distance.label == "Mile"
        # Sprint still appears in the breakdown for transparency.
        assert "Sprint" in [b.label for b in ts.draft.by_distance]


# ---------- official side: podium rate (top 3) ----------


def test_official_metric_uses_podium_top3(app: Flask, make_user) -> None:
    """A placement <= 3 counts as a "win" for the official-side
    metric. Five races at [1, 2, 3, 5, 8] → 3 podiums → 60%."""
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        _seed_official(app, user["id"], mile_turf, [1, 2, 3, 5, 8])
        ts = profiles_service.track_strengths_for_user(user["id"])
        assert ts.official.total_races == 5
        assert ts.official.by_distance[0].label == "Mile"
        assert ts.official.by_distance[0].races == 5
        assert ts.official.by_distance[0].wins == 3  # 1, 2, 3 are podiums
        assert ts.official.by_distance[0].win_rate == 0.6


def test_official_best_tiebreak_prefers_more_races(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        long_turf = _make_preset(app, name="LT", surface="Turf", category="Long")
        # Both at 50% podium rate; Long has more races → wins tiebreak.
        _seed_official(app, user["id"], mile_turf, [1, 5, 1, 5])  # 4 races, 2 podiums
        _seed_official(
            app, user["id"], long_turf, [1, 5, 1, 5, 1, 5]
        )  # 6 races, 3 podiums
        ts = profiles_service.track_strengths_for_user(user["id"])
        assert ts.official.best_distance.label == "Long"


def test_distance_order_is_canonical_per_mode(app: Flask, make_user) -> None:
    """Sprint/Mile/Medium/Long order regardless of insert order, on
    each mode independently."""
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        long_turf = _make_preset(app, name="LT", surface="Turf", category="Long")
        sprint_turf = _make_preset(app, name="ST", surface="Turf", category="Sprint")
        # Populate Long before Sprint.
        _seed_official(app, user["id"], long_turf, [1, 2, 3])
        _seed_official(app, user["id"], sprint_turf, [1, 2, 3])
        ts = profiles_service.track_strengths_for_user(user["id"])
        labels = [b.label for b in ts.official.by_distance]
        assert labels == ["Sprint", "Long"]


# ---------- HTTP rendering ----------


def test_card_renders_official_side(
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
    # Official section heading shows up because user has official data.
    assert "Official ·" in body
    # Mile and Turf labels present.
    assert "Mile" in body
    assert "Turf" in body
    # Podium rate metric — three races, all podium → 100%.
    assert "100%" in body
    # Draft section heading should NOT render when user has no draft data.
    assert "Draft ·" not in body


def test_card_renders_draft_side_when_only_draft_data(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    opp = make_user(username="bob", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        _seed_draft_matches(
            app, user["id"], opp["id"], mile_turf, wins=2, losses=1
        )
    resp = client.get("/profiles/alice")
    body = resp.data.decode()
    assert "Track strengths" in body
    assert "Draft ·" in body
    assert "Official ·" not in body
    # 2/3 wins → 66% (rounded down to 67% in template's '%.0f' format).
    assert "67%" in body


def test_card_renders_both_sections_with_divider(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    opp = make_user(username="bob", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        _seed_official(app, user["id"], mile_turf, [1, 2, 3])
        _seed_draft_matches(
            app, user["id"], opp["id"], mile_turf, wins=2, losses=1
        )
    resp = client.get("/profiles/alice")
    body = resp.data.decode()
    assert "Draft ·" in body
    assert "Official ·" in body


def test_card_hidden_when_no_races(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="empty", role=Role.USER)
    resp = client.get("/profiles/empty")
    body = resp.data.decode()
    assert "Track strengths" not in body


# ---------- Best Track hero tile ----------


def test_best_track_tile_empty_when_no_data(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", role=Role.USER)
    resp = client.get("/profiles/alice")
    body = resp.data.decode()
    assert "Best track" in body
    assert "Not enough data" in body


def test_best_track_tile_prefers_draft_when_more_data_there(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Hero tile reads from whichever mode the user has more data in.
    Draft preferred on ties — that's where most players spend time."""
    user = make_user(username="alice", role=Role.USER)
    opp = make_user(username="bob", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        # 5 draft matches, 3 wins.
        _seed_draft_matches(
            app, user["id"], opp["id"], mile_turf, wins=3, losses=2
        )
        # 1 official race, 1 podium (less data than draft).
        _seed_official(app, user["id"], mile_turf, [1])
    resp = client.get("/profiles/alice")
    body = resp.data.decode()
    assert "Mile · Turf" in body
    # Draft metric label is "WR".
    assert "WR" in body
    assert "60%" in body  # 3/5 win rate from the draft side


def test_best_track_tile_uses_official_when_only_official_data(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    with app.app_context():
        mile_turf = _make_preset(app, name="MT", surface="Turf", category="Mile")
        _seed_official(app, user["id"], mile_turf, [1, 4, 5])
    resp = client.get("/profiles/alice")
    body = resp.data.decode()
    assert "Mile · Turf" in body
    # Official metric label is "podium".
    assert "podium" in body
