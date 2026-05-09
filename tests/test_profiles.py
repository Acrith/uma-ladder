from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import UmaCharacter
from uma_ladder.services import profiles as profiles_service


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _make_character(app: Flask, slug: str = "special-week") -> int:
    with app.app_context():
        c = UmaCharacter(slug=slug, name_en=slug.replace("-", " ").title())
        db.session.add(c)
        db.session.commit()
        return c.id


def test_me_requires_login(client: FlaskClient) -> None:
    resp = client.get("/profiles/me", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_owner_can_edit_own_profile(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", password="password123")
    oshi_id = _make_character(app)
    _login(client, "alice", "password123")

    resp = client.post(
        "/profiles/me",
        data={
            "display_name": "Alice in Wonderland",
            "friend_code": "1234-5678",
            "description": "Hello there",
            "oshi_character_id": str(oshi_id),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        assert user is not None
        profile = profiles_service.get_or_create_profile(user)
        assert profile.display_name == "Alice in Wonderland"
        assert profile.friend_code == "1234-5678"
        assert profile.oshi_character_id == oshi_id


def test_unknown_oshi_is_rejected(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.post(
        "/profiles/me",
        data={"oshi_character_id": "9999"},
    )
    assert resp.status_code == 200
    assert b"Unknown character." in resp.data


def test_public_profile_renders(client: FlaskClient, app: Flask, make_user) -> None:
    make_user(username="alice", password="password123")
    oshi_id = _make_character(app, "gold-ship")
    _login(client, "alice", "password123")
    client.post(
        "/profiles/me",
        data={
            "display_name": "Alice",
            "description": "Bio body",
            "oshi_character_id": str(oshi_id),
        },
    )
    client.post("/auth/logout")

    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    assert b"Alice" in resp.data
    assert b"Bio body" in resp.data
    assert b"Gold Ship" in resp.data


def test_public_profile_404_for_unknown(client: FlaskClient) -> None:
    resp = client.get("/profiles/ghost")
    assert resp.status_code == 404


def test_get_or_create_profile_idempotent(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        user = profiles_service.find_user_by_username(info["username"])
        assert user is not None
        a = profiles_service.get_or_create_profile(user)
        b = profiles_service.get_or_create_profile(user)
        assert a.id == b.id


def test_update_profile_unknown_oshi_raises(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        user = profiles_service.find_user_by_username(info["username"])
        assert user is not None
        with pytest.raises(profiles_service.UnknownOshiError):
            profiles_service.update_profile(
                user, profiles_service.ProfileUpdate(oshi_character_id=9999)
            )


def test_nav_points_to_public_profile_not_edit(
    client: FlaskClient, make_user
) -> None:
    """Clicking the user's own name in the nav opens the public profile
    page, not the edit form. The edit form is reachable from the
    page's Edit affordance."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'href="/profiles/alice"' in body
    assert 'href="/profiles/me"' not in body


def test_public_profile_shows_edit_button_for_owner(
    client: FlaskClient, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Edit profile" in body
    assert 'href="/profiles/me"' in body


def test_public_profile_hides_edit_button_for_other_users(
    client: FlaskClient, make_user
) -> None:
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")
    _login(client, "bob", "password123")
    resp = client.get("/profiles/alice")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Edit profile" not in body


# ─── PR-K3.1 — discord_user_id locked when OAuth-linked ─────────


def _link_discord(app: Flask, username: str, *, external_id: str = "42") -> None:
    """Helper: attach a Discord identity to an existing user. Mirrors
    what /auth/discord/callback does on first link, including the
    `UserProfile.discord_user_id` mirror set by `link_identity`."""
    from uma_ladder.services import auth_identities as identity_service
    from uma_ladder.services.oauth import ProviderProfile

    with app.app_context():
        user = profiles_service.find_user_by_username(username)
        assert user is not None
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="discord",
                external_id=external_id,
                external_username=f"{username}_disc",
            ),
        )


def test_save_with_linked_discord_preserves_verified_mirror(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Disabled inputs aren't submitted by browsers, so a normal
    save would carry no value for discord_user_id and the route's
    naive update would clear the mirror. Server-side guard
    (PR-K3.1) preserves the verified value regardless."""
    make_user(username="alice", password="password123")
    _link_discord(app, "alice", external_id="100200300400500600")
    _login(client, "alice", "password123")

    resp = client.post(
        "/profiles/me",
        data={
            "display_name": "Alice Updated",
            # Note: NO discord_user_id field — mimics the disabled
            # input not being submitted.
            "discord_handle": "alice_handle",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        profile = profiles_service.get_or_create_profile(user)
        assert profile.display_name == "Alice Updated"
        # Mirror still equals the verified value, NOT cleared.
        assert profile.discord_user_id == "100200300400500600"


def test_save_with_linked_discord_ignores_spoofed_user_id(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """An attacker re-enables the disabled discord_user_id input
    via DevTools and submits a different snowflake (e.g. to
    redirect @-mentions to someone else's Discord). Server-side
    guard must reject this — the verified mirror stays."""
    make_user(username="alice", password="password123")
    _link_discord(app, "alice", external_id="100200300400500600")
    _login(client, "alice", "password123")

    resp = client.post(
        "/profiles/me",
        data={
            "display_name": "Alice",
            "discord_user_id": "999999999999999999",  # spoof attempt
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        profile = profiles_service.get_or_create_profile(user)
        # Spoof rejected — mirror still equals the verified id.
        assert profile.discord_user_id == "100200300400500600"


def test_save_without_linked_discord_updates_user_id_normally(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """No identity linked → field is editable, behaves as before."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")

    resp = client.post(
        "/profiles/me",
        data={
            "display_name": "Alice",
            "discord_user_id": "111222333444555666",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        profile = profiles_service.get_or_create_profile(user)
        assert profile.discord_user_id == "111222333444555666"


def test_editor_renders_disabled_field_when_linked(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    _link_discord(app, "alice")
    _login(client, "alice", "password123")
    body = client.get("/profiles/me").data.decode()
    assert "✓ Verified" in body
    assert "Verified via Discord OAuth" in body
    # The discord_user_id input carries `disabled` when linked.
    # Loose assertion — exact attribute order varies between
    # WTForms versions, but the substring is stable.
    assert "disabled" in body


def test_editor_renders_editable_field_when_unlinked(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    body = client.get("/profiles/me").data.decode()
    # Original help text is the marker — it's gone in the linked
    # variant.
    assert "Enable Discord Developer Mode" in body
    assert "Verified via Discord OAuth" not in body
