"""PR-G3: Race-day conditions (RaceSeason / Weather / GroundCondition).

The helper centralises the Snowy/Winter constraint and the random
roll. Three model surfaces use it:

- ChampionsMeeting (admin-set via /admin/cm)
- OfficialRace (organizer-set on /official/new)
- DraftMatch (auto-rolled when randomize_preset locks the preset)
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    ChampionsMeeting,
    DraftMatch,
    DraftMatchStatus,
    GroundCondition,
    OfficialRace,
    PresetSource,
    RacePreset,
    RaceSeason,
    Role,
    Season,
    SeasonStatus,
    Weather,
)
from uma_ladder.services import cm as cm_service
from uma_ladder.services import draft as draft_service
from uma_ladder.services import official as official_service
from uma_ladder.services import track_conditions as tc

# ---------- helper: ensure a Tokyo G1 preset exists ----------


def _ensure_preset() -> RacePreset:
    p = (
        db.session.query(RacePreset)
        .filter_by(name="Tokyo Yushun", venue="Tokyo")
        .first()
    )
    if p:
        return p
    p = RacePreset(
        source=PresetSource.G1_IMPORT,
        name="Tokyo Yushun",
        grade="G1",
        venue="Tokyo",
        surface="Turf",
        distance_meters=2400,
        distance_category="Medium",
        direction="Left",
        course_variant=None,
        max_runners=18,
        enabled=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _ensure_season() -> Season:
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


# ---------- normalize() ----------


def test_normalize_passes_known_values_through(app: Flask) -> None:
    season, w, g = tc.normalize(
        race_season="Spring", weather="Sunny", ground_condition="Firm"
    )
    assert season == "Spring"
    assert w == "Sunny"
    assert g == "Firm"


def test_normalize_blanks_to_none() -> None:
    out = tc.normalize(race_season="", weather=None, ground_condition="  ")
    assert out == (None, None, None)


def test_normalize_rejects_unknown_value() -> None:
    with pytest.raises(tc.TrackConditionError) as exc:
        tc.normalize(race_season="Mars", weather=None, ground_condition=None)
    assert "race_season" in str(exc.value)


def test_normalize_rejects_snowy_outside_winter() -> None:
    with pytest.raises(tc.TrackConditionError) as exc:
        tc.normalize(
            race_season="Summer", weather="Snowy", ground_condition=None
        )
    assert "Winter" in str(exc.value)


def test_normalize_allows_snowy_in_winter() -> None:
    out = tc.normalize(
        race_season="Winter", weather="Snowy", ground_condition="Heavy"
    )
    assert out == ("Winter", "Snowy", "Heavy")


def test_normalize_allows_snowy_when_season_blank() -> None:
    """A partially filled form (weather chosen but season pending) must
    not error — the user is mid-edit. Validation kicks in only when
    both fields are set."""
    out = tc.normalize(
        race_season=None, weather="Snowy", ground_condition=None
    )
    assert out == (None, "Snowy", None)


# ---------- roll_random() ----------


def test_roll_random_never_yields_snowy_outside_winter() -> None:
    """Probabilistic — exhaustive over 1000 rolls. With seed=0 the
    sequence is deterministic so a regression in this constraint
    would always fail at the same iteration."""
    rng = random.Random(0)
    for _ in range(1000):
        season, weather, ground = tc.roll_random(rng=rng)
        if weather == "Snowy":
            assert season == "Winter"
        # Sanity: every value is from the right enum.
        assert season in {s.value for s in RaceSeason}
        assert weather in {w.value for w in Weather}
        assert ground in {g.value for g in GroundCondition}


def test_roll_random_can_produce_snowy_in_winter() -> None:
    """Make sure the constraint isn't *over*-constraining — Winter
    Snowy is reachable. With ~6.25% probability per roll we'd expect
    to hit it within ~50 tries; cap at 500 for safety."""
    rng = random.Random(1)
    for _ in range(500):
        season, weather, _ = tc.roll_random(rng=rng)
        if season == "Winter" and weather == "Snowy":
            return
    pytest.fail("Winter+Snowy not produced in 500 tries — RNG path broken")


def test_roll_random_respects_season_bans() -> None:
    """Banning Winter forces Snowy off the table too."""
    rng = random.Random(2)
    for _ in range(200):
        season, weather, _ = tc.roll_random(
            rng=rng, forbidden_seasons={"Winter"}
        )
        assert season != "Winter"
        assert weather != "Snowy"


def test_roll_random_raises_when_pool_emptied() -> None:
    rng = random.Random(3)
    with pytest.raises(tc.TrackConditionError):
        tc.roll_random(
            rng=rng, forbidden_seasons={"Spring", "Summer", "Autumn", "Winter"}
        )


# ---------- CM service wiring ----------


def test_cm_create_persists_conditions(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        cm = cm_service.create_cm(
            cm_service.CmInput(
                name="Snowy CM",
                starts_on=date(2026, 12, 15),
                ends_on=None,
                preset_id=preset.id,
                race_season="Winter",
                weather="Snowy",
                ground_condition="Heavy",
            ),
            by_user_id=actor["id"],
        )
        assert cm.race_season == "Winter"
        assert cm.weather == "Snowy"
        assert cm.ground_condition == "Heavy"


def test_cm_create_rejects_snowy_outside_winter(
    app: Flask, make_user
) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        with pytest.raises(tc.TrackConditionError):
            cm_service.create_cm(
                cm_service.CmInput(
                    name="Bad",
                    starts_on=date(2026, 5, 1),
                    ends_on=None,
                    preset_id=preset.id,
                    race_season="Spring",
                    weather="Snowy",
                ),
                by_user_id=actor["id"],
            )


def test_cm_update_replaces_conditions(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        preset = _ensure_preset()
        cm = cm_service.create_cm(
            cm_service.CmInput(
                name="Edit",
                starts_on=date(2026, 6, 1),
                ends_on=None,
                preset_id=preset.id,
                race_season="Spring",
                weather="Sunny",
                ground_condition="Firm",
            ),
            by_user_id=actor["id"],
        )
        cm_service.update_cm(
            cm.id,
            cm_service.CmInput(
                name="Edit",
                starts_on=date(2026, 6, 1),
                ends_on=None,
                preset_id=preset.id,
                race_season="Autumn",
                weather="Rainy",
                ground_condition="Soft",
            ),
            by_user_id=actor["id"],
        )
        refreshed = db.session.get(ChampionsMeeting, cm.id)
        assert refreshed.race_season == "Autumn"
        assert refreshed.weather == "Rainy"
        assert refreshed.ground_condition == "Soft"


# ---------- Official race service wiring ----------


def test_official_create_race_persists_conditions(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context():
        s = _ensure_season()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id,
                name="Test",
                organizer_user_id=org["id"],
                race_season="Summer",
                weather="Cloudy",
                ground_condition="Good",
            )
        )
        refreshed = db.session.get(OfficialRace, race.id)
        assert refreshed.race_season == "Summer"
        assert refreshed.weather == "Cloudy"
        assert refreshed.ground_condition == "Good"


def test_official_create_race_rejects_snowy_outside_winter(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context():
        s = _ensure_season()
        with pytest.raises(tc.TrackConditionError):
            official_service.create_race(
                official_service.CreateRaceRequest(
                    season_id=s.id,
                    name="Bad",
                    organizer_user_id=org["id"],
                    race_season="Spring",
                    weather="Snowy",
                )
            )


# ---------- Draft auto-roll ----------


def test_randomize_preset_rolls_track_conditions(
    app: Flask, make_user
) -> None:
    """When a draft match locks its preset, conditions should be set
    too — and the Snowy/Winter constraint must hold for whatever the
    RNG produces."""
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        s = _ensure_season()
        _ensure_preset()
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="g1",
            )
        )
        match.opponent_user_id = opp["id"]
        match.status = DraftMatchStatus.TRACK_BAN_PHASE
        db.session.commit()

        # Stub out the both-track-banned guard so the test can call
        # randomize_preset directly without going through the ban flow.
        from uma_ladder.services import draft as draft_module

        original = draft_module.both_players_track_banned
        draft_module.both_players_track_banned = lambda _mid: True
        try:
            rolled = draft_service.randomize_preset(
                match.id, rng=random.Random(7)
            )
        finally:
            draft_module.both_players_track_banned = original

        refreshed = db.session.get(DraftMatch, rolled.id)
        assert refreshed.race_season is not None
        assert refreshed.weather is not None
        assert refreshed.ground_condition is not None
        if refreshed.weather == "Snowy":
            assert refreshed.race_season == "Winter"
