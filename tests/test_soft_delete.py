"""PR-Q3a — soft-delete (account disable) tests.

Covers the service guards + cascade behaviour, the masked_display
helper that hides a soft-deleted user's handle in historical
contexts, login refusal, Players-index filter, and the
superadmin-gated admin disable/enable routes.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import AuthIdentity, User, UserProfile
from uma_ladder.models.users import Role
from uma_ladder.services import admin as admin_service
from uma_ladder.services import auth as auth_service
from uma_ladder.services import auth_identities as identity_service
from uma_ladder.services import profiles as profiles_service
from uma_ladder.services.auth import RegistrationRequest, register_user
from uma_ladder.services.oauth import ProviderProfile


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ─── Service layer ───────────────────────────────────────────────


def test_soft_delete_blanks_profile(app: Flask, make_user) -> None:
    """The profile fields that surface publicly all clear to NULL.
    Race / draft history rows survive (FKs preserved)."""
    super_a = make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)  # quorum
    target = make_user(username="goner", role=Role.USER)
    with app.app_context():
        actor = db.session.get(User, super_a["id"])
        t = db.session.get(User, target["id"])
        # Populate the profile with public-leak fields.
        profile = profiles_service.get_or_create_profile(t)
        profile.display_name = "Goner McGee"
        profile.friend_code = "1234-5678-9012"
        profile.discord_handle = "goner#1234"
        profile.discord_user_id = "987654321"
        db.session.commit()

        admin_service.soft_delete_user(actor=actor, target=t)

        refreshed = db.session.get(User, target["id"])
        assert refreshed is not None  # row still exists
        assert refreshed.disabled_at is not None
        p = refreshed.profile
        assert p is not None
        assert p.display_name is None
        assert p.friend_code is None
        assert p.discord_handle is None
        assert p.discord_user_id is None
        assert p.oshi_character_id is None


def test_soft_delete_removes_oauth_identities(
    app: Flask, make_user
) -> None:
    """OAuth identities drop so the external_id is free to bind to
    another account — the original-account-recovery flow after
    cleaning up a Discord-OAuth duplicate."""
    super_a = make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    target = make_user(username="goner", role=Role.USER)
    with app.app_context():
        t = db.session.get(User, target["id"])
        identity_service.link_identity(
            t,
            ProviderProfile(
                provider="discord",
                external_id="555",
                external_username="goner_disc",
            ),
        )
        assert (
            db.session.query(AuthIdentity).filter_by(user_id=t.id).count()
            == 1
        )

        actor = db.session.get(User, super_a["id"])
        admin_service.soft_delete_user(actor=actor, target=t)

        assert (
            db.session.query(AuthIdentity).filter_by(user_id=target["id"]).count()
            == 0
        )


def test_soft_delete_scrambles_password(app: Flask, make_user) -> None:
    """The original password no longer authenticates — defence in
    depth against the disabled_at check being bypassed by future
    code changes."""
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    target = make_user(username="goner", role=Role.USER, password="oldpass1234")
    with app.app_context():
        actor = db.session.scalars(
            db.select(User).where(User.username == "sup")
        ).first()
        t = db.session.get(User, target["id"])
        admin_service.soft_delete_user(actor=actor, target=t)

        refreshed = db.session.get(User, target["id"])
        # Even if we bypassed the disabled_at check (e.g. a bug),
        # the old password can't reach the user — the hash is
        # scrambled.
        assert not refreshed.check_password("oldpass1234")


def test_soft_delete_blocks_self(app: Flask, make_user) -> None:
    only_super = make_user(username="lone", role=Role.SUPERADMIN)
    with app.app_context():
        u = db.session.get(User, only_super["id"])
        with pytest.raises(admin_service.CannotEditSelfError):
            admin_service.soft_delete_user(actor=u, target=u)
        assert db.session.get(User, only_super["id"]).disabled_at is None


def test_soft_delete_blocks_last_superadmin(app: Flask, make_user) -> None:
    target = make_user(username="onlysup", role=Role.SUPERADMIN)
    helper = make_user(username="adm_helper", role=Role.ADMIN)
    with app.app_context():
        actor = db.session.get(User, helper["id"])
        db.session.expunge(actor)
        actor.role = Role.SUPERADMIN
        t = db.session.get(User, target["id"])
        with pytest.raises(admin_service.LastSuperadminError):
            admin_service.soft_delete_user(actor=actor, target=t)
        assert db.session.get(User, target["id"]).disabled_at is None


def test_restore_user_clears_disabled_at(app: Flask, make_user) -> None:
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    target = make_user(username="goner", role=Role.USER)
    with app.app_context():
        actor = db.session.scalars(
            db.select(User).where(User.username == "sup")
        ).first()
        t = db.session.get(User, target["id"])
        admin_service.soft_delete_user(actor=actor, target=t)
        assert db.session.get(User, target["id"]).disabled_at is not None
        admin_service.restore_user(actor=actor, target=t)
        assert db.session.get(User, target["id"]).disabled_at is None


# ─── masked_display_for ──────────────────────────────────────────


def test_masked_display_active_user_returns_display_name(
    app: Flask, make_user
) -> None:
    target = make_user(username="alice", role=Role.USER)
    with app.app_context():
        u = db.session.get(User, target["id"])
        profile = profiles_service.get_or_create_profile(u)
        profile.display_name = "Alice McMane"
        db.session.commit()
        assert profiles_service.masked_display_for(u) == "Alice McMane"


def test_masked_display_active_no_display_name_falls_back_to_username(
    app: Flask, make_user
) -> None:
    target = make_user(username="alice", role=Role.USER)
    with app.app_context():
        u = db.session.get(User, target["id"])
        # No profile mutation — display_name stays NULL.
        assert profiles_service.masked_display_for(u) == "alice"


def test_masked_display_disabled_user_returns_partial_mask(
    app: Flask, make_user
) -> None:
    """`kezuke` → `k***e` — fixed asterisk count (3) so we don't
    leak username length, but preserve first + last letters for
    context. Mask is always 5 chars total — shorter than any
    non-trivial username, so it never blows out UI columns."""
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    target = make_user(username="kezuke", role=Role.USER)
    with app.app_context():
        actor = db.session.scalars(
            db.select(User).where(User.username == "sup")
        ).first()
        t = db.session.get(User, target["id"])
        admin_service.soft_delete_user(actor=actor, target=t)
        refreshed = db.session.get(User, target["id"])
        masked = profiles_service.masked_display_for(refreshed)
        assert masked.startswith("k")
        assert masked.endswith("e")
        assert masked.count("*") == 3
        # Length is independent of username length.
        assert len(masked) == 5  # 1 + 3 + 1


# ─── Login refusal ───────────────────────────────────────────────


def test_login_refused_for_disabled_user(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    make_user(username="goner", role=Role.USER, password="password123")
    with app.app_context():
        actor = db.session.scalars(
            db.select(User).where(User.username == "sup")
        ).first()
        t = db.session.scalars(
            db.select(User).where(User.username == "goner")
        ).first()
        admin_service.soft_delete_user(actor=actor, target=t)

    # Even if the password hash were somehow still valid, the
    # disabled_at guard blocks login (and the password was
    # scrambled — this also asserts the scramble worked).
    resp = client.post(
        "/auth/login",
        data={"username": "goner", "password": "password123"},
        follow_redirects=False,
    )
    # Goes back to the login form (200) with an error, NOT 302.
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data


# ─── Players index filter ────────────────────────────────────────


def test_players_index_hides_disabled_users(app: Flask, make_user) -> None:
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    make_user(username="kept", role=Role.USER)
    make_user(username="hiddenuser", role=Role.USER)
    with app.app_context():
        actor = db.session.scalars(
            db.select(User).where(User.username == "sup")
        ).first()
        hidden = db.session.scalars(
            db.select(User).where(User.username == "hiddenuser")
        ).first()
        admin_service.soft_delete_user(actor=actor, target=hidden)

        page = profiles_service.list_players(page=1, page_size=30)
        usernames = {u.username for u, _profile in page.rows}
        assert "kept" in usernames
        assert "hiddenuser" not in usernames


# ─── Admin routes ────────────────────────────────────────────────


def test_disable_route_requires_superadmin(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="adm", role=Role.ADMIN)
    target = make_user(username="goner", role=Role.USER)
    _login(client, "adm")
    resp = client.post(
        f"/admin/users/{target['id']}/disable",
        data={"confirm_username": "goner"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    with app.app_context():
        assert db.session.get(User, target["id"]).disabled_at is None


def test_disable_route_rejects_wrong_confirm(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    target = make_user(username="goner", role=Role.USER)
    _login(client, "sup")
    resp = client.post(
        f"/admin/users/{target['id']}/disable",
        data={"confirm_username": "wrong"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(User, target["id"]).disabled_at is None


def test_disable_route_happy_path_and_enable(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    target = make_user(username="goner", role=Role.USER)
    _login(client, "sup")
    # Disable
    resp = client.post(
        f"/admin/users/{target['id']}/disable",
        data={"confirm_username": "goner"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(User, target["id"]).disabled_at is not None
    # Enable (no confirmation field — restore is reversible by
    # design and lower-risk than disable)
    resp = client.post(
        f"/admin/users/{target['id']}/enable",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(User, target["id"]).disabled_at is None


def test_public_profile_404s_for_disabled_user(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="sup", role=Role.SUPERADMIN)
    make_user(username="sup2", role=Role.SUPERADMIN)
    make_user(username="goner", role=Role.USER)
    with app.app_context():
        actor = db.session.scalars(
            db.select(User).where(User.username == "sup")
        ).first()
        t = db.session.scalars(
            db.select(User).where(User.username == "goner")
        ).first()
        admin_service.soft_delete_user(actor=actor, target=t)
    resp = client.get("/profiles/goner")
    assert resp.status_code == 404
