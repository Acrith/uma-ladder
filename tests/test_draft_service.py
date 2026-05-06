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


def _outfit(char: UmaCharacter, costume_suffix: int = 1):
    """Helper: create a costume row for a character so it can be banned."""
    from uma_ladder.models import UmaOutfit

    o = UmaOutfit(
        uma_character_id=char.id,
        costume_id=char.id * 100 + costume_suffix,
        title_en=f"{char.slug} costume {costume_suffix}",
        released_globally=True,
        enabled=True,
    )
    db.session.add(o)
    db.session.commit()
    return o


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
        # Two presets so the Tokyo ban doesn't empty the pool (which would
        # be caught by the new feasibility check before we test the dedup).
        _preset(name="Tokyo P", venue="Tokyo")
        _preset(name="Sapporo P", venue="Sapporo", direction="Right")
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
        _preset(name="A", venue="Tokyo")
        _preset(name="B", venue="Sapporo")
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
        keeper = _preset(name="Keeper", venue="Sapporo", direction="Left")
        _preset(name="Banned", venue="Tokyo", direction="Left")
        # Add a Right preset so opp's Right ban does something (otherwise the
        # redundancy check would reject it).
        _preset(name="Right one", venue="Hakodate", direction="Right")
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
        o1 = _outfit(c1)
        c2 = _character("gold-ship")
        o2 = _outfit(c2)
        draft_service.submit_uma_ban(m_id, host, c1.id, uma_outfit_id=o1.id)
        draft_service.submit_uma_ban(m_id, opp, c2.id, uma_outfit_id=o2.id)
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
        o_banned_by_host = _outfit(c_banned_by_host)
        c_banned_by_opp = _character("banned-by-opp")
        o_banned_by_opp = _outfit(c_banned_by_opp)
        c_used = _character("used-uma")
        draft_service.submit_uma_ban(
            m_id, host, c_banned_by_host.id, uma_outfit_id=o_banned_by_host.id
        )
        draft_service.submit_uma_ban(
            m_id, opp, c_banned_by_opp.id, uma_outfit_id=o_banned_by_opp.id
        )

        draft_service.set_room_code(m_id, "RC-1")
        # 2v2 max-margin sweep: host (1,2) vs opp (3,4) → sum 3 vs 7,
        # margin 4 of max 4 → K-multiplier 1.5 → effective K 48 →
        # delta = 48 * (1 - 0.5) = 24.
        m = draft_service.submit_results(
            m_id,
            [
                draft_service.DraftResultLine(
                    user_id=host, placement=1, uma_character_id=c_used.id
                ),
                draft_service.DraftResultLine(
                    user_id=host, placement=2, custom_uma_name="Host Second"
                ),
                draft_service.DraftResultLine(
                    user_id=opp, placement=3, custom_uma_name="Opp First"
                ),
                draft_service.DraftResultLine(
                    user_id=opp, placement=4, custom_uma_name="Opp Second"
                ),
            ],
            confirmed_by_user_id=host,
        )
        assert m.status == DraftMatchStatus.COMPLETED
        assert m.winner_user_id == host
        assert draft_service.current_rating(host, s.id) == DEFAULT_RATING + 24
        assert draft_service.current_rating(opp, s.id) == DEFAULT_RATING - 24
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
        forbidden_outfit = _outfit(forbidden)
        whatever = _character("whatever")
        whatever_outfit = _outfit(whatever)
        draft_service.submit_uma_ban(
            m_id, host, forbidden.id, uma_outfit_id=forbidden_outfit.id
        )
        draft_service.submit_uma_ban(
            m_id, opp, whatever.id, uma_outfit_id=whatever_outfit.id
        )
        draft_service.set_room_code(m_id, "RC-1")
        with pytest.raises(draft_service.BannedCharacterUsedError):
            draft_service.submit_results(
                m_id,
                [
                    # opp tries to use the exact costume host banned
                    draft_service.DraftResultLine(
                        user_id=host, placement=1
                    ),
                    draft_service.DraftResultLine(
                        user_id=opp,
                        placement=2,
                        uma_character_id=forbidden.id,
                        uma_outfit_id=forbidden_outfit.id,
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
    """Defensive: even though submit_track_ban now rejects pool-emptying
    bans, randomize_preset must still mark the match as
    RANDOMIZATION_FAILED if a preset gets disabled or the pool changes
    between ban submission and the roll. We exercise that path by
    inserting a pool-emptying ban directly via the DB."""
    from uma_ladder.models import DraftBanType, DraftMatchBan

    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id)
        _drive_to_track_ban(m_id, host, opp)
        _preset(name="OnlyOne", venue="Tokyo", direction="Left")
        # Bypass submit_track_ban's feasibility check by writing rows
        # directly — simulates the race-condition / pool-change path.
        for uid, value in ((host, "Tokyo"), (opp, "__skip__")):
            db.session.add(
                DraftMatchBan(
                    draft_match_id=m_id,
                    user_id=uid,
                    ban_type=DraftBanType.VENUE,
                    condition_key=value,
                    locked_at=datetime.now(UTC),
                )
            )
        db.session.commit()

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


# ---------- PR-I2: placement-aware ELO for 2v2 / 3v3 ----------


def _drive_to_results_phase(match_id: int, host: int, opp: int) -> None:
    """Walk a match through ban → randomize → room-code so submit_results
    is reachable. Picks the cheapest deterministic path."""
    _drive_to_track_ban(match_id, host, opp)
    _preset(name="K", venue="Sapporo")
    _preset(name="X", venue="Tokyo")
    draft_service.submit_track_ban(
        match_id, host, ban_type=DraftBanType.VENUE, condition_key="Tokyo"
    )
    draft_service.submit_track_ban(
        match_id, opp, ban_type=DraftBanType.VENUE, condition_key="__skip__"
    )
    draft_service.randomize_preset(match_id, rng=random.Random(0))
    c_h = _character("h-uma")
    o_h = _outfit(c_h)
    c_o = _character("o-uma")
    o_o = _outfit(c_o)
    draft_service.submit_uma_ban(match_id, host, c_h.id, uma_outfit_id=o_h.id)
    draft_service.submit_uma_ban(match_id, opp, c_o.id, uma_outfit_id=o_o.id)
    draft_service.set_room_code(match_id, "RC")


def _line(user_id: int, placement: int) -> draft_service.DraftResultLine:
    return draft_service.DraftResultLine(
        user_id=user_id,
        placement=placement,
        custom_uma_name=f"u{user_id}-{placement}",
    )


def test_2v2_max_margin_decisive_win_gives_15x_k(app: Flask) -> None:
    """Host (1,2) vs opp (3,4) — sum 3 vs 7, max possible margin.
    K-multiplier 1.5 → effective K=48 → ±24 swing."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        _drive_to_results_phase(m_id, host, opp)
        draft_service.submit_results(
            m_id,
            [_line(host, 1), _line(host, 2), _line(opp, 3), _line(opp, 4)],
            confirmed_by_user_id=host,
        )
        assert draft_service.current_rating(host, s.id) == DEFAULT_RATING + 24
        assert draft_service.current_rating(opp, s.id) == DEFAULT_RATING - 24


def test_2v2_split_placements_mid_margin(app: Flask) -> None:
    """Host (1,3) vs opp (2,4) — sum 4 vs 6, margin 2 of max 4.
    K-multiplier 1.0 → effective K=32 → ±16 swing."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        _drive_to_results_phase(m_id, host, opp)
        draft_service.submit_results(
            m_id,
            [_line(host, 1), _line(host, 3), _line(opp, 2), _line(opp, 4)],
            confirmed_by_user_id=host,
        )
        assert draft_service.current_rating(host, s.id) == DEFAULT_RATING + 16
        assert draft_service.current_rating(opp, s.id) == DEFAULT_RATING - 16


def test_2v2_winner_is_best_individual_with_minimal_swing_on_scrap(app: Flask) -> None:
    """PR-I4 final contract: best individual decides the winner;
    SIGNED team-sum margin (loser_sum - winner_sum) drives the
    K-multiplier.

    Host (1,8) vs opp (2,3) — host wins (has #1) but host_sum=9 is
    worse than opp_sum=5. Signed margin = 5 - 9 = -4. Multiplier
    floor at 0.1 → effective K=3 → ±2 swing. The scrap-win case the
    user wanted to be near-zero ELO."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        _drive_to_results_phase(m_id, host, opp)
        m = draft_service.submit_results(
            m_id,
            [_line(host, 1), _line(host, 8), _line(opp, 2), _line(opp, 3)],
            confirmed_by_user_id=host,
        )
        assert m.winner_user_id == host
        # Floor multiplier produces minimal swing — exactly the
        # "barely moved the needle" outcome for a debuffer-carry win.
        host_change = draft_service.current_rating(host, s.id) - DEFAULT_RATING
        opp_change = draft_service.current_rating(opp, s.id) - DEFAULT_RATING
        assert 0 < host_change <= 4
        assert -4 <= opp_change < 0


def test_2v2_tied_sums_still_decided_by_best_individual(app: Flask) -> None:
    """Host (1,4) vs opp (2,3) — sums tied at 5. Best individual:
    host has 1 → host wins. Margin 0 → K-multiplier 0.5 →
    effective K=16 → ±8 swing (the near-coin-flip case)."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        _drive_to_results_phase(m_id, host, opp)
        m = draft_service.submit_results(
            m_id,
            [_line(host, 1), _line(host, 4), _line(opp, 2), _line(opp, 3)],
            confirmed_by_user_id=host,
        )
        assert m.winner_user_id == host
        assert draft_service.current_rating(host, s.id) == DEFAULT_RATING + 8
        assert draft_service.current_rating(opp, s.id) == DEFAULT_RATING - 8


def test_3v3_max_margin_sweep(app: Flask) -> None:
    """Host (1,2,3) vs opp (4,5,6) — sum 6 vs 15, margin 9 of max 9.
    K-multiplier 1.5 → effective K=48 → ±24 swing."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=3)
        _drive_to_results_phase(m_id, host, opp)
        draft_service.submit_results(
            m_id,
            [
                _line(host, 1), _line(host, 2), _line(host, 3),
                _line(opp, 4), _line(opp, 5), _line(opp, 6),
            ],
            confirmed_by_user_id=host,
        )
        assert draft_service.current_rating(host, s.id) == DEFAULT_RATING + 24
        assert draft_service.current_rating(opp, s.id) == DEFAULT_RATING - 24


def test_winner_is_best_individual_with_signed_magnitude(app: Flask) -> None:
    """Host (1,6) vs opp (2,3) — host wins (has #1). Signed margin
    (loser-winner) = 5 - 7 = -2 of max +4. Multiplier 0 capped to
    floor 0.1 → effective K=3 → ±2 swing. Compare to the symmetric
    abs-margin formula (now removed) which would have given ±16."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        _drive_to_results_phase(m_id, host, opp)
        m = draft_service.submit_results(
            m_id,
            [_line(host, 1), _line(host, 6), _line(opp, 2), _line(opp, 3)],
            confirmed_by_user_id=host,
        )
        assert m.winner_user_id == host
        host_change = draft_service.current_rating(host, s.id) - DEFAULT_RATING
        opp_change = draft_service.current_rating(opp, s.id) - DEFAULT_RATING
        # Signed-margin scrap win: small but non-zero swing.
        assert 0 < host_change <= 4
        assert -4 <= opp_change < 0


def test_3v3_scrap_win_with_carry_uma_nets_minimal_elo(app: Flask) -> None:
    """The user's headline 3v3 case: host (1,5,6) vs opp (2,3,4).
    Host wins because they have #1, but their other two umas placed
    last among the player set. Host sum=12, opp sum=9. Signed
    margin = 9 - 12 = -3 of max +9 → multiplier 0.5 - 1/3 ≈ 0.17
    → effective K≈5 → ±3 swing.

    Caters to debuffer-style team comps where one carry uma wins
    while teammates enable that win without placing high — should
    not net the same ELO as a clean sweep."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=3)
        _drive_to_results_phase(m_id, host, opp)
        m = draft_service.submit_results(
            m_id,
            [
                _line(host, 1), _line(host, 5), _line(host, 6),
                _line(opp, 2),  _line(opp, 3),  _line(opp, 4),
            ],
            confirmed_by_user_id=host,
        )
        assert m.winner_user_id == host
        host_change = draft_service.current_rating(host, s.id) - DEFAULT_RATING
        opp_change = draft_service.current_rating(opp, s.id) - DEFAULT_RATING
        # Multiplier ~0.17, K~5, delta = round(5*0.5) = 2 or 3.
        assert 1 <= host_change <= 5
        assert -5 <= opp_change <= -1
        # And critically: notably smaller than a 3v3 sweep, which
        # would give ±24 (mult 1.5, K=48).
        assert host_change < 24


# ---------- PR-I4: validate_completeness + edit_results ----------


def test_validate_completeness_flags_missing_umas(app: Flask) -> None:
    """A 2v2 with only 3 lines (one player missing an uma) returns
    a human-readable warning per offending player. Submission is
    still allowed; the warning drives the JS confirm dialog."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        match = draft_service.get_match(m_id)
        # Yuuta-style mistake: 2 lines for host, only 1 for opp.
        lines = [
            draft_service.DraftResultLine(user_id=host, placement=1),
            draft_service.DraftResultLine(user_id=opp,  placement=2),
            draft_service.DraftResultLine(user_id=host, placement=3),
        ]
        warnings = draft_service.validate_completeness(match, lines)
        assert len(warnings) == 1
        assert "bob" in warnings[0]
        assert "1 uma placement" in warnings[0]
        assert "expects 2" in warnings[0]


def test_validate_completeness_clean_2v2_returns_empty(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        match = draft_service.get_match(m_id)
        lines = [
            draft_service.DraftResultLine(user_id=host, placement=1),
            draft_service.DraftResultLine(user_id=host, placement=2),
            draft_service.DraftResultLine(user_id=opp,  placement=3),
            draft_service.DraftResultLine(user_id=opp,  placement=4),
        ]
        assert draft_service.validate_completeness(match, lines) == []


def test_edit_results_replaces_results_and_recomputes_elo(app: Flask) -> None:
    """Admin recovery for a botched submission: the OCR review may
    have mis-assigned a row (e.g. a host uma got marked as opp's),
    which changes who has the #1 finisher and therefore who wins.
    Admin edits → new line set → winner flips correctly, ELO is
    recomputed against the *current* rating, exactly two change
    rows persist (the old ones were wiped)."""
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        _drive_to_results_phase(m_id, host, opp)

        # Original (wrong) submission: rows mis-attributed so opp
        # ends up with the #1 finisher → opp wins.
        draft_service.submit_results(
            m_id,
            [
                draft_service.DraftResultLine(user_id=opp,  placement=1),
                draft_service.DraftResultLine(user_id=opp,  placement=4),
                draft_service.DraftResultLine(user_id=host, placement=2),
                draft_service.DraftResultLine(user_id=host, placement=3),
            ],
            confirmed_by_user_id=host,
        )
        match = draft_service.get_match(m_id)
        assert match.winner_user_id == opp  # opp has #1, opp wins.

        # Admin corrects the row attributions: host actually had #1.
        draft_service.edit_results(
            m_id,
            [
                draft_service.DraftResultLine(user_id=host, placement=1),
                draft_service.DraftResultLine(user_id=host, placement=2),
                draft_service.DraftResultLine(user_id=opp,  placement=3),
                draft_service.DraftResultLine(user_id=opp,  placement=4),
            ],
            by_user_id=host,
        )
        match = draft_service.get_match(m_id)
        assert match.winner_user_id == host
        assert match.status == DraftMatchStatus.COMPLETED

        results = draft_service.list_results_for_match(m_id)
        elo = draft_service.list_elo_changes_for_match(m_id)
        assert len(results) == 4
        assert len(elo) == 2

        host_delta = next(c.delta for c in elo if c.user_id == host)
        opp_delta = next(c.delta for c in elo if c.user_id == opp)
        assert host_delta > 0
        assert opp_delta < 0


def test_edit_results_refuses_non_completed_match(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        # Match is in WAITING_FOR_OPPONENT or similar — not completed.
        with pytest.raises(draft_service.InvalidMatchStateError):
            draft_service.edit_results(
                m_id,
                [draft_service.DraftResultLine(user_id=host, placement=1)],
                by_user_id=host,
            )


def test_list_results_for_match_orders_by_placement(app: Flask) -> None:
    with app.app_context():
        s = _season()
        host = _user("alice")
        opp = _user("bob")
        m_id = _match_with_two(host, opp, season_id=s.id, umas_per_player=2)
        _drive_to_results_phase(m_id, host, opp)
        draft_service.submit_results(
            m_id,
            [
                draft_service.DraftResultLine(user_id=host, placement=3),
                draft_service.DraftResultLine(user_id=host, placement=1),
                draft_service.DraftResultLine(user_id=opp,  placement=2),
                draft_service.DraftResultLine(user_id=opp,  placement=4),
            ],
            confirmed_by_user_id=host,
        )
        rows = draft_service.list_results_for_match(m_id)
        assert [r.placement for r in rows] == [1, 2, 3, 4]
