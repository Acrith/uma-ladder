"""Most Used Umas — service helper + profile rendering.

Counts placements grouped by uma_character_id across both kinds.
Filters via min_races so a single one-off pick doesn't dominate the
card. Custom-name results (no character id) are skipped.
"""

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
    UmaCharacter,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import profiles as profiles_service


def _make_chars(app: Flask, n: int) -> list[int]:
    with app.app_context():
        chars = [
            UmaCharacter(slug=f"c-{i}", name_en=f"Char {i}", image_url=f"http://img/{i}")
            for i in range(n)
        ]
        db.session.add_all(chars)
        db.session.commit()
        return [c.id for c in chars]


def _seed_official_results(
    app: Flask, user_id: int, char_id: int, placements: list[int]
) -> None:
    """One race per placement; (race_id, user_id) is uniquely
    constrained, so we spawn a fresh race per row."""
    with app.app_context():
        season = (
            db.session.query(Season).first()
            or _ensure_season(app)
        )
        preset = (
            db.session.query(RacePreset).first()
            or _ensure_preset(app)
        )
        for p in placements:
            race = OfficialRace(
                season_id=season.id,
                organizer_user_id=user_id,
                name=f"Race {char_id}-{p}",
                preset_id=preset.id,
                status=OfficialRaceStatus.COMPLETED,
            )
            db.session.add(race)
            db.session.flush()
            db.session.add(
                OfficialRaceResult(
                    official_race_id=race.id,
                    user_id=user_id,
                    uma_character_id=char_id,
                    placement=p,
                    uma_name="x",
                )
            )
        db.session.commit()


def _seed_draft_results(
    app: Flask, user_id: int, char_id: int, placements: list[int]
) -> None:
    with app.app_context():
        season = (
            db.session.query(Season).first()
            or _ensure_season(app)
        )
        for i, p in enumerate(placements):
            match = DraftMatch(
                season_id=season.id,
                host_user_id=user_id,
                join_code=f"DRAFT{char_id}{i:02d}AB",
                umas_per_player=2,
                preset_pool="custom",
                status=DraftMatchStatus.COMPLETED,
            )
            db.session.add(match)
            db.session.flush()
            db.session.add(
                DraftRaceResult(
                    draft_match_id=match.id,
                    user_id=user_id,
                    uma_character_id=char_id,
                    placement=p,
                )
            )
        db.session.commit()


def _ensure_season(app: Flask) -> Season:
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


def _ensure_preset(app: Flask) -> RacePreset:
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


# ---------- service-level ----------


def test_official_returns_top_by_race_count(app: Flask, make_user) -> None:
    user = make_user(username="alice", role=Role.USER)
    char_a, char_b, char_c = _make_chars(app, 3)
    # char_a: 5 races (top), char_b: 3 races, char_c: 1 race (filtered out by min_races=3)
    _seed_official_results(app, user["id"], char_a, [1, 1, 2, 3, 5])
    _seed_official_results(app, user["id"], char_b, [2, 3, 4])
    _seed_official_results(app, user["id"], char_c, [1])
    with app.app_context():
        rows = profiles_service.most_used_umas_for_user(
            user["id"], kind="official"
        )
        assert [r.character_id for r in rows] == [char_a, char_b]
        assert rows[0].races == 5
        assert rows[0].wins == 2
        assert rows[0].podiums == 4  # placements 1,1,2,3 ≤ 3
        assert rows[0].avg_placement == 2.4
        assert rows[0].win_rate == 0.4
        assert rows[0].podium_rate == 0.8


def test_draft_returns_top_by_match_count(app: Flask, make_user) -> None:
    user = make_user(username="bob", role=Role.USER)
    char_a, _ = _make_chars(app, 2)
    _seed_draft_results(app, user["id"], char_a, [1, 2, 1])
    with app.app_context():
        rows = profiles_service.most_used_umas_for_user(
            user["id"], kind="draft"
        )
        assert len(rows) == 1
        assert rows[0].races == 3
        assert rows[0].wins == 2
        assert rows[0].podiums == 3  # all ≤ 3 placements
        assert rows[0].name == "Char 0"
        assert rows[0].image_url == "http://img/0"


def test_min_races_guard(app: Flask, make_user) -> None:
    user = make_user(username="solo", role=Role.USER)
    [char_a] = _make_chars(app, 1)
    _seed_official_results(app, user["id"], char_a, [1, 2])  # below default 3
    with app.app_context():
        assert profiles_service.most_used_umas_for_user(
            user["id"], kind="official"
        ) == []
        # Lower the guard and the row appears.
        rows = profiles_service.most_used_umas_for_user(
            user["id"], kind="official", min_races=2
        )
        assert len(rows) == 1


def test_skips_results_without_character_id(app: Flask, make_user) -> None:
    """Custom-name results (no uma_character_id) shouldn't pollute the
    aggregation — without a stable id we'd be collapsing typos as one
    'Uma' or splitting the same Uma across rows."""
    user = make_user(username="custom", role=Role.USER)
    [char_a] = _make_chars(app, 1)
    _seed_official_results(app, user["id"], char_a, [1, 2, 3])

    with app.app_context():
        # Append three custom-name (no character id) rows.
        season = db.session.query(Season).first()
        preset = db.session.query(RacePreset).first()
        for i in range(3):
            race = OfficialRace(
                season_id=season.id,
                organizer_user_id=user["id"],
                name=f"Custom-{i}",
                preset_id=preset.id,
                status=OfficialRaceStatus.COMPLETED,
            )
            db.session.add(race)
            db.session.flush()
            db.session.add(
                OfficialRaceResult(
                    official_race_id=race.id,
                    user_id=user["id"],
                    uma_character_id=None,
                    uma_name="freetext",
                    placement=4,
                )
            )
        db.session.commit()
        rows = profiles_service.most_used_umas_for_user(
            user["id"], kind="official"
        )
        assert len(rows) == 1
        assert rows[0].character_id == char_a
        assert rows[0].races == 3  # custom-name rows excluded


def test_unknown_kind_returns_empty(app: Flask, make_user) -> None:
    user = make_user(username="x", role=Role.USER)
    with app.app_context():
        assert profiles_service.most_used_umas_for_user(
            user["id"], kind="garbage"
        ) == []


# ---------- HTTP rendering ----------


def test_public_profile_renders_most_used_official(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="alice", role=Role.USER)
    [char_a] = _make_chars(app, 1)
    _seed_official_results(app, user["id"], char_a, [1, 2, 3, 1])
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Most used · Official" in body
    assert "Char 0" in body
    assert "4 races" in body
    # 50% win rate (2/4) + 100% podium (4/4 ≤ 3).
    assert "50%</span> WR" in body
    assert "100%</span> podium" in body


def test_public_profile_hides_card_when_no_data(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """No results → no card in the layout (the placeholder approach
    from Stage 1 is intentionally not used here)."""
    make_user(username="empty", role=Role.USER)
    resp = client.get("/profiles/empty")
    body = resp.data.decode()
    assert "Most used · Official" not in body
    assert "Most used · Draft" not in body
