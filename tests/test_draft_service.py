from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftBanType,
    DraftEloChange,
    DraftMatch,
    DraftMatchStatus,
    RacePreset,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import draft as draft_service
from uma_ladder.services.auth import RegistrationRequest, register_user
from uma_ladder.services.elo import DEFAULT_RATING


def _season() -> Season:
    now = datetime.now(UTC)
    s = Season(
        name="S1",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=89),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    return s


def _user(name: str) -> int:
    u = register_user(RegistrationRequest(username=name, password="password123"))
    return u.id


def _preset(**overrides) -> RacePreset:
    defaults = dict(
        source=PresetSource.CUSTOM_BUILTIN,
        name="Tokyo Turf 2000m (Medium) Left",
        venue="Tokyo",
        surface="Turf",
        distance_meters=2000,
        distance_category="Medium",
        direction="Left",
        course_variant=None,
        max_runners=18,
        enabled=True,
    )
    defaults.update(overrides)
    p = RacePreset(**defaults)
    db.session.add(p)
    db.session.commit()
    return p


def test_create_match_starts_waiting(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        assert match.status == DraftMatchStatus.WAITING_FOR_OPPONENT
        assert len(match.join_code) == draft_service.JOIN_CODE_LEN


def test_create_match_rejects_bad_uma_count(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        with pytest.raises(draft_service.InvalidUmaCountError):
            draft_service.create_match(
                draft_service.CreateMatchRequest(
                    season_id=s.id,
                    host_user_id=host,
                    umas_per_player=4,
                    preset_pool="custom",
                )
            )


def test_cannot_join_own_match(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        with pytest.raises(draft_service.CannotJoinOwnMatchError):
            draft_service.join_match(match.id, host)


def test_join_advances_to_submitting(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        match = draft_service.join_match(match.id, opp)
        assert match.status == DraftMatchStatus.SUBMITTING_UMAS
        assert match.opponent_user_id == opp


def test_join_full_match_rejected(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        third = _user("carol")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        draft_service.join_match(match.id, opp)
        with pytest.raises(draft_service.InvalidMatchStateError):
            draft_service.join_match(match.id, third)


def test_submit_umas_count_must_match(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        draft_service.join_match(match.id, opp)
        with pytest.raises(draft_service.InvalidUmaCountError):
            draft_service.submit_umas(
                match.id, host, [draft_service.UmaSubmission(custom_uma_name="A")]
            )


def test_ready_check_advances_when_both_locked(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        draft_service.join_match(match.id, opp)
        for uid in (host, opp):
            draft_service.submit_umas(
                match.id,
                uid,
                [
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-1"),
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-2"),
                ],
            )
        # first ready → READY_CHECK
        match = draft_service.ready_up(match.id, host)
        assert match.status == DraftMatchStatus.READY_CHECK
        # second ready → BAN_PHASE
        match = draft_service.ready_up(match.id, opp)
        assert match.status == DraftMatchStatus.BAN_PHASE


def test_ban_rejects_self_uma(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        draft_service.join_match(match.id, opp)
        for uid in (host, opp):
            draft_service.submit_umas(
                match.id,
                uid,
                [
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-1"),
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-2"),
                ],
            )
        for uid in (host, opp):
            draft_service.ready_up(match.id, uid)

        host_entries = [e for e in draft_service.list_uma_entries(match.id) if e.user_id == host]
        with pytest.raises(draft_service.UnknownBanTargetError):
            draft_service.submit_bans(
                match.id,
                host,
                banned_uma_entry_id=host_entries[0].id,
                track_ban_type=DraftBanType.VENUE,
                track_condition_key="Tokyo",
            )


def test_full_happy_path_with_elo(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        # one preset that survives bans
        keeper = _preset(name="Keeper", venue="Sapporo", direction="Right")
        # one that gets banned
        _preset(name="Banned", venue="Tokyo", direction="Left")

        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        draft_service.join_match(match.id, opp)
        for uid in (host, opp):
            draft_service.submit_umas(
                match.id,
                uid,
                [
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-1"),
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-2"),
                ],
            )
        for uid in (host, opp):
            draft_service.ready_up(match.id, uid)

        entries = draft_service.list_uma_entries(match.id)
        host_entries = [e for e in entries if e.user_id == host]
        opp_entries = [e for e in entries if e.user_id == opp]

        # host bans an opp Uma + Tokyo venue
        draft_service.submit_bans(
            match.id, host,
            banned_uma_entry_id=opp_entries[0].id,
            track_ban_type=DraftBanType.VENUE,
            track_condition_key="Tokyo",
        )
        # opp bans a host Uma + Left direction
        draft_service.submit_bans(
            match.id, opp,
            banned_uma_entry_id=host_entries[0].id,
            track_ban_type=DraftBanType.DIRECTION,
            track_condition_key="Left",
        )

        match = draft_service.randomize_preset(match.id, rng=random.Random(42))
        assert match.status == DraftMatchStatus.ROOM_CODE_PENDING
        assert match.selected_preset_id == keeper.id

        draft_service.set_room_code(match.id, "ROOM-1")
        match = draft_service.submit_results(
            match.id,
            [
                draft_service.DraftResultLine(user_id=host, placement=1),
                draft_service.DraftResultLine(user_id=opp, placement=2),
            ],
            confirmed_by_user_id=host,
        )
        assert match.status == DraftMatchStatus.COMPLETED
        assert match.winner_user_id == host

        host_rating = draft_service.current_rating(host, s.id)
        opp_rating = draft_service.current_rating(opp, s.id)
        assert host_rating == DEFAULT_RATING + 16
        assert opp_rating == DEFAULT_RATING - 16

        changes = db.session.query(DraftEloChange).all()
        assert len(changes) == 2


def test_randomization_failure_marks_match(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        # only one preset, both players ban its venue
        _preset(name="Only", venue="Tokyo", direction="Left")

        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        draft_service.join_match(match.id, opp)
        for uid in (host, opp):
            draft_service.submit_umas(
                match.id,
                uid,
                [
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-1"),
                    draft_service.UmaSubmission(custom_uma_name=f"U{uid}-2"),
                ],
            )
        for uid in (host, opp):
            draft_service.ready_up(match.id, uid)

        entries = draft_service.list_uma_entries(match.id)
        host_entries = [e for e in entries if e.user_id == host]
        opp_entries = [e for e in entries if e.user_id == opp]
        draft_service.submit_bans(
            match.id, host,
            banned_uma_entry_id=opp_entries[0].id,
            track_ban_type=DraftBanType.VENUE,
            track_condition_key="Tokyo",
        )
        draft_service.submit_bans(
            match.id, opp,
            banned_uma_entry_id=host_entries[0].id,
            track_ban_type=DraftBanType.DIRECTION,
            track_condition_key="Left",
        )
        from uma_ladder.services.randomizer import RandomizerError

        with pytest.raises(RandomizerError):
            draft_service.randomize_preset(match.id)
        # Must reload after the rolled-back exception path
        match = draft_service.get_match(match.id)
        assert match.status == DraftMatchStatus.RANDOMIZATION_FAILED


def test_room_code_24h_expiry(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        draft_service.join_match(match.id, opp)
        # Drive state forward enough to allow set_room_code: ban_phase → randomize
        # is a longer route; for the expiry boundary we cheat by setting status directly.
        m = db.session.get(DraftMatch, match.id)
        m.status = DraftMatchStatus.ROOM_CODE_PENDING
        db.session.commit()

        issued_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        draft_service.set_room_code(match.id, "ABC1", now=issued_at)
        m = db.session.get(DraftMatch, match.id)
        assert draft_service.is_room_code_expired(
            m, now=issued_at + timedelta(hours=23, minutes=59)
        ) is False
        assert draft_service.is_room_code_expired(
            m, now=issued_at + timedelta(hours=24)
        ) is True


def test_elo_ladder_orders_by_rating(app: Flask) -> None:
    with app.app_context():
        s = _season()
        a = _user("alice")
        b = _user("bob")
        c = _user("carol")
        # Insert deterministic Elo history.
        for user_id, rating, outcome in [
            (a, 1100, 1.0),
            (b, 950, 0.0),
            (c, 1010, 1.0),
        ]:
            db.session.add(
                DraftEloChange(
                    draft_match_id=1,  # FK constrained — we'll bypass with a fake match below
                    season_id=s.id,
                    user_id=user_id,
                    opponent_user_id=user_id,  # placeholder
                    rating_before=1000,
                    rating_after=rating,
                    delta=rating - 1000,
                    outcome=outcome,
                )
            )
        # FK to draft_matches.id needs a real row
        host = a
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id, host_user_id=host, umas_per_player=2, preset_pool="custom"
            )
        )
        # rebind change rows to the real match id
        db.session.query(DraftEloChange).update({"draft_match_id": match.id})
        db.session.commit()

        rows = draft_service.season_elo_ladder(s.id)
        # carol 1010, bob 950, alice 1100 → sorted desc: alice, carol, bob
        assert [(r.username, r.rating) for r in rows] == [
            ("alice", 1100),
            ("carol", 1010),
            ("bob", 950),
        ]
