"""PR-P5 — auto-grant hook tests.

Covers the three helper functions in services.achievements
(`grant_on_official_result`, `grant_on_draft_match_completion`,
`grant_on_season_close`) and the end-to-end wiring through the
service flows (`official.submit_results`, `seasons.set_status`).

Idempotency is enforced at the existing `grant()` layer, but we
still re-test here because the auto-grant helpers fan out multiple
grants per call and we want a regression net against
double-granting from a stray refactor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.services import achievements as achievements_service
from uma_ladder.services import official as official_service
from uma_ladder.services import seasons as seasons_service
from uma_ladder.services.auth import RegistrationRequest, register_user


def _make_season(name: str = "S1", status: str = SeasonStatus.ACTIVE) -> Season:
    now = datetime.now(UTC)
    s = Season(
        name=name,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=89),
        status=status,
    )
    db.session.add(s)
    db.session.commit()
    return s


def _make_user(username: str, role: str = Role.USER) -> int:
    u = register_user(
        RegistrationRequest(username=username, password="password123", role=role)
    )
    return u.id


def _has(user_id: int, key: str) -> bool:
    return achievements_service.has_achievement(user_id, key)


# ─── Helpers (unit) ──────────────────────────────────────────────


def test_grant_on_official_result_first_place(app: Flask) -> None:
    """Placement 1 grants all three official milestones."""
    with app.app_context():
        uid = _make_user("winner")
        achievements_service.grant_on_official_result(uid, placement=1)
        assert _has(uid, "first_official_race")
        assert _has(uid, "first_official_podium")
        assert _has(uid, "first_official_win")


def test_grant_on_official_result_podium_not_win(app: Flask) -> None:
    """Placement 2 grants race + podium but not win."""
    with app.app_context():
        uid = _make_user("p2")
        achievements_service.grant_on_official_result(uid, placement=2)
        assert _has(uid, "first_official_race")
        assert _has(uid, "first_official_podium")
        assert not _has(uid, "first_official_win")


def test_grant_on_official_result_no_podium(app: Flask) -> None:
    """Placement 4 grants only the participation milestone."""
    with app.app_context():
        uid = _make_user("p4")
        achievements_service.grant_on_official_result(uid, placement=4)
        assert _has(uid, "first_official_race")
        assert not _has(uid, "first_official_podium")
        assert not _has(uid, "first_official_win")


def test_grant_on_official_result_idempotent(app: Flask) -> None:
    """Re-calling the helper doesn't insert duplicate rows. The
    user keeps exactly one UserAchievement per achievement."""
    with app.app_context():
        from uma_ladder.models import UserAchievement

        uid = _make_user("repeat")
        achievements_service.grant_on_official_result(uid, placement=1)
        achievements_service.grant_on_official_result(uid, placement=1)
        achievements_service.grant_on_official_result(uid, placement=1)
        count = (
            db.session.query(UserAchievement)
            .filter_by(user_id=uid)
            .count()
        )
        assert count == 3  # three distinct keys, one row each


def test_grant_on_draft_match_completion(app: Flask) -> None:
    """Both participants get first_draft_match; only the winner
    gets first_draft_win."""
    with app.app_context():
        host = _make_user("host")
        opp = _make_user("opp")
        achievements_service.grant_on_draft_match_completion(
            host_user_id=host,
            opponent_user_id=opp,
            winner_user_id=host,
        )
        assert _has(host, "first_draft_match")
        assert _has(opp, "first_draft_match")
        assert _has(host, "first_draft_win")
        assert not _has(opp, "first_draft_win")


def test_grant_on_draft_match_completion_no_opponent(
    app: Flask,
) -> None:
    """Defensive: if opponent_user_id is None (shouldn't happen at
    this point in the flow, but the column allows it), we skip
    them rather than crash."""
    with app.app_context():
        host = _make_user("solo_host")
        achievements_service.grant_on_draft_match_completion(
            host_user_id=host,
            opponent_user_id=None,
            winner_user_id=host,
        )
        assert _has(host, "first_draft_match")
        assert _has(host, "first_draft_win")


def test_grant_on_season_close_top_3(app: Flask) -> None:
    """Top 3 of the season ladder get season_top_3; rank 1 also
    gets season_champion."""
    with app.app_context():
        season = _make_season()
        organizer = _make_user("org", role=Role.ORGANIZER)
        a = _make_user("a")
        b = _make_user("b")
        c = _make_user("c")
        d = _make_user("d")

        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R1", organizer_user_id=organizer
            )
        )
        official_service.open_registration(race.id)
        for uid in (a, b, c, d):
            official_service.register(race.id, uid)
        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=a, placement=1),
                official_service.ResultLine(user_id=b, placement=2),
                official_service.ResultLine(user_id=c, placement=3),
                official_service.ResultLine(user_id=d, placement=4),
            ],
            confirmed_by_user_id=organizer,
            notify=False,
        )

        # Sanity: race-result grants fired (covered separately) —
        # season-close grants haven't yet because the season is
        # still ACTIVE.
        assert not _has(a, "season_champion")
        assert not _has(a, "season_top_3")

        achievements_service.grant_on_season_close(season.id)

        assert _has(a, "season_champion")
        assert _has(a, "season_top_3")
        assert _has(b, "season_top_3")
        assert _has(c, "season_top_3")
        assert not _has(b, "season_champion")
        assert not _has(d, "season_top_3")  # 4th place — out of top 3


# ─── End-to-end wiring ───────────────────────────────────────────


def test_submit_results_fires_grants(app: Flask) -> None:
    """The wiring in services.official.submit_results actually
    triggers the auto-grants. Catches the case where someone moves
    the grant block but forgets to re-import the helper."""
    with app.app_context():
        season = _make_season()
        organizer = _make_user("org", role=Role.ORGANIZER)
        winner = _make_user("winner")
        loser = _make_user("loser")
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R1", organizer_user_id=organizer
            )
        )
        official_service.open_registration(race.id)
        official_service.register(race.id, winner)
        official_service.register(race.id, loser)

        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=winner, placement=1),
                official_service.ResultLine(user_id=loser, placement=2),
            ],
            confirmed_by_user_id=organizer,
            notify=False,
        )

        assert _has(winner, "first_official_race")
        assert _has(winner, "first_official_podium")
        assert _has(winner, "first_official_win")
        assert _has(loser, "first_official_race")
        assert _has(loser, "first_official_podium")
        assert not _has(loser, "first_official_win")


def test_season_set_status_to_completed_fires_grants(
    app: Flask,
) -> None:
    """Flipping a season to COMPLETED via the seasons service
    auto-grants the podium achievements to the top 3."""
    with app.app_context():
        season = _make_season()
        organizer = _make_user("org", role=Role.ORGANIZER)
        a = _make_user("a")
        b = _make_user("b")
        c = _make_user("c")

        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer
            )
        )
        official_service.open_registration(race.id)
        for uid in (a, b, c):
            official_service.register(race.id, uid)
        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=a, placement=1),
                official_service.ResultLine(user_id=b, placement=2),
                official_service.ResultLine(user_id=c, placement=3),
            ],
            confirmed_by_user_id=organizer,
            notify=False,
        )

        assert not _has(a, "season_champion")
        seasons_service.set_status(season.id, SeasonStatus.COMPLETED)
        assert _has(a, "season_champion")
        assert _has(a, "season_top_3")
        assert _has(b, "season_top_3")
        assert _has(c, "season_top_3")


def test_season_set_status_already_completed_does_not_refire(
    app: Flask,
) -> None:
    """If a season is already COMPLETED, re-setting to COMPLETED is
    a no-op (no extra grant work). Tests the `was_completed`
    guard."""
    with app.app_context():
        season = _make_season(status=SeasonStatus.COMPLETED)
        organizer = _make_user("org", role=Role.ORGANIZER)
        a = _make_user("a")

        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer
            )
        )
        official_service.open_registration(race.id)
        official_service.register(race.id, a)
        official_service.submit_results(
            race.id,
            [official_service.ResultLine(user_id=a, placement=1)],
            confirmed_by_user_id=organizer,
            notify=False,
        )

        # First re-set: was_completed=True, no grant.
        seasons_service.set_status(season.id, SeasonStatus.COMPLETED)
        assert not _has(a, "season_champion")


def test_grant_helper_swallows_unknown_user_id(app: Flask) -> None:
    """`_safe_grant` resolves user_id internally. Passing an id
    that doesn't exist should be a no-op — never raise."""
    with app.app_context():
        # No exception even though user_id 999999 doesn't exist.
        achievements_service.grant_on_official_result(999_999, placement=1)
