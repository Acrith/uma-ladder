"""PR-Q3b — user-on-user moderation reports.

Service guards, admin queue routes, and the dismissed-count helper
that powers the "N prior dismissed" credibility chip.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import Report, ReportStatus, User
from uma_ladder.models.users import Role
from uma_ladder.services import reports as reports_service


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ─── Service layer ───────────────────────────────────────────────


def test_file_report_creates_open_row(app: Flask, make_user) -> None:
    reporter_d = make_user(username="reporter", role=Role.USER)
    target_d = make_user(username="badguy", role=Role.USER)
    with app.app_context():
        reporter = db.session.get(User, reporter_d["id"])
        row = reports_service.file_report(
            reporter=reporter,
            reported_user_id=target_d["id"],
            reason="They harassed me in chat",
            context="/profiles/badguy",
        )
        assert row.id is not None
        assert row.status == ReportStatus.OPEN
        assert row.reporter_user_id == reporter_d["id"]
        assert row.reported_user_id == target_d["id"]
        assert "harassed" in row.reason
        assert row.context == "/profiles/badguy"


def test_file_report_rejects_self(app: Flask, make_user) -> None:
    me_d = make_user(username="me", role=Role.USER)
    with app.app_context():
        me = db.session.get(User, me_d["id"])
        with pytest.raises(reports_service.SelfReportError):
            reports_service.file_report(
                reporter=me,
                reported_user_id=me_d["id"],
                reason="trying to report myself",
            )


def test_file_report_rejects_empty_reason(app: Flask, make_user) -> None:
    reporter_d = make_user(username="r", role=Role.USER)
    target_d = make_user(username="t", role=Role.USER)
    with app.app_context():
        reporter = db.session.get(User, reporter_d["id"])
        with pytest.raises(reports_service.ReportError):
            reports_service.file_report(
                reporter=reporter,
                reported_user_id=target_d["id"],
                reason="   ",
            )


def test_file_report_truncates_long_reason(app: Flask, make_user) -> None:
    reporter_d = make_user(username="r", role=Role.USER)
    target_d = make_user(username="t", role=Role.USER)
    long_reason = "x" * 800
    with app.app_context():
        reporter = db.session.get(User, reporter_d["id"])
        row = reports_service.file_report(
            reporter=reporter,
            reported_user_id=target_d["id"],
            reason=long_reason,
        )
        assert len(row.reason) == reports_service.MAX_REASON_CHARS


def test_file_report_target_must_exist(app: Flask, make_user) -> None:
    reporter_d = make_user(username="r", role=Role.USER)
    with app.app_context():
        reporter = db.session.get(User, reporter_d["id"])
        with pytest.raises(reports_service.TargetNotFoundError):
            reports_service.file_report(
                reporter=reporter,
                reported_user_id=999_999,
                reason="ghost",
            )


def test_mark_actioned_and_dismiss_transition_status(
    app: Flask, make_user
) -> None:
    reporter_d = make_user(username="r", role=Role.USER)
    target_d = make_user(username="t", role=Role.USER)
    admin_d = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        reporter = db.session.get(User, reporter_d["id"])
        admin = db.session.get(User, admin_d["id"])

        row1 = reports_service.file_report(
            reporter=reporter,
            reported_user_id=target_d["id"],
            reason="incident 1",
        )
        row2 = reports_service.file_report(
            reporter=reporter,
            reported_user_id=target_d["id"],
            reason="incident 2",
        )

        reports_service.mark_actioned(row1.id, actor=admin, notes="disabled")
        reports_service.dismiss(row2.id, actor=admin, notes="bad faith")

        refreshed1 = db.session.get(Report, row1.id)
        refreshed2 = db.session.get(Report, row2.id)
        assert refreshed1.status == ReportStatus.ACTIONED
        assert refreshed1.resolution_notes == "disabled"
        assert refreshed1.resolved_by_user_id == admin_d["id"]
        assert refreshed2.status == ReportStatus.DISMISSED
        assert refreshed2.resolution_notes == "bad faith"


def test_cannot_resolve_already_resolved(
    app: Flask, make_user
) -> None:
    reporter_d = make_user(username="r", role=Role.USER)
    target_d = make_user(username="t", role=Role.USER)
    admin_d = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        reporter = db.session.get(User, reporter_d["id"])
        admin = db.session.get(User, admin_d["id"])
        row = reports_service.file_report(
            reporter=reporter,
            reported_user_id=target_d["id"],
            reason="x",
        )
        reports_service.dismiss(row.id, actor=admin)
        with pytest.raises(reports_service.AlreadyResolvedError):
            reports_service.mark_actioned(row.id, actor=admin)


def test_dismissed_count_for_reporter(app: Flask, make_user) -> None:
    """The credibility chip on the admin queue is driven by this
    count. Verify it only counts DISMISSED rows (not open, not
    actioned)."""
    reporter_d = make_user(username="r", role=Role.USER)
    target_d = make_user(username="t", role=Role.USER)
    admin_d = make_user(username="adm", role=Role.ADMIN)
    with app.app_context():
        reporter = db.session.get(User, reporter_d["id"])
        admin = db.session.get(User, admin_d["id"])

        # Open report — should NOT count.
        reports_service.file_report(
            reporter=reporter, reported_user_id=target_d["id"], reason="o"
        )
        # Actioned report — should NOT count.
        actioned = reports_service.file_report(
            reporter=reporter, reported_user_id=target_d["id"], reason="a"
        )
        reports_service.mark_actioned(actioned.id, actor=admin)
        # Two dismissed reports — should count.
        d1 = reports_service.file_report(
            reporter=reporter, reported_user_id=target_d["id"], reason="d1"
        )
        d2 = reports_service.file_report(
            reporter=reporter, reported_user_id=target_d["id"], reason="d2"
        )
        reports_service.dismiss(d1.id, actor=admin)
        reports_service.dismiss(d2.id, actor=admin)

        assert (
            reports_service.dismissed_count_for_reporter(reporter_d["id"])
            == 2
        )


# ─── HTTP layer ──────────────────────────────────────────────────


def test_file_report_route_login_required(
    client: FlaskClient, make_user
) -> None:
    """Anonymous visitors can't file reports — auth-required keeps
    drive-by abuse out."""
    make_user(username="target", role=Role.USER)
    resp = client.post(
        "/profiles/target/report",
        data={"reason": "anonymous report"},
        follow_redirects=False,
    )
    # flask-login redirects to login for an unauthenticated POST.
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_file_report_route_happy_path(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="reporter", role=Role.USER)
    make_user(username="target", role=Role.USER)
    _login(client, "reporter")
    resp = client.post(
        "/profiles/target/report",
        data={"reason": "they were rude"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        rows = list(db.session.scalars(db.select(Report)))
        assert len(rows) == 1
        assert rows[0].reason == "they were rude"


def test_admin_queue_lists_open_reports(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="reporter", role=Role.USER)
    make_user(username="target", role=Role.USER)
    make_user(username="adm", role=Role.ADMIN)
    _login(client, "reporter")
    client.post(
        "/profiles/target/report",
        data={"reason": "needs admin attention"},
    )
    client.post("/auth/logout")
    _login(client, "adm")
    resp = client.get("/admin/reports")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "needs admin attention" in body
    assert "@reporter" in body
    assert "@target" in body


def test_admin_queue_shows_dismissed_chip(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """A reporter with prior dismissals gets a credibility chip on
    their new report. Verifies the queue UI surfaces the count."""
    rep_d = make_user(username="bad_reporter", role=Role.USER)
    target_d = make_user(username="target", role=Role.USER)
    admin_d = make_user(username="adm", role=Role.ADMIN)

    # File + dismiss one prior report so the chip shows on the NEXT.
    with app.app_context():
        reporter = db.session.get(User, rep_d["id"])
        admin = db.session.get(User, admin_d["id"])
        old = reports_service.file_report(
            reporter=reporter,
            reported_user_id=target_d["id"],
            reason="old report",
        )
        reports_service.dismiss(old.id, actor=admin)

    _login(client, "bad_reporter")
    client.post(
        "/profiles/target/report",
        data={"reason": "second report"},
    )
    client.post("/auth/logout")
    _login(client, "adm")
    resp = client.get("/admin/reports")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "1 prior dismissed" in body


def test_admin_action_and_dismiss_routes_require_admin(
    client: FlaskClient, make_user
) -> None:
    """Regular users can't reach the resolve endpoints — the role
    floor is the policy gate."""
    make_user(username="alice", role=Role.USER)
    _login(client, "alice")
    resp = client.post("/admin/reports/1/action", data={})
    assert resp.status_code == 403
    resp = client.post("/admin/reports/1/dismiss", data={})
    assert resp.status_code == 403
