"""Route-layer tests for ``/auth/discord/{start,callback}`` (PR-K2).

Validates the state-CSRF + PKCE wiring, the four resolution
branches in the callback (login-existing, create-new,
link-to-current-user, identity-belongs-to-other-user), and that
the routes are gated when Discord OAuth isn't configured.

Uses a FakeHttpTransport injected via the app extensions registry
to drive Discord's responses without touching the network.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import User, UserProfile
from uma_ladder.services import auth_identities as identity_service
from uma_ladder.services.auth import RegistrationRequest, register_user
from uma_ladder.services.oauth import FakeHttpTransport, ProviderProfile

# ─── shared fixtures / helpers ───────────────────────────────────


@pytest.fixture
def configured_app(app: Flask) -> Iterator[Flask]:
    """Same app fixture but with Discord OAuth env populated and a
    FakeHttpTransport pre-registered so routes use it instead of
    UrllibHttpTransport."""
    app.config["DISCORD_OAUTH_CLIENT_ID"] = "test-client-id"
    app.config["DISCORD_OAUTH_CLIENT_SECRET"] = "test-client-secret"
    transport = FakeHttpTransport()
    app.extensions["uma_ladder.oauth_http_transport"] = transport
    yield app
    app.extensions.pop("uma_ladder.oauth_http_transport", None)


def _transport(app: Flask) -> FakeHttpTransport:
    return app.extensions["uma_ladder.oauth_http_transport"]


def _script_discord(
    app: Flask,
    *,
    external_id: str,
    username: str | None = "alice",
    global_name: str | None = None,
    avatar: str | None = None,
    access_token: str = "AT",
    token_status: int = 200,
    profile_status: int = 200,
) -> None:
    transport = _transport(app)
    transport.script(
        method="POST", url_substring="oauth2/token",
        status_code=token_status,
        body=json.dumps({"access_token": access_token, "token_type": "Bearer"}),
    )
    body = {"id": external_id}
    if username is not None:
        body["username"] = username
    if global_name is not None:
        body["global_name"] = global_name
    if avatar is not None:
        body["avatar"] = avatar
    transport.script(
        method="GET", url_substring="users/@me",
        status_code=profile_status,
        body=json.dumps(body),
    )


def _seed_session_state(client: FlaskClient, *, state: str, verifier: str) -> None:
    with client.session_transaction() as sess:
        sess["oauth_discord_state"] = state
        sess["oauth_discord_verifier"] = verifier


def _login_password(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ─── /auth/discord/start ─────────────────────────────────────────


def test_start_404_when_oauth_not_configured(client: FlaskClient) -> None:
    """The button is hidden in templates when OAuth is unconfigured,
    but the route itself must also refuse — defense against URL-
    typing visitors and old links from before Discord was wired up."""
    resp = client.get("/auth/discord/start")
    assert resp.status_code == 404


def test_start_redirects_to_discord_with_pkce_and_state(
    configured_app: Flask, client: FlaskClient
) -> None:
    resp = client.get("/auth/discord/start")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("https://discord.com/oauth2/authorize?")
    qs = urllib.parse.parse_qs(location.split("?", 1)[1])
    assert qs["client_id"] == ["test-client-id"]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["identify"]
    assert qs["code_challenge_method"] == ["S256"]

    # State + verifier should land in session for the callback to
    # validate against; assert presence rather than equality so the
    # test doesn't depend on token randomness.
    with client.session_transaction() as sess:
        assert sess.get("oauth_discord_state")
        assert sess.get("oauth_discord_verifier")
        # The state propagated to the redirect URL must equal the
        # one stashed in the session — that's the whole CSRF gate.
        assert qs["state"] == [sess["oauth_discord_state"]]


# ─── /auth/discord/callback — login as existing identity ────────


def test_callback_logs_in_when_identity_exists(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="ada", password="password123")
        )
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="discord",
                external_id="111000111000",
                external_username="ada",
            ),
        )

    _seed_session_state(client, state="STATE-X", verifier="VERIFIER-Y")
    _script_discord(configured_app, external_id="111000111000", username="ada")

    resp = client.get(
        "/auth/discord/callback?state=STATE-X&code=CODE-X",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")  # dashboard

    # Subsequent request should be authenticated; /auth/login redirects
    # an authenticated user back to the dashboard.
    resp_after = client.get("/auth/login", follow_redirects=False)
    assert resp_after.status_code == 302


# ─── /auth/discord/callback — create-new ─────────────────────────


def test_callback_creates_user_when_no_identity(
    configured_app: Flask, client: FlaskClient
) -> None:
    _seed_session_state(client, state="S", verifier="V")
    _script_discord(
        configured_app,
        external_id="555444333222",
        username="brand_new",
        avatar="ah",
    )
    resp = client.get(
        "/auth/discord/callback?state=S&code=C", follow_redirects=False
    )
    assert resp.status_code == 302

    with configured_app.app_context():
        identity = identity_service.find_identity("discord", "555444333222")
        assert identity is not None
        user = db.session.get(User, identity.user_id)
        assert user is not None
        assert user.username == "brand_new"
        # UserProfile was created with verified Discord fields
        profile = db.session.query(UserProfile).filter_by(user_id=user.id).one()
        assert profile.discord_user_id == "555444333222"


# ─── /auth/discord/callback — link to current user ──────────────


def test_callback_links_identity_to_logged_in_user(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        register_user(
            RegistrationRequest(username="cleo", password="password123")
        )
    _login_password(client, "cleo", "password123")

    _seed_session_state(client, state="S", verifier="V")
    _script_discord(
        configured_app, external_id="777888999000", username="cleo_disc"
    )
    resp = client.get(
        "/auth/discord/callback?state=S&code=C", follow_redirects=False
    )
    assert resp.status_code == 302
    # Link flow lands on the user's public profile, not the dashboard
    assert "/profiles/cleo" in resp.headers["Location"]

    with configured_app.app_context():
        identity = identity_service.find_identity("discord", "777888999000")
        assert identity is not None
        # Linked to cleo, not a freshly-created account
        user = db.session.get(User, identity.user_id)
        assert user is not None
        assert user.username == "cleo"


def test_callback_refuses_to_steal_identity_from_other_user(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        owner = register_user(
            RegistrationRequest(username="owner", password="password123")
        )
        identity_service.link_identity(
            owner,
            ProviderProfile(
                provider="discord",
                external_id="222",
                external_username="owner_disc",
            ),
        )
        register_user(
            RegistrationRequest(username="intruder", password="password123")
        )
    _login_password(client, "intruder", "password123")

    _seed_session_state(client, state="S", verifier="V")
    _script_discord(configured_app, external_id="222", username="owner_disc")
    resp = client.get(
        "/auth/discord/callback?state=S&code=C", follow_redirects=False
    )
    assert resp.status_code == 302

    with configured_app.app_context():
        # Owner still owns the identity; nothing got reassigned.
        identity = identity_service.find_identity("discord", "222")
        assert identity is not None
        user = db.session.get(User, identity.user_id)
        assert user is not None
        assert user.username == "owner"


# ─── State / CSRF guards ─────────────────────────────────────────


def test_callback_400_on_state_mismatch(
    configured_app: Flask, client: FlaskClient
) -> None:
    _seed_session_state(client, state="GENUINE", verifier="V")
    # No discord HTTP calls should fire — the bad state must short-
    # circuit before exchange. Don't script any responses; if the
    # route were to call out, FakeHttpTransport would raise.
    resp = client.get(
        "/auth/discord/callback?state=ATTACKER&code=C",
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_callback_redirects_to_login_when_session_state_missing(
    configured_app: Flask, client: FlaskClient
) -> None:
    """No state in session = stale URL or new tab. We don't 400 (the
    user did nothing wrong) — we flash + send them back to login."""
    resp = client.get(
        "/auth/discord/callback?state=X&code=C", follow_redirects=False
    )
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_callback_handles_discord_error_param(
    configured_app: Flask, client: FlaskClient
) -> None:
    """User clicked Cancel on Discord's consent screen → Discord
    sends ``?error=access_denied``. We surface a clean message
    instead of trying to exchange a missing code."""
    resp = client.get(
        "/auth/discord/callback?error=access_denied&error_description=user+cancelled",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_callback_session_state_is_consumed_one_time(
    configured_app: Flask, client: FlaskClient
) -> None:
    """After a successful callback the state + verifier should be
    gone from session — replaying the same callback URL must fail
    rather than re-trigger the flow with stale data."""
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="zoe", password="password123")
        )
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="zoe",
            ),
        )

    _seed_session_state(client, state="S", verifier="V")
    _script_discord(configured_app, external_id="42", username="zoe")
    client.get(
        "/auth/discord/callback?state=S&code=C", follow_redirects=False
    )

    with client.session_transaction() as sess:
        assert "oauth_discord_state" not in sess
        assert "oauth_discord_verifier" not in sess


# ─── Login template ──────────────────────────────────────────────


def test_login_page_shows_continue_with_discord_when_configured(
    configured_app: Flask, client: FlaskClient
) -> None:
    body = client.get("/auth/login").data.decode()
    assert "Continue with Discord" in body
    assert "/auth/discord/start" in body


def test_login_page_hides_discord_button_when_unconfigured(
    client: FlaskClient,
) -> None:
    body = client.get("/auth/login").data.decode()
    assert "Continue with Discord" not in body


# ─── /auth/discord/unlink (PR-K3) ────────────────────────────────


def test_unlink_removes_identity_and_clears_profile_field(
    configured_app: Flask, client: FlaskClient
) -> None:
    """Unlink drops the auth_identity row AND nulls
    UserProfile.discord_user_id (the snowflake there came from
    OAuth's overwrite, not from anything the user typed)."""
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="kim", password="password123")
        )
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="kim_disc",
            ),
        )
    _login_password(client, "kim", "password123")

    resp = client.post("/auth/discord/unlink", follow_redirects=False)
    assert resp.status_code == 302
    assert "/profiles/me" in resp.headers["Location"]

    with configured_app.app_context():
        assert identity_service.find_identity("discord", "42") is None
        # Mirror cleared so a stale snowflake doesn't keep firing
        # ping-enabled @ mentions for an account that's no longer
        # actually linked.
        profile = (
            db.session.query(UserProfile)
            .filter_by(user_id=db.session.query(User).filter_by(username="kim").one().id)
            .one()
        )
        assert profile.discord_user_id is None


def test_unlink_idempotent_when_no_identity(
    configured_app: Flask, client: FlaskClient
) -> None:
    """Clicking unlink twice (or unlinking when nothing's linked)
    flashes a benign message, not a 500."""
    with configured_app.app_context():
        register_user(
            RegistrationRequest(username="lou", password="password123")
        )
    _login_password(client, "lou", "password123")
    resp = client.post("/auth/discord/unlink", follow_redirects=False)
    assert resp.status_code == 302


def test_unlink_requires_login(client: FlaskClient) -> None:
    """Anonymous POST must not be able to unlink anything — Flask-
    Login redirects to /auth/login. We assert the redirect rather
    than a 403 because @login_required is the standard guard
    elsewhere in the app."""
    resp = client.post("/auth/discord/unlink", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


# ─── Verified-via-OAuth badge on public profile (PR-K3) ─────────


def test_public_profile_shows_verified_badge_when_oauth_linked(
    configured_app: Flask, client: FlaskClient
) -> None:
    """When the user has a Discord auth_identity, the public
    profile's Discord chip carries the bright ✓ verified glyph."""
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="mona", password="password123")
        )
        # link_identity also sets profile.discord_handle +
        # discord_user_id, which the badge logic depends on.
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="mona_disc",
            ),
        )

    body = client.get("/profiles/mona").data.decode()
    assert "mona_disc" in body  # handle is rendered
    assert "Verified" in body  # title attribute on the ✓ glyph


def test_public_profile_shows_at_glyph_for_manual_only(
    configured_app: Flask, client: FlaskClient
) -> None:
    """Existing @-pings-enabled glyph is preserved when the user
    typed their discord_user_id manually but never OAuth-linked.
    Regression: don't promote unverified entries to the verified
    visual."""
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="nash", password="password123")
        )
        from uma_ladder.services import profiles as profiles_service

        profile = profiles_service.get_or_create_profile(user)
        profile.discord_handle = "nash_typed"
        profile.discord_user_id = "999000111222333444"
        db.session.commit()

    body = client.get("/profiles/nash").data.decode()
    assert "nash_typed" in body
    # Manual-only sees the @ glyph + "manually" disclaimer in the
    # tooltip — never the cyan ✓.
    assert "manually" in body  # "ID was entered manually..." title
    assert "Verified" not in body or body.count("Verified") == 0


def test_public_profile_no_badge_glyph_when_no_discord_id(
    configured_app: Flask, client: FlaskClient
) -> None:
    """Display-only handle (no snowflake) renders the chip but
    neither glyph — nothing to ping, nothing to verify."""
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="opal", password="password123")
        )
        from uma_ladder.services import profiles as profiles_service

        profile = profiles_service.get_or_create_profile(user)
        profile.discord_handle = "opal_handle"
        # discord_user_id stays None
        db.session.commit()

    body = client.get("/profiles/opal").data.decode()
    assert "opal_handle" in body
    # Title-attribute strings tell us no glyph rendered.
    assert "Verified" not in body
    assert "Pings enabled" not in body


# ─── Linked-accounts card on profile editor (PR-K2 follow-up) ───


def test_profile_editor_shows_link_button_when_unlinked(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        register_user(
            RegistrationRequest(username="paula", password="password123")
        )
    _login_password(client, "paula", "password123")

    body = client.get("/profiles/me").data.decode()
    assert "Link Discord" in body
    assert "Unlink" not in body


def test_profile_editor_shows_unlink_button_when_linked(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="quinn", password="password123")
        )
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="quinn_disc",
            ),
        )
    _login_password(client, "quinn", "password123")

    body = client.get("/profiles/me").data.decode()
    assert "✓ Linked" in body
    assert "Unlink" in body
    assert "/auth/discord/unlink" in body
