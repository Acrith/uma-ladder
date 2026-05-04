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
    UmaCharacter,
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


def _character(slug: str, name: str | None = None) -> UmaCharacter:
    c = UmaCharacter(slug=slug, name_en=name or slug.replace("-", " ").title())
    db.session.add(c)
    db.session.commit()
    return c


def _match_with_two(host: int, opp: int, *, season_id: int, umas_per_player: int = 2) -> int:
    m = draft_service.create_match(
        draft_service.CreateMatchRequest(
            season_id=season_id,
            host_user_id=host,
            umas_per_player=umas_per_player,
            preset_pool="custom",
        )
    )
    draft_service.join_match(m.id, opp)
    return m.id


def _drive_to_track_ban(match_id: int, host: int, opp: int) -> None:
    draft_service.ready_up(match_id, host)
    draft_service.ready_up(match_id, opp)


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


def test_create_match_rejects_bad_uma_count(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        with pytest.raises(draft_service.InvalidUmaCountError):
            draft_service.create_match(
                draft_service.CreateMatchRequest(
                    season_id=s.id, host_user_id=host, umas_per_player=4, preset_pool="custom"
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


def test_join_advances_to_ready_check(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        m = draft_service.get_match(m_id)
        assert m.status == DraftMatchStatus.READY_CHECK


def test_ready_up_advances_when_both_ready(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        m = draft_service.ready_up(m_id, host)
        assert m.status == DraftMatchStatus.READY_CHECK
        assert m.host_ready and not m.opponent_ready
        m = draft_service.ready_up(m_id, opp)
        assert m.status == DraftMatchStatus.TRACK_BAN_PHASE


def test_track_ban_rejects_uma_type(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        with pytest.raises(draft_service.UnknownBanTargetError):
            draft_service.submit_track_ban(
                m_id, host, ban_type=DraftBanType.UMA, condition_key="x"
            )


def test_track_ban_dedupes_per_user(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        draft_service.submit_track_ban(
            m_id, host, ban_type=DraftBanType.VENUE, condition_key="Tokyo"
        )
        with pytest.raises(draft_service.DuplicateBanError):
            draft_service.submit_track_ban(
                m_id, host, ban_type=DraftBanType.DIRECTION, condition_key="Left"
            )


def test_randomize_requires_both_track_bans(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        _preset(name="A")
        draft_service.submit_track_ban(
            m_id, host, ban_type=DraftBanType.VENUE, condition_key="Sapporo"
        )
        with pytest.raises(draft_service.InvalidMatchStateError):
            draft_service.randomize_preset(m_id)


def test_randomize_advances_to_uma_ban(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        keeper = _preset(name="Keeper", venue="Sapporo")
        _preset(name="Banned", venue="Tokyo")
        draft_service.submit_track_ban(
            m_id, host, ban_type=DraftBanType.VENUE, condition_key="Tokyo"
        )
        draft_service.submit_track_ban(
            m_id, opp, ban_type=DraftBanType.DIRECTION, condition_key="Right"
        )
        m = draft_service.randomize_preset(m_id, rng=random.Random(1))
        # only Keeper survives the venue=Tokyo + direction=Right bans
        # … wait, Keeper venue=Sapporo direction=Left default; check test sanity:
        # The default preset uses direction=Left, so direction=Right ban excludes nothing.
        # Banning venue=Tokyo eliminates "Banned". Keeper remains.
        assert m.status == DraftMatchStatus.UMA_BAN_PHASE
        assert m.selected_preset_id == keeper.id


def test_randomize_min_max_runners_for_3v3(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        # umas_per_player=3 → min_max_runners=6
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=3)
        _drive_to_track_ban(m_id, host, opp)
        small = _preset(name="Tiny", venue="Sapporo", direction="Left", max_runners=4)  # noqa: F841
        big = _preset(name="Big", venue="Hakodate", direction="Left", max_runners=18)
        draft_service.submit_track_ban(
            m_id, host, ban_type=DraftBanType.VENUE, condition_key="__skip__"
        )
        draft_service.submit_track_ban(
            m_id, opp, ban_type=DraftBanType.VENUE, condition_key="__skip__"
        )
        m = draft_service.randomize_preset(m_id, rng=random.Random(0))
        assert m.selected_preset_id == big.id


def test_uma_ban_rejects_unknown_character(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        _preset(name="P")
        for uid in (host, opp):
            draft_service.submit_track_ban(
                m_id, uid, ban_type=DraftBanType.VENUE, condition_key="__skip__"
            )
        draft_service.randomize_preset(m_id)
        with pytest.raises(draft_service.UnknownBanTargetError):
            draft_service.submit_uma_ban(m_id, host, 999)


def test_uma_ban_advances_to_room_code_pending(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        _preset(name="P")
        for uid in (host, opp):
            draft_service.submit_track_ban(
                m_id, uid, ban_type=DraftBanType.VENUE, condition_key="__skip__"
            )
        draft_service.randomize_preset(m_id)
        c1 = _character("special-week")
        c2 = _character("gold-ship")
        draft_service.submit_uma_ban(m_id, host, c1.id)
        draft_service.submit_uma_ban(m_id, opp, c2.id)
        m = draft_service.get_match(m_id)
        assert m.status == DraftMatchStatus.ROOM_CODE_PENDING


def test_full_happy_path_with_elo(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        _preset(name="Keeper", venue="Sapporo")
        _preset(name="Banned", venue="Tokyo")
        draft_service.submit_track_ban(
            m_id, host, ban_type=DraftBanType.VENUE, condition_key="Tokyo"
        )
        draft_service.submit_track_ban(
            m_id, opp, ban_type=DraftBanType.VENUE, condition_key="__skip__"
        )
        draft_service.randomize_preset(m_id, rng=random.Random(1))
        c_banned_by_host = _character("banned-by-host")
        c_banned_by_opp = _character("banned-by-opp")
        c_used = _character("used-uma")
        draft_service.submit_uma_ban(m_id, host, c_banned_by_host.id)
        draft_service.submit_uma_ban(m_id, opp, c_banned_by_opp.id)

        draft_service.set_room_code(m_id, "RC-1")
        m = draft_service.submit_results(
            m_id,
            [
                draft_service.DraftResultLine(
                    user_id=host, placement=1, uma_character_id=c_used.id
                ),
                draft_service.DraftResultLine(
                    user_id=opp, placement=2, custom_uma_name="Some Custom"
                ),
            ],
            confirmed_by_user_id=host,
        )
        assert m.status == DraftMatchStatus.COMPLETED
        assert m.winner_user_id == host
        assert draft_service.current_rating(host, s.id) == DEFAULT_RATING + 16
        assert draft_service.current_rating(opp, s.id) == DEFAULT_RATING - 16
        assert db.session.query(DraftEloChange).count() == 2


def test_results_reject_banned_character(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        _preset(name="P")
        for uid in (host, opp):
            draft_service.submit_track_ban(
                m_id, uid, ban_type=DraftBanType.VENUE, condition_key="__skip__"
            )
        draft_service.randomize_preset(m_id)
        forbidden = _character("forbidden")
        whatever = _character("whatever")
        draft_service.submit_uma_ban(m_id, host, forbidden.id)
        draft_service.submit_uma_ban(m_id, opp, whatever.id)
        draft_service.set_room_code(m_id, "RC-1")
        with pytest.raises(draft_service.BannedCharacterUsedError):
            draft_service.submit_results(
                m_id,
                [
                    # opp tries to use the character host banned
                    draft_service.DraftResultLine(
                        user_id=host, placement=1
                    ),
                    draft_service.DraftResultLine(
                        user_id=opp, placement=2, uma_character_id=forbidden.id
                    ),
                ],
                confirmed_by_user_id=host,
            )


def test_room_code_24h_expiry(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        # cheat the state forward
        m = db.session.get(DraftMatch, m_id)
        m.status = DraftMatchStatus.ROOM_CODE_PENDING
        db.session.commit()

        issued_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        draft_service.set_room_code(m_id, "ABC1", now=issued_at)
        m = db.session.get(DraftMatch, m_id)
        assert draft_service.is_room_code_expired(
            m, now=issued_at + timedelta(hours=23, minutes=59)
        ) is False
        assert draft_service.is_room_code_expired(
            m, now=issued_at + timedelta(hours=24)
        ) is True


def test_randomization_failure_marks_match(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        _preset(name="OnlyOne", venue="Tokyo", direction="Left")
        draft_service.submit_track_ban(
            m_id, host, ban_type=DraftBanType.VENUE, condition_key="Tokyo"
        )
        draft_service.submit_track_ban(
            m_id, opp, ban_type=DraftBanType.VENUE, condition_key="__skip__"
        )
        from uma_ladder.services.randomizer import RandomizerError

        with pytest.raises(RandomizerError):
            draft_service.randomize_preset(m_id)
        m = draft_service.get_match(m_id)
        assert m.status == DraftMatchStatus.RANDOMIZATION_FAILED


def test_elo_ladder_orders_by_rating(app: Flask) -> None:
    with app.app_context():
        s = _season()
        a = _user("alice")
        b = _user("bob")
        c = _user("carol")
        # one real match (so FK to draft_matches is satisfied)
        m_id = _match_with_two(a, b, season_id=s.id)
        for user_id, rating, outcome in [
            (a, 1100, 1.0),
            (b, 950, 0.0),
            (c, 1010, 1.0),
        ]:
            db.session.add(
                DraftEloChange(
                    draft_match_id=m_id,
                    season_id=s.id,
                    user_id=user_id,
                    opponent_user_id=user_id,
                    rating_before=1000,
                    rating_after=rating,
                    delta=rating - 1000,
                    outcome=outcome,
                )
            )
        db.session.commit()
        rows = draft_service.season_elo_ladder(s.id)
        assert [(r.username, r.rating) for r in rows] == [
            ("alice", 1100),
            ("carol", 1010),
            ("bob", 950),
        ]
