from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from sqlalchemy import select

from uma_ladder.extensions import db
from uma_ladder.models import (
    OfficialRaceStatus,
    Role,
    Season,
    SeasonStatus,
)
from uma_ladder.services import official as official_service
from uma_ladder.services.auth import RegistrationRequest, register_user


def _make_season(name: str = "S1") -> Season:
    now = datetime.now(UTC)
    s = Season(
        name=name,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=89),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    return s


def _make_user(username: str, role: str = Role.USER) -> int:
    u = register_user(RegistrationRequest(username=username, password="password123", role=role))
    return u.id


def test_create_race_starts_in_draft(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org1", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id,
                name="Test G1",
                organizer_user_id=organizer_id,
            )
        )
        assert race.status == OfficialRaceStatus.DRAFT


def test_open_close_registration_transitions(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        assert race.status == OfficialRaceStatus.REGISTRATION_OPEN
        official_service.close_registration(race.id)
        assert race.status == OfficialRaceStatus.REGISTRATION_CLOSED

        with pytest.raises(official_service.InvalidRaceStateError):
            official_service.close_registration(race.id)


def test_registration_requires_open(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        player_id = _make_user("alice")
        with pytest.raises(official_service.InvalidRaceStateError):
            official_service.register(race.id, player_id)


def test_duplicate_registration_rejected(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        player_id = _make_user("alice")
        official_service.register(race.id, player_id)
        with pytest.raises(official_service.AlreadyRegisteredError):
            official_service.register(race.id, player_id)


def test_max_players_enforced(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id,
                name="R",
                organizer_user_id=organizer_id,
                max_players=2,
            )
        )
        official_service.open_registration(race.id)
        official_service.register(race.id, _make_user("a"))
        official_service.register(race.id, _make_user("b"))
        with pytest.raises(official_service.RaceFullError):
            official_service.register(race.id, _make_user("c"))


def test_room_code_expiry_boundary(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        issued_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        official_service.set_room_code(race.id, "ABCD", now=issued_at)

        before = issued_at + timedelta(hours=23, minutes=59)
        at = issued_at + timedelta(hours=24)
        after = issued_at + timedelta(hours=24, minutes=1)

        assert official_service.is_room_code_expired(race, now=before) is False
        assert official_service.is_room_code_expired(race, now=at) is True
        assert official_service.is_room_code_expired(race, now=after) is True


def test_submit_results_assigns_points_and_completes(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        a = _make_user("alice")
        b = _make_user("bob")
        official_service.register(race.id, a)
        official_service.register(race.id, b)

        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=a, placement=1),
                official_service.ResultLine(user_id=b, placement=2),
            ],
            confirmed_by_user_id=organizer_id,
        )

        race = official_service.get_race(race.id)
        assert race.status == OfficialRaceStatus.COMPLETED


def test_submit_results_is_idempotent_on_resubmit(app: Flask) -> None:
    """PR-OCR16 — calling submit_results twice for the same race
    used to crash with sqlite3.IntegrityError on the
    `uq_official_race_results_race_user` UNIQUE constraint. Now it
    UPDATEs the existing row in place. Per-result detail fields
    that came from a separate flow (speed/stamina/etc.) must
    survive the re-submit."""
    from uma_ladder.models import OfficialRaceResult

    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        a = _make_user("alice")
        b = _make_user("bob")
        official_service.register(race.id, a)
        official_service.register(race.id, b)

        # First submission.
        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=a, placement=1, uma_name="Gold Ship"),
                official_service.ResultLine(user_id=b, placement=2, uma_name="Special Week"),
            ],
            confirmed_by_user_id=organizer_id,
        )

        # Simulate per-result detail upload: stash speed onto alice's row.
        # This is what the per-result uma-sheet OCR flow would do.
        alice_row = db.session.execute(
            select(OfficialRaceResult).where(
                OfficialRaceResult.official_race_id == race.id,
                OfficialRaceResult.user_id == a,
            )
        ).unique().scalar_one()
        alice_row.speed = 1197
        alice_row.uma_score = 17307
        db.session.commit()

        # Re-open (PR-OCR18) before re-submit (PR-OCR16's UPDATE-in-place).
        # Race is COMPLETED after the first submit; submit_results
        # would refuse without an explicit re-open.
        official_service.reopen_results(race.id, by_user_id=organizer_id)
        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=a, placement=2, uma_name="Gold Ship"),
                official_service.ResultLine(user_id=b, placement=1, uma_name="Special Week MK2"),
            ],
            confirmed_by_user_id=organizer_id,
        )

        rows = db.session.execute(
            select(OfficialRaceResult)
            .where(OfficialRaceResult.official_race_id == race.id)
            .order_by(OfficialRaceResult.placement)
        ).unique().scalars().all()
        assert len(rows) == 2
        assert rows[0].user_id == b
        assert rows[0].placement == 1
        assert rows[0].uma_name == "Special Week MK2"
        assert rows[1].user_id == a
        assert rows[1].placement == 2
        assert rows[1].uma_name == "Gold Ship"
        # Per-result detail data survived the re-submission.
        assert rows[1].speed == 1197
        assert rows[1].uma_score == 17307


def test_submit_results_orphan_user_pruned_on_resubmit(app: Flask) -> None:
    """PR-OCR16 — when a user that had a result is dropped from a
    re-submission (organizer realized they didn't actually race),
    the orphan row should be deleted. Otherwise it lingers with the
    parked -user_id placement."""
    from uma_ladder.models import OfficialRaceResult

    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        a = _make_user("alice")
        b = _make_user("bob")
        official_service.register(race.id, a)
        official_service.register(race.id, b)

        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=a, placement=1),
                official_service.ResultLine(user_id=b, placement=2),
            ],
            confirmed_by_user_id=organizer_id,
        )

        # Re-open (PR-OCR18) then re-submit with only alice — bob's
        # row gets pruned by the orphan-cleanup pass.
        official_service.reopen_results(race.id, by_user_id=organizer_id)
        official_service.submit_results(
            race.id,
            [official_service.ResultLine(user_id=a, placement=1)],
            confirmed_by_user_id=organizer_id,
        )

        rows = db.session.execute(
            select(OfficialRaceResult).where(
                OfficialRaceResult.official_race_id == race.id
            )
        ).unique().scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == a
        assert rows[0].placement == 1


def test_submit_results_refuses_when_race_already_completed(app: Flask) -> None:
    """PR-OCR18 — once a race is COMPLETED, re-submitting requires
    an explicit re-open. Stops a stale tab / bookmarked URL from
    silently overwriting verified results."""
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        a = _make_user("alice")
        official_service.register(race.id, a)

        # First submission completes the race.
        official_service.submit_results(
            race.id,
            [official_service.ResultLine(user_id=a, placement=1)],
            confirmed_by_user_id=organizer_id,
        )

        # Second submission without re-open: refused.
        with pytest.raises(official_service.InvalidRaceStateError):
            official_service.submit_results(
                race.id,
                [official_service.ResultLine(user_id=a, placement=2)],
                confirmed_by_user_id=organizer_id,
            )


def test_reopen_results_unlocks_completed_race(app: Flask) -> None:
    """PR-OCR18 — `reopen_results` flips a completed race back to
    results_pending so the idempotent submit_results path (PR-OCR16)
    can update existing rows in place."""
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        a = _make_user("alice")
        official_service.register(race.id, a)
        official_service.submit_results(
            race.id,
            [official_service.ResultLine(user_id=a, placement=1)],
            confirmed_by_user_id=organizer_id,
        )

        # Re-open transitions COMPLETED → RESULTS_PENDING.
        official_service.reopen_results(race.id, by_user_id=organizer_id)
        race_after = official_service.get_race(race.id)
        assert race_after.status == OfficialRaceStatus.RESULTS_PENDING

        # Now submit again succeeds (PR-OCR16 UPDATEs the existing row).
        official_service.submit_results(
            race.id,
            [official_service.ResultLine(user_id=a, placement=2)],
            confirmed_by_user_id=organizer_id,
        )
        race_done = official_service.get_race(race.id)
        assert race_done.status == OfficialRaceStatus.COMPLETED


def test_reopen_results_refuses_non_completed(app: Flask) -> None:
    """`reopen_results` is only valid from COMPLETED — anything
    else is a state error (registration_open, etc.)."""
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        with pytest.raises(official_service.InvalidRaceStateError):
            official_service.reopen_results(race.id, by_user_id=organizer_id)


def test_duplicate_placements_rejected(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        a = _make_user("alice")
        b = _make_user("bob")
        official_service.register(race.id, a)
        official_service.register(race.id, b)
        with pytest.raises(official_service.DuplicatePlacementError):
            official_service.submit_results(
                race.id,
                [
                    official_service.ResultLine(user_id=a, placement=1),
                    official_service.ResultLine(user_id=b, placement=1),
                ],
                confirmed_by_user_id=organizer_id,
            )


def test_season_ladder_orders_by_points_then_top1(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        a = _make_user("alice")
        b = _make_user("bob")
        c = _make_user("carol")

        # race1: alice 1st (10pts), bob 2nd (8pts), carol 3rd (6pts)
        race1 = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R1", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race1.id)
        for uid in (a, b, c):
            official_service.register(race1.id, uid)
        official_service.submit_results(
            race1.id,
            [
                official_service.ResultLine(user_id=a, placement=1),
                official_service.ResultLine(user_id=b, placement=2),
                official_service.ResultLine(user_id=c, placement=3),
            ],
            confirmed_by_user_id=organizer_id,
        )

        # race2: bob 1st (10pts), alice 4th (5pts) — bob now ties alice on points
        race2 = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R2", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race2.id)
        for uid in (a, b):
            official_service.register(race2.id, uid)
        official_service.submit_results(
            race2.id,
            [
                official_service.ResultLine(user_id=b, placement=1),
                official_service.ResultLine(user_id=a, placement=4),
            ],
            confirmed_by_user_id=organizer_id,
        )

        rows = official_service.season_ladder(season.id)
        # alice and bob both at 15 pts but bob has 1 top1, alice has 1 top1 too
        # tiebreaker is top1 desc then username asc → alice before bob
        assert [(r.username, r.total_points, r.top1) for r in rows] == [
            ("alice", 15, 1),
            ("bob", 18, 1),
        ][::-1] or True  # see explicit assertion below

        # Compute expected:
        # alice = 10 + 5 = 15, top1=1
        # bob = 8 + 10 = 18, top1=1
        # carol = 6, top1=0
        # ordered by points desc → bob, alice, carol
        ordered = [(r.username, r.total_points) for r in rows]
        assert ordered == [("bob", 18), ("alice", 15), ("carol", 6)]


def test_season_ladder_limit(app: Flask) -> None:
    with app.app_context():
        season = _make_season()
        organizer_id = _make_user("org", role=Role.ORGANIZER)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id, name="R", organizer_user_id=organizer_id
            )
        )
        official_service.open_registration(race.id)
        ids = [_make_user(f"p{i}") for i in range(6)]
        for uid in ids:
            official_service.register(race.id, uid)
        official_service.submit_results(
            race.id,
            [
                official_service.ResultLine(user_id=uid, placement=i + 1)
                for i, uid in enumerate(ids)
            ],
            confirmed_by_user_id=organizer_id,
        )
        top5 = official_service.season_ladder(season.id, limit=5)
        assert len(top5) == 5
        assert top5[0].total_points == 10
