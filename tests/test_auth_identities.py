"""Service-layer tests for ``services.auth_identities`` (PR-K1).

Covers find/link/unlink + create-from-OAuth + username collision
suffixing. Route-layer + transport-layer tests live in
``test_oauth_routes.py`` and ``test_oauth_provider.py``.
"""

from __future__ import annotations

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import AuthIdentity, UserProfile
from uma_ladder.services import auth_identities as svc
from uma_ladder.services.auth import RegistrationRequest, register_user
from uma_ladder.services.oauth import ProviderProfile


def _profile(
    *,
    provider: str = "discord",
    external_id: str = "100000000000000001",
    username: str | None = "alice",
    email: str | None = None,
    avatar_url: str | None = None,
) -> ProviderProfile:
    return ProviderProfile(
        provider=provider,
        external_id=external_id,
        external_username=username,
        email=email,
        avatar_url=avatar_url,
    )


# ─── find_identity ───────────────────────────────────────────────


def test_find_identity_returns_none_when_missing(app: Flask) -> None:
    with app.app_context():
        assert svc.find_identity("discord", "1") is None


def test_find_identity_returns_row_when_present(app: Flask) -> None:
    with app.app_context():
        user = register_user(RegistrationRequest(username="bob", password="password123"))
        svc.link_identity(user, _profile(external_id="42"))
        found = svc.find_identity("discord", "42")
        assert found is not None
        assert found.user_id == user.id


# ─── link_identity ───────────────────────────────────────────────


def test_link_identity_creates_row_and_mirrors_to_profile(app: Flask) -> None:
    with app.app_context():
        user = register_user(RegistrationRequest(username="carol", password="password123"))
        svc.link_identity(
            user,
            _profile(external_id="555", username="carol_disc"),
        )
        identity = svc.find_identity("discord", "555")
        assert identity is not None
        assert identity.external_username == "carol_disc"

        # Mirroring to UserProfile so existing `<@id>` mention infra
        # keeps working without a manual profile-edit step.
        profile = db.session.query(UserProfile).filter_by(user_id=user.id).one()
        assert profile.discord_user_id == "555"
        assert profile.discord_handle == "carol_disc"


def test_link_identity_refuses_to_steal_from_other_user(app: Flask) -> None:
    with app.app_context():
        owner = register_user(RegistrationRequest(username="owner", password="password123"))
        intruder = register_user(RegistrationRequest(username="intr", password="password123"))
        svc.link_identity(owner, _profile(external_id="900"))
        with pytest.raises(svc.IdentityAlreadyLinkedError):
            svc.link_identity(intruder, _profile(external_id="900"))


def test_link_identity_idempotent_for_same_user_refreshes_fields(
    app: Flask,
) -> None:
    with app.app_context():
        user = register_user(RegistrationRequest(username="dave", password="password123"))
        svc.link_identity(
            user, _profile(external_id="42", username="old_name")
        )
        # User changed their Discord display name; relinking should
        # update the cached username, not error or duplicate.
        svc.link_identity(
            user, _profile(external_id="42", username="new_name")
        )
        identity = svc.find_identity("discord", "42")
        assert identity is not None
        assert identity.external_username == "new_name"
        rows = db.session.query(AuthIdentity).filter_by(user_id=user.id).all()
        assert len(rows) == 1


# ─── unlink_identity ─────────────────────────────────────────────


def test_unlink_identity_removes_row(app: Flask) -> None:
    with app.app_context():
        user = register_user(RegistrationRequest(username="eve", password="password123"))
        svc.link_identity(user, _profile(external_id="111"))
        assert svc.unlink_identity(user, "discord") is True
        assert svc.find_identity("discord", "111") is None


def test_unlink_identity_returns_false_when_nothing_to_unlink(
    app: Flask,
) -> None:
    with app.app_context():
        user = register_user(RegistrationRequest(username="frank", password="password123"))
        assert svc.unlink_identity(user, "discord") is False


# ─── create_user_for_oauth ───────────────────────────────────────


def test_create_user_for_oauth_creates_user_identity_and_profile(
    app: Flask,
) -> None:
    with app.app_context():
        user = svc.create_user_for_oauth(
            _profile(external_id="9001", username="newcomer")
        )
        assert user.id is not None
        assert user.username == "newcomer"

        # Identity row created
        identity = svc.find_identity("discord", "9001")
        assert identity is not None
        assert identity.user_id == user.id

        # UserProfile created with verified discord fields
        profile = db.session.query(UserProfile).filter_by(user_id=user.id).one()
        assert profile.discord_user_id == "9001"
        assert profile.discord_handle == "newcomer"


def test_create_user_for_oauth_suffixes_on_username_collision(
    app: Flask,
) -> None:
    with app.app_context():
        # Pre-existing local user with the same name we'd derive
        register_user(RegistrationRequest(username="taken", password="password123"))
        new_user = svc.create_user_for_oauth(
            _profile(external_id="42", username="taken")
        )
        assert new_user.username != "taken"
        assert new_user.username.startswith("taken_")
        # Suffix is 4 hex chars per spec (`taken_xxxx`)
        assert len(new_user.username) == len("taken") + 1 + 4


def test_create_user_for_oauth_falls_back_when_username_unsanitizable(
    app: Flask,
) -> None:
    """If the OAuth display name is e.g. all-Japanese (sanitizes to
    empty), we fall back to a provider-prefixed slice of the
    external_id rather than create an empty username."""
    with app.app_context():
        user = svc.create_user_for_oauth(
            _profile(external_id="123456789012345678", username="日本語")
        )
        assert user.username.startswith("d")  # discord prefix
        assert "123456789012345678" in user.username


def test_create_user_for_oauth_password_is_unguessable(app: Flask) -> None:
    """OAuth-only users get a random password they cannot know.
    Sanity-check: the random password we generate is not one of
    the obvious "weak default" values."""
    with app.app_context():
        user = svc.create_user_for_oauth(
            _profile(external_id="42", username="alice")
        )
        for guess in ("", "password", "password123", "discord", str(user.id)):
            assert not user.check_password(guess), (
                f"unguessable random password matched {guess!r}"
            )


# ─── touch_login ─────────────────────────────────────────────────


def test_touch_login_stamps_last_login(app: Flask) -> None:
    with app.app_context():
        user = register_user(RegistrationRequest(username="grace", password="password123"))
        identity = svc.link_identity(user, _profile(external_id="42"))
        assert identity.last_login_at is None
        svc.touch_login(identity)
        assert identity.last_login_at is not None


# ─── model uniqueness ────────────────────────────────────────────


def test_unique_constraint_on_provider_external_id(app: Flask) -> None:
    """Two identity rows with the same (provider, external_id)
    must not be insertable — the unique index guards against
    accidental double-link via race or admin-tooling bug."""
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        user_a = register_user(RegistrationRequest(username="hank", password="password123"))
        user_b = register_user(RegistrationRequest(username="ivy", password="password123"))
        db.session.add(
            AuthIdentity(user_id=user_a.id, provider="discord", external_id="42")
        )
        db.session.commit()
        db.session.add(
            AuthIdentity(user_id=user_b.id, provider="discord", external_id="42")
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
