"""Direction is a binary axis for game-aptitude purposes — even when
'Stretch'/'Straight' presets exist, the second player cannot also ban a
direction once the first has."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftBanType,
    RacePreset,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import draft as draft_service


def _setup_match(app: Flask):
    with app.app_context():
        from uma_ladder.services.auth import RegistrationRequest, register_user

        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        host = register_user(
            RegistrationRequest(username="alice", password="password123")
        ).id
        opp = register_user(
            RegistrationRequest(username="bob", password="password123")
        ).id
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host,
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        draft_service.join_match(match.id, opp)
        draft_service.ready_up(match.id, host)
        draft_service.ready_up(match.id, opp)
        return match.id, host, opp


def _preset(**kw) -> RacePreset:
    base = dict(
        source=PresetSource.CUSTOM_BUILTIN,
        name="P",
        venue="Tokyo",
        surface="Turf",
        distance_meters=2000,
        distance_category="Medium",
        direction="Left",
        course_variant=None,
        max_runners=18,
        enabled=True,
    )
    base.update(kw)
    p = RacePreset(**base)
    db.session.add(p)
    db.session.commit()
    return p


def _seed_with_stretch_pool() -> None:
    """Pool with Left, Right, AND Stretch presets — without the binary
    rule, banning Right on top of Left would still leave Stretch presets,
    so the empty-pool feasibility check would pass."""
    _preset(name="L", venue="Tokyo", direction="Left")
    _preset(name="R", venue="Sapporo", direction="Right")
    _preset(name="S", venue="Niigata", direction="Stretch", distance_meters=1000)
    _preset(name="ST", venue="Hakodate", direction="Straight", distance_meters=1000)


def test_direction_dropdown_empty_after_opponent_direction_ban(app: Flask) -> None:
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _seed_with_stretch_pool()
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.DIRECTION, condition_key="Left"
        )
        opts = draft_service.feasible_track_ban_options(
            match_id,
            opp,
            static_options={"direction": ["Left", "Right"], "venue": ["Tokyo", "Sapporo"]},
        )
        # Direction list must be empty — even though Right would technically
        # leave Stretch/Straight presets in the pool.
        assert opts["direction"] == []
        # Other categories aren't affected.
        assert "Sapporo" in opts["venue"] or "Tokyo" in opts["venue"]


def test_submit_rejects_second_direction_ban_even_when_pool_survives(app: Flask) -> None:
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _seed_with_stretch_pool()
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.DIRECTION, condition_key="Left"
        )
        with pytest.raises(draft_service.BanWouldEmptyPoolError) as exc:
            draft_service.submit_track_ban(
                match_id, opp, ban_type=DraftBanType.DIRECTION, condition_key="Right"
            )
        assert "direction" in str(exc.value).lower()


def test_first_direction_ban_still_allowed(app: Flask) -> None:
    """Sanity: a single direction ban is still allowed."""
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _seed_with_stretch_pool()
        # No opponent ban yet.
        opts = draft_service.feasible_track_ban_options(
            match_id, host, static_options={"direction": ["Left", "Right"]}
        )
        assert sorted(opts["direction"]) == ["Left", "Right"]
        ban = draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.DIRECTION, condition_key="Left"
        )
        assert ban.condition_key == "Left"


def test_skip_ban_does_not_lock_out_direction(app: Flask) -> None:
    """The __skip__ placeholder must not count as a direction ban."""
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _seed_with_stretch_pool()
        # Host skips with the venue placeholder.
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.VENUE, condition_key="__skip__"
        )
        opts = draft_service.feasible_track_ban_options(
            match_id, opp, static_options={"direction": ["Left", "Right"]}
        )
        # Opp can still pick a direction.
        assert sorted(opts["direction"]) == ["Left", "Right"]
