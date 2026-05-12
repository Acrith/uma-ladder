"""PR-Q4 — invite-code anti-spam gate tests.

Three rings:
- Service unit tests for the invite_codes + app_settings layers.
- Auth route integration: /auth/register with the gate on/off, with
  valid/invalid/exhausted/revoked codes.
- Admin route integration: minting, revoking, toggling.

OAuth callback paths aren't exercised here — they rely on the same
service primitives (validate / consume) which are covered, and full
OAuth round-trip tests live alongside the existing oauth tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import InviteCode, InviteCodeUse, Role, User
from uma_ladder.services import app_settings as settings_service
from uma_ladder.services import invite_codes as ic

# ─── Helpers ─────────────────────────────────────────────────────


def _mint(
    *,
    max_uses: int | None = 1,
    label: str | None = None,
    actor_user_id: int | None = None,
) -> InviteCode:
    return ic.mint_code(
        ic.MintRequest(max_uses=max_uses, label=label),
        actor_user_id=actor_user_id,
    )


def _login_admin(app: Flask, client: FlaskClient, make_user) -> dict:
    admin = make_user(username="adm", role=Role.ADMIN)
    resp = client.post(
        "/auth/login",
        data={"username": admin["username"], "password": admin["password"]},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    return admin


# ─── Code generation + normalize ─────────────────────────────────


def test_generated_code_format(app: Flask) -> None:
    """Three 4-char groups separated by dashes, all from the readable
    alphabet (no 0/O/1/I/L)."""
    with app.app_context():
        code = ic._generate_code()
        parts = code.split("-")
        assert len(parts) == 3
        assert all(len(p) == 4 for p in parts)
        assert set(code.replace("-", "")).issubset(set(ic._ALPHABET))


def test_normalize_strips_dashes_and_uppercases(app: Flask) -> None:
    """Users who paste without dashes or in lowercase still match."""
    with app.app_context():
        row = _mint()
        bare = row.code.replace("-", "").lower()
        assert ic.validate_code(bare).id == row.id
        # With extra spaces too.
        assert ic.validate_code(f"  {row.code}  ").id == row.id


# ─── Mint / bulk_mint ────────────────────────────────────────────


def test_mint_creates_row(app: Flask) -> None:
    with app.app_context():
        row = _mint(label="hello")
        assert row.code
        assert row.max_uses == 1
        assert row.uses_count == 0
        assert row.label == "hello"
        assert row.revoked_at is None


def test_bulk_mint_creates_n_rows(app: Flask) -> None:
    with app.app_context():
        rows = ic.bulk_mint(
            ic.MintRequest(max_uses=1, label="club"),
            count=5,
            actor_user_id=None,
        )
        assert len(rows) == 5
        codes = {r.code for r in rows}
        assert len(codes) == 5  # all distinct
        assert all(r.label == "club" for r in rows)


def test_bulk_mint_rejects_zero(app: Flask) -> None:
    import pytest

    with app.app_context(), pytest.raises(ic.InviteCodeError):
        ic.bulk_mint(
            ic.MintRequest(), count=0, actor_user_id=None
        )


def test_bulk_mint_rejects_over_cap(app: Flask) -> None:
    import pytest

    with app.app_context(), pytest.raises(ic.InviteCodeError):
        ic.bulk_mint(
            ic.MintRequest(), count=101, actor_user_id=None
        )


# ─── Validate ────────────────────────────────────────────────────


def test_validate_not_found(app: Flask) -> None:
    import pytest

    with app.app_context(), pytest.raises(ic.InviteCodeNotFoundError):
        ic.validate_code("AAAA-BBBB-CCCC")


def test_validate_revoked(app: Flask) -> None:
    import pytest

    with app.app_context():
        row = _mint()
        ic.revoke_code(row.id)
        with pytest.raises(ic.InviteCodeRevokedError):
            ic.validate_code(row.code)


def test_validate_expired(app: Flask) -> None:
    import pytest

    with app.app_context():
        row = _mint()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.session.commit()
        with pytest.raises(ic.InviteCodeExpiredError):
            ic.validate_code(row.code)


def test_validate_exhausted(app: Flask) -> None:
    import pytest

    with app.app_context():
        row = _mint(max_uses=2)
        row.uses_count = 2
        db.session.commit()
        with pytest.raises(ic.InviteCodeExhaustedError):
            ic.validate_code(row.code)


def test_validate_unlimited_never_exhausts(app: Flask) -> None:
    """max_uses=None means unlimited — large uses_count still validates."""
    with app.app_context():
        row = _mint(max_uses=None)
        row.uses_count = 999
        db.session.commit()
        assert ic.validate_code(row.code).id == row.id


# ─── Consume ─────────────────────────────────────────────────────


def test_consume_increments_and_audits(app: Flask, make_user) -> None:
    target = make_user(username="newbie")
    with app.app_context():
        row = _mint(max_uses=3)
        ic.consume_code(row.code, user_id=target["id"], claimed_from_ip="1.2.3.4")
        row = db.session.get(InviteCode, row.id)
        assert row.uses_count == 1
        # Audit row exists and points at our user + IP.
        use = (
            db.session.query(InviteCodeUse)
            .filter_by(invite_code_id=row.id)
            .one()
        )
        assert use.user_id == target["id"]
        assert use.claimed_from_ip == "1.2.3.4"


def test_consume_last_seat_then_exhausted(app: Flask, make_user) -> None:
    """A code with max_uses=1: first consume works, second raises."""
    import pytest

    u1 = make_user(username="u1")
    u2 = make_user(username="u2")
    with app.app_context():
        row = _mint(max_uses=1)
        ic.consume_code(row.code, user_id=u1["id"], claimed_from_ip=None)
        with pytest.raises(ic.InviteCodeExhaustedError):
            ic.consume_code(row.code, user_id=u2["id"], claimed_from_ip=None)


def test_revoke_is_idempotent(app: Flask) -> None:
    with app.app_context():
        row = _mint()
        first = ic.revoke_code(row.id).revoked_at
        second = ic.revoke_code(row.id).revoked_at
        assert first == second  # second call didn't bump the timestamp


# ─── Settings service ────────────────────────────────────────────


def test_settings_default_off(app: Flask) -> None:
    with app.app_context():
        assert settings_service.is_invite_only_enabled() is False


def test_settings_round_trip(app: Flask) -> None:
    with app.app_context():
        settings_service.set_invite_only_enabled(True)
        assert settings_service.is_invite_only_enabled() is True
        settings_service.set_invite_only_enabled(False)
        assert settings_service.is_invite_only_enabled() is False


# ─── /auth/register integration ──────────────────────────────────


def test_register_with_gate_off_no_code_works(
    app: Flask, client: FlaskClient
) -> None:
    """Gate OFF — invite_code field is dormant, registration works."""
    resp = client.post(
        "/auth/register",
        data={
            "username": "openuser",
            "password": "password123",
            "confirm": "password123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert (
            db.session.query(User).filter_by(username="openuser").one()
        )


def test_register_with_gate_on_no_code_blocks(
    app: Flask, client: FlaskClient
) -> None:
    """Gate ON, no code submitted — form re-renders, no user created."""
    with app.app_context():
        settings_service.set_invite_only_enabled(True)
    resp = client.post(
        "/auth/register",
        data={
            "username": "blocked",
            "password": "password123",
            "confirm": "password123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200  # form re-render, not a redirect
    with app.app_context():
        assert (
            db.session.query(User).filter_by(username="blocked").first()
            is None
        )


def test_register_with_gate_on_valid_code_works(
    app: Flask, client: FlaskClient
) -> None:
    """Gate ON, valid code — registration succeeds AND consumes the code."""
    with app.app_context():
        settings_service.set_invite_only_enabled(True)
        row = _mint(max_uses=1)
        code_value = row.code
    resp = client.post(
        "/auth/register",
        data={
            "username": "invited",
            "password": "password123",
            "confirm": "password123",
            "invite_code": code_value,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert (
            db.session.query(User).filter_by(username="invited").one()
        )
        row = db.session.scalars(
            db.select(InviteCode).where(InviteCode.code == code_value)
        ).one()
        assert row.uses_count == 1


def test_register_with_gate_on_bad_code_blocks(
    app: Flask, client: FlaskClient
) -> None:
    """Gate ON, code doesn't exist — re-render, no user."""
    with app.app_context():
        settings_service.set_invite_only_enabled(True)
    resp = client.post(
        "/auth/register",
        data={
            "username": "tryhard",
            "password": "password123",
            "confirm": "password123",
            "invite_code": "AAAA-BBBB-CCCC",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert (
            db.session.query(User).filter_by(username="tryhard").first()
            is None
        )


def test_register_with_gate_on_exhausted_code_blocks(
    app: Flask, client: FlaskClient, make_user
) -> None:
    """A single-use code already consumed — next signup rejected."""
    first = make_user(username="first")
    with app.app_context():
        settings_service.set_invite_only_enabled(True)
        row = _mint(max_uses=1)
        ic.consume_code(row.code, user_id=first["id"], claimed_from_ip=None)
        code_value = row.code
    resp = client.post(
        "/auth/register",
        data={
            "username": "second",
            "password": "password123",
            "confirm": "password123",
            "invite_code": code_value,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert (
            db.session.query(User).filter_by(username="second").first()
            is None
        )


def test_register_with_gate_on_revoked_code_blocks(
    app: Flask, client: FlaskClient
) -> None:
    """Revoked code can't onboard anyone."""
    with app.app_context():
        settings_service.set_invite_only_enabled(True)
        row = _mint()
        ic.revoke_code(row.id)
        code_value = row.code
    resp = client.post(
        "/auth/register",
        data={
            "username": "blocked2",
            "password": "password123",
            "confirm": "password123",
            "invite_code": code_value,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert (
            db.session.query(User).filter_by(username="blocked2").first()
            is None
        )


# ─── Admin route surface ─────────────────────────────────────────


def test_admin_invite_codes_page_requires_admin(
    app: Flask, client: FlaskClient
) -> None:
    """Anonymous visitor → 401."""
    resp = client.get("/admin/invite-codes", follow_redirects=False)
    assert resp.status_code == 401


def test_admin_invite_codes_page_renders(
    app: Flask, client: FlaskClient, make_user
) -> None:
    _login_admin(app, client, make_user)
    resp = client.get("/admin/invite-codes")
    assert resp.status_code == 200
    assert b"Invite codes" in resp.data


def test_admin_toggle_flips_flag(
    app: Flask, client: FlaskClient, make_user
) -> None:
    _login_admin(app, client, make_user)
    with app.app_context():
        assert settings_service.is_invite_only_enabled() is False
    resp = client.post(
        "/admin/invite-codes/toggle", follow_redirects=False
    )
    assert resp.status_code == 302
    with app.app_context():
        assert settings_service.is_invite_only_enabled() is True
    # Flip back.
    client.post("/admin/invite-codes/toggle", follow_redirects=False)
    with app.app_context():
        assert settings_service.is_invite_only_enabled() is False


def test_admin_mint_creates_codes(
    app: Flask, client: FlaskClient, make_user
) -> None:
    _login_admin(app, client, make_user)
    resp = client.post(
        "/admin/invite-codes/mint",
        data={"max_uses": "5", "count": "3", "label": "club X"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        codes = (
            db.session.query(InviteCode)
            .filter_by(label="club X")
            .all()
        )
        assert len(codes) == 3
        assert all(c.max_uses == 5 for c in codes)


def test_admin_revoke_revokes(
    app: Flask, client: FlaskClient, make_user
) -> None:
    _login_admin(app, client, make_user)
    with app.app_context():
        row = _mint()
        code_id = row.id
    resp = client.post(
        f"/admin/invite-codes/{code_id}/revoke", follow_redirects=False
    )
    assert resp.status_code == 302
    with app.app_context():
        row = db.session.get(InviteCode, code_id)
        assert row.revoked_at is not None
