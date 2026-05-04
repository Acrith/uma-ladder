"""Track-ban feasibility:

- The dropdown must filter out values that, applied on top of the
  opponent's existing ban, would leave the pool empty (direct conflicts
  like Direction=Left vs Right, and cross-category dependencies like
  banning Turf eliminating Long).
- submit_track_ban must reject empty-pool bans server-side as a
  defence against concurrent submissions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftBanType,
    DraftMatchBan,
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


# ---------- direct-conflict (Left vs Right) ----------


def test_feasibility_filters_opposite_direction(app: Flask) -> None:
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        # Pool: one Left, one Right preset.
        _preset(name="Left only", venue="Tokyo", direction="Left")
        _preset(name="Right only", venue="Sapporo", direction="Right")

        # Host bans Direction=Left.
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.DIRECTION, condition_key="Left"
        )

        # When opponent's dropdown is computed, Direction=Right must be
        # filtered out (banning it on top of host's Left ban empties the pool).
        opts = draft_service.feasible_track_ban_options(
            match_id,
            opp,
            static_options={"direction": ["Left", "Right"]},
        )
        assert "Right" not in opts["direction"]


def test_submit_rejects_pool_emptying_direction_combo(app: Flask) -> None:
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _preset(name="Left only", venue="Tokyo", direction="Left")
        _preset(name="Right only", venue="Sapporo", direction="Right")
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.DIRECTION, condition_key="Left"
        )
        with pytest.raises(draft_service.BanWouldEmptyPoolError):
            draft_service.submit_track_ban(
                match_id, opp, ban_type=DraftBanType.DIRECTION, condition_key="Right"
            )


# ---------- cross-category (Turf banned → Long becomes infeasible) ----------


def _seed_no_long_dirt_pool() -> None:
    """Pool with Long only on Turf; Dirt has Sprint/Mile/Medium but no Long."""
    _preset(name="Long Turf", venue="Tokyo", surface="Turf", direction="Left", distance_category="Long", distance_meters=3000)
    _preset(name="Mile Turf", venue="Sapporo", surface="Turf", direction="Right", distance_category="Mile", distance_meters=1600)
    _preset(name="Sprint Dirt", venue="Hakodate", surface="Dirt", direction="Right", distance_category="Sprint", distance_meters=1200)
    _preset(name="Mile Dirt", venue="Niigata", surface="Dirt", direction="Left", distance_category="Mile", distance_meters=1600)
    _preset(name="Medium Dirt", venue="Fukushima", surface="Dirt", direction="Right", distance_category="Medium", distance_meters=2000)


def test_feasibility_filters_long_when_turf_banned(app: Flask) -> None:
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _seed_no_long_dirt_pool()
        # Host bans Surface=Turf. After this, only Dirt presets remain
        # (Sprint, Mile, Medium) — no Long preset survives.
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.SURFACE, condition_key="Turf"
        )
        opts = draft_service.feasible_track_ban_options(
            match_id,
            opp,
            static_options={
                "distance_category": ["Sprint", "Mile", "Medium", "Long"]
            },
        )
        # Long is redundant (no Long preset left to ban anyway).
        assert "Long" not in opts["distance_category"]
        # Sprint / Mile / Medium each match one preset, so banning them
        # removes one preset and leaves two → kept.
        assert "Sprint" in opts["distance_category"]
        assert "Mile" in opts["distance_category"]
        assert "Medium" in opts["distance_category"]


def test_submit_rejects_redundant_distance_after_surface_ban(app: Flask) -> None:
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _seed_no_long_dirt_pool()
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.SURFACE, condition_key="Turf"
        )
        with pytest.raises(draft_service.BanWouldEmptyPoolError):
            draft_service.submit_track_ban(
                match_id,
                opp,
                ban_type=DraftBanType.DISTANCE_CATEGORY,
                condition_key="Long",
            )


# ---------- edges ----------


def test_skip_ban_bypasses_feasibility(app: Flask) -> None:
    """The __skip__ placeholder must not be rejected by the feasibility
    check — skipping doesn't ban anything for real."""
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _preset(name="Only", venue="Tokyo", direction="Left")
        # Even with no presets surviving "Tokyo" ban, skip is fine.
        draft_service.submit_track_ban(
            match_id, host, ban_type=DraftBanType.VENUE, condition_key="__skip__"
        )
        # Sanity: row was inserted and ignored as a real ban.
        rows = (
            db.session.query(DraftMatchBan)
            .filter_by(draft_match_id=match_id, user_id=host)
            .all()
        )
        assert len(rows) == 1


def test_solo_ban_still_rejected_when_alone_empties(app: Flask) -> None:
    """Even without an opponent ban, a single ban that empties the pool
    is rejected — e.g. trying to ban the only venue in the pool."""
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _preset(name="Only", venue="Tokyo", direction="Left")
        with pytest.raises(draft_service.BanWouldEmptyPoolError):
            draft_service.submit_track_ban(
                match_id,
                host,
                ban_type=DraftBanType.VENUE,
                condition_key="Tokyo",
            )


def test_feasibility_unaffected_by_own_pending_ban(app: Flask) -> None:
    """The feasibility check uses the OPPONENT'S bans, not the user's own
    pending ones (which should be impossible — submit_track_ban dedupes —
    but the helper guards against it)."""
    match_id, host, opp = _setup_match(app)
    with app.app_context():
        _preset(name="Left only", venue="Tokyo", direction="Left")
        _preset(name="Right only", venue="Sapporo", direction="Right")
        # Insert a stale row for `host` directly to simulate weird state.
        db.session.add(
            DraftMatchBan(
                draft_match_id=match_id,
                user_id=host,
                ban_type=DraftBanType.DIRECTION,
                condition_key="Left",
                locked_at=datetime.now(UTC),
            )
        )
        db.session.commit()
        # Host's own dropdown still has both Left and Right.
        opts = draft_service.feasible_track_ban_options(
            match_id, host, static_options={"direction": ["Left", "Right"]}
        )
        assert sorted(opts["direction"]) == ["Left", "Right"]
