"""Admin audit log — append-only feed of privileged actions.

Service is best-effort (audit failure must never block the actual
change). Hooks fire from role change / race cancel / match cancel /
forfeit. UI is admin-gated and paginated with action filter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    AdminAuditLog,
    DraftMatchStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
    User,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.models.users import Role as RoleEnum
from uma_ladder.services import admin as admin_service
from uma_ladder.services import admin_audit
from uma_ladder.services import draft as draft_service
from uma_ladder.services import official as official_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


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


def _ensure_preset(app: Flask) -> RacePreset:
    p = db.session.query(RacePreset).first()
    if p:
        return p
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


def test_log_action_appends_row(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    with app.app_context():
        row = admin_audit.log_action(
            actor_user_id=actor["id"],
            action="role_change",
            target_user_id=target["id"],
            before={"role": "user"},
            after={"role": "organizer"},
        )
        assert row is not None
        assert row.id is not None
        assert row.action == "role_change"
        assert row.before_json == {"role": "user"}
        assert row.after_json == {"role": "organizer"}


def test_log_action_swallows_exceptions(app: Flask, monkeypatch) -> None:
    """A broken audit row should never propagate. We force a commit
    failure and verify None is returned cleanly."""
    with app.app_context():
        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(db.session, "commit", boom)
        result = admin_audit.log_action(
            actor_user_id=None, action="role_change"
        )
        assert result is None


def test_role_change_writes_audit_entry(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    with app.app_context():
        admin_service.change_user_role(
            actor=db.session.get(User, actor["id"]),
            target=db.session.get(User, target["id"]),
            new_role=RoleEnum.ORGANIZER,
        )
        rows = db.session.query(AdminAuditLog).filter_by(
            action="role_change"
        ).all()
        assert len(rows) == 1
        assert rows[0].before_json == {"role": "user"}
        assert rows[0].after_json == {"role": "organizer"}


def test_race_cancel_writes_audit_entry(app: Flask, make_user) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context():
        season = _ensure_season(app)
        preset = _ensure_preset(app)
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=season.id,
                organizer_user_id=org["id"],
                name="ToCancel",
                preset_id=preset.id,
                max_players=12,
                notes=None,
            )
        )
        official_service.cancel_race(race.id, by_user_id=org["id"])
        rows = db.session.query(AdminAuditLog).filter_by(
            action="official_race_cancel"
        ).all()
        assert len(rows) == 1
        assert rows[0].target_kind == "official_race"
        assert rows[0].target_id == race.id


def test_match_cancel_and_forfeit_write_audit_entries(
    app: Flask, make_user
) -> None:
    host = make_user(username="host", role=Role.USER)
    opp = make_user(username="opp", role=Role.USER)
    org = make_user(username="org", role=Role.ORGANIZER)
    with app.app_context():
        season = _ensure_season(app)
        # Cancel path: create match, cancel.
        m1 = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=season.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        draft_service.cancel_match(m1.id, by_user_id=org["id"])
        # Forfeit path: create another match wired to opponent +
        # ROOM_CODE_AVAILABLE state, forfeit.
        m2 = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=season.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        m2.opponent_user_id = opp["id"]
        m2.status = DraftMatchStatus.ROOM_CODE_AVAILABLE
        db.session.commit()
        draft_service.submit_forfeit(
            m2.id,
            forfeiter_user_id=host["id"],
            by_user_id=org["id"],
            reason="ban_violation",
        )

        cancel_rows = db.session.query(AdminAuditLog).filter_by(
            action="draft_match_cancel"
        ).all()
        forfeit_rows = db.session.query(AdminAuditLog).filter_by(
            action="draft_match_forfeit"
        ).all()
        assert len(cancel_rows) == 1
        assert cancel_rows[0].target_id == m1.id
        assert len(forfeit_rows) == 1
        assert forfeit_rows[0].target_user_id == host["id"]
        assert forfeit_rows[0].details == "ban_violation"


def test_list_recent_filters_and_paginates(app: Flask, make_user) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        for i in range(7):
            admin_audit.log_action(
                actor_user_id=actor["id"],
                action="role_change" if i % 2 == 0 else "draft_match_cancel",
                details=f"#{i}",
            )
        page1 = admin_audit.list_recent(page=1, page_size=5)
        assert page1.total == 7
        assert len(page1.entries) == 5
        # Filter by action.
        only_role = admin_audit.list_recent(action="role_change", page_size=10)
        assert only_role.total == 4
        assert all(e.action == "role_change" for e in only_role.entries)


# ---------- HTTP layer ----------


def test_audit_page_admin_gate(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    resp = client.get("/admin/audit")
    assert resp.status_code == 403


def test_audit_page_renders(
    client: FlaskClient, app: Flask, make_user
) -> None:
    actor = make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="bob", role=Role.USER)
    with app.app_context():
        admin_service.change_user_role(
            actor=db.session.get(User, actor["id"]),
            target=db.session.get(User, target["id"]),
            new_role=RoleEnum.ORGANIZER,
        )
    _login(client, "adm")
    resp = client.get("/admin/audit")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Audit log" in body
    assert "role_change" in body
    assert "@adm" in body
    assert "@bob" in body


def test_audit_page_filter_by_action(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        admin_audit.log_action(
            actor_user_id=None, action="role_change", details="r"
        )
        admin_audit.log_action(
            actor_user_id=None, action="draft_match_cancel", details="c"
        )
    _login(client, "adm")
    resp = client.get("/admin/audit?action=role_change")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "role_change" in body
    # Cancel entry's details "c" shouldn't appear when filtered to role_change.
    assert ">c<" not in body


def test_admin_draft_edit_results_gate(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Organizer can NOT reach the admin edit-results page; only
    admin+ can."""
    make_user(username="org", role=Role.ORGANIZER)
    _login(client, "org")
    # Match doesn't have to exist for the role check to fire — gate
    # runs before the route handler queries the DB.
    resp = client.get("/admin/draft/1/edit-results")
    assert resp.status_code == 403


def test_admin_draft_edit_results_recovers_botched_match(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """End-to-end: a 2v2 match was completed with the wrong winner
    (3 lines instead of 4). Admin edits via the form → match flips
    to the correct winner with full 2v2 results."""
    from uma_ladder.models import DraftMatchStatus
    from uma_ladder.services import draft as draft_service

    make_user(username="adm", role=Role.ADMIN, password="password123")
    host = make_user(username="hostie", password="password123")
    opp = make_user(username="oppy", password="password123")
    with app.app_context():
        s = Season(
            name="S",
            starts_at=datetime.now(UTC) - timedelta(days=1),
            ends_at=datetime.now(UTC) + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        # Build a completed match by jumping past ban + randomization
        # (we just need a COMPLETED match to edit).
        m = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        m.opponent_user_id = opp["id"]
        m.status = DraftMatchStatus.ROOM_CODE_AVAILABLE
        db.session.commit()
        # Submit wrong/incomplete results.
        draft_service.submit_results(
            m.id,
            [
                draft_service.DraftResultLine(user_id=host["id"], placement=1),
                draft_service.DraftResultLine(user_id=host["id"], placement=4),
                draft_service.DraftResultLine(user_id=opp["id"],  placement=2),
            ],
            confirmed_by_user_id=host["id"],
        )
        match_id = m.id
        # Sums: host 1+4=5, opp 2 → opp wins, wrong outcome.
        assert draft_service.get_match(match_id).winner_user_id == opp["id"]

    _login(client, "adm", "password123")
    resp = client.post(
        f"/admin/draft/{match_id}/edit-results",
        data={
            "row_count": "4",
            "placement_0": "1",
            "user_0": str(host["id"]),
            "uma_name_0": "Gold Ship",
            "placement_1": "2",
            "user_1": str(host["id"]),
            "uma_name_1": "Biwa Hayahide",
            "placement_2": "3",
            "user_2": str(opp["id"]),
            "uma_name_2": "Tamamo Cross",
            "placement_3": "4",
            "user_3": str(opp["id"]),
            "uma_name_3": "Daiwa Scarlet",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        match = draft_service.get_match(match_id)
        assert match.winner_user_id == host["id"]
        results = draft_service.list_results_for_match(match_id)
        assert len(results) == 4
        # Audit row written under the new action.
        rows = (
            db.session.query(AdminAuditLog)
            .filter_by(action="draft_match_edit_results")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].target_id == match_id
