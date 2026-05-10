"""Route-layer tests for ``/auth/google/{start,callback,unlink}`` (PR-K4).

Mirrors test_oauth_routes.py's Discord coverage. Validates the
state-CSRF + PKCE wiring, the four resolution branches in the
callback (login-existing, create-new, link-to-current-user,
identity-belongs-to-other-user), and that the routes are gated
when Google OAuth isn't configured.

Uses a FakeHttpTransport injected via the app extensions registry
to drive Google's responses without touching the network.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import User
from uma_ladder.services import auth_identities as identity_service
from uma_ladder.services.auth import RegistrationRequest, register_user
from uma_ladder.services.oauth import FakeHttpTransport, ProviderProfile

# ─── shared fixtures / helpers ───────────────────────────────────


@pytest.fixture
def configured_app(app: Flask) -> Iterator[Flask]:
    """Same app fixture but with Google OAuth env populated and a
    FakeHttpTransport pre-registered so routes use it instead of
    UrllibHttpTransport."""
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = "test-google-client-id"
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = "test-google-client-secret"
    transport = FakeHttpTransport()
    app.extensions["uma_ladder.oauth_http_transport"] = transport
    yield app
    app.extensions.pop("uma_ladder.oauth_http_transport", None)


def _transport(app: Flask) -> FakeHttpTransport:
    return app.extensions["uma_ladder.oauth_http_transport"]


def _script_google(
    app: Flask,
    *,
    sub: str,
    name: str | None = "Alice Example",
    picture: str | None = None,
    access_token: str = "AT",
    token_status: int = 200,
    profile_status: int = 200,
) -> None:
    transport = _transport(app)
    transport.script(
        method="POST", url_substring="oauth2.googleapis.com/token",
        status_code=token_status,
        body=json.dumps({"access_token": access_token, "token_type": "Bearer"}),
    )
    body: dict[str, str] = {"sub": sub}
    if name is not None:
        body["name"] = name
    if picture is not None:
        body["picture"] = picture
    transport.script(
        method="GET", url_substring="userinfo",
        status_code=profile_status,
        body=json.dumps(body),
    )


def _seed_session_state(client: FlaskClient, *, state: str, verifier: str) -> None:
    with client.session_transaction() as sess:
        sess["oauth_google_state"] = state
        sess["oauth_google_verifier"] = verifier


def _login_password(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ─── /auth/google/start ──────────────────────────────────────────


def test_start_404_when_oauth_not_configured(client: FlaskClient) -> None:
    """The button is hidden in templates when OAuth is unconfigured,
    but the route itself must also refuse — defense against URL-
    typing visitors and old links from before Google was wired up."""
    resp = client.get("/auth/google/start")
    assert resp.status_code == 404


def test_start_redirects_to_google_with_pkce_and_state(
    configured_app: Flask, client: FlaskClient
) -> None:
    resp = client.get("/auth/google/start")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    qs = urllib.parse.parse_qs(location.split("?", 1)[1])
    assert qs["client_id"] == ["test-google-client-id"]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["openid profile"]
    assert qs["code_challenge_method"] == ["S256"]

    with client.session_transaction() as sess:
        assert sess.get("oauth_google_state")
        assert sess.get("oauth_google_verifier")
        assert qs["state"] == [sess["oauth_google_state"]]


# ─── /auth/google/callback — login as existing identity ─────────


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
                provider="google",
                external_id="100200300400500600700",
                external_username="Ada Example",
            ),
        )

    _seed_session_state(client, state="STATE-X", verifier="VERIFIER-Y")
    _script_google(
        configured_app, sub="100200300400500600700", name="Ada Example"
    )

    resp = client.get(
        "/auth/google/callback?state=STATE-X&code=CODE-X",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")  # dashboard

    resp_after = client.get("/auth/login", follow_redirects=False)
    assert resp_after.status_code == 302


# ─── /auth/google/callback — create-new ──────────────────────────


def test_callback_creates_user_when_no_identity(
    configured_app: Flask, client: FlaskClient
) -> None:
    """First-time Google sign-in derives a username from the display
    name and creates a fresh User. Unlike Discord, no UserProfile
    side-write happens (no google_subject column on UserProfile)."""
    _seed_session_state(client, state="S", verifier="V")
    _script_google(
        configured_app, sub="555444333222", name="brand new user"
    )
    resp = client.get(
        "/auth/google/callback?state=S&code=C", follow_redirects=False
    )
    assert resp.status_code == 302

    with configured_app.app_context():
        identity = identity_service.find_identity("google", "555444333222")
        assert identity is not None
        user = db.session.get(User, identity.user_id)
        assert user is not None
        # "brand new user" → sanitised to "brandnewuser"
        assert user.username == "brandnewuser"


# ─── /auth/google/callback — link to current user ───────────────


def test_callback_links_identity_to_logged_in_user(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        register_user(
            RegistrationRequest(username="cleo", password="password123")
        )
    _login_password(client, "cleo", "password123")

    _seed_session_state(client, state="S", verifier="V")
    _script_google(configured_app, sub="777888999000", name="Cleo Google")
    resp = client.get(
        "/auth/google/callback?state=S&code=C", follow_redirects=False
    )
    assert resp.status_code == 302
    assert "/profiles/cleo" in resp.headers["Location"]

    with configured_app.app_context():
        identity = identity_service.find_identity("google", "777888999000")
        assert identity is not None
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
                provider="google",
                external_id="222",
                external_username="Owner Google",
            ),
        )
        register_user(
            RegistrationRequest(username="intruder", password="password123")
        )
    _login_password(client, "intruder", "password123")

    _seed_session_state(client, state="S", verifier="V")
    _script_google(configured_app, sub="222", name="Owner Google")
    resp = client.get(
        "/auth/google/callback?state=S&code=C", follow_redirects=False
    )
    assert resp.status_code == 302

    with configured_app.app_context():
        identity = identity_service.find_identity("google", "222")
        assert identity is not None
        user = db.session.get(User, identity.user_id)
        assert user is not None
        assert user.username == "owner"


# ─── State / CSRF guards ─────────────────────────────────────────


def test_callback_400_on_state_mismatch(
    configured_app: Flask, client: FlaskClient
) -> None:
    _seed_session_state(client, state="GENUINE", verifier="V")
    resp = client.get(
        "/auth/google/callback?state=ATTACKER&code=C",
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_callback_redirects_to_login_when_session_state_missing(
    configured_app: Flask, client: FlaskClient
) -> None:
    resp = client.get(
        "/auth/google/callback?state=X&code=C", follow_redirects=False
    )
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_callback_handles_provider_error_param(
    configured_app: Flask, client: FlaskClient
) -> None:
    resp = client.get(
        "/auth/google/callback?error=access_denied",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_callback_session_state_is_consumed_one_time(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="zoe", password="password123")
        )
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="google",
                external_id="42",
                external_username="Zoe G",
            ),
        )

    _seed_session_state(client, state="S", verifier="V")
    _script_google(configured_app, sub="42", name="Zoe G")
    client.get(
        "/auth/google/callback?state=S&code=C", follow_redirects=False
    )

    with client.session_transaction() as sess:
        assert "oauth_google_state" not in sess
        assert "oauth_google_verifier" not in sess


# ─── Login template ──────────────────────────────────────────────


def test_login_page_shows_continue_with_google_when_configured(
    configured_app: Flask, client: FlaskClient
) -> None:
    body = client.get("/auth/login").data.decode()
    assert "Continue with Google" in body
    assert "/auth/google/start" in body


def test_login_page_hides_google_button_when_unconfigured(
    client: FlaskClient,
) -> None:
    body = client.get("/auth/login").data.decode()
    assert "Continue with Google" not in body


# ─── /auth/google/unlink ─────────────────────────────────────────


def test_unlink_removes_identity(
    configured_app: Flask, client: FlaskClient
) -> None:
    """Unlinking Google removes only the auth_identities row.
    Unlike Discord, there's no UserProfile column to clear because
    we don't mirror anything from Google."""
    with configured_app.app_context():
        user = register_user(
            RegistrationRequest(username="kim", password="password123")
        )
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="google",
                external_id="42",
                external_username="Kim G",
            ),
        )
    _login_password(client, "kim", "password123")

    resp = client.post("/auth/google/unlink", follow_redirects=False)
    assert resp.status_code == 302
    assert "/profiles/me" in resp.headers["Location"]

    with configured_app.app_context():
        assert identity_service.find_identity("google", "42") is None


def test_unlink_idempotent_when_no_identity(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        register_user(
            RegistrationRequest(username="lou", password="password123")
        )
    _login_password(client, "lou", "password123")
    resp = client.post("/auth/google/unlink", follow_redirects=False)
    assert resp.status_code == 302


def test_unlink_requires_login(client: FlaskClient) -> None:
    resp = client.post("/auth/google/unlink", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


# ─── Linked-accounts card on profile editor ─────────────────────


def test_profile_editor_shows_link_button_when_unlinked(
    configured_app: Flask, client: FlaskClient
) -> None:
    with configured_app.app_context():
        register_user(
            RegistrationRequest(username="paula", password="password123")
        )
    _login_password(client, "paula", "password123")

    body = client.get("/profiles/me").data.decode()
    assert "Link Google" in body


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
                provider="google",
                external_id="42",
                external_username="Quinn G",
            ),
        )
    _login_password(client, "quinn", "password123")

    body = client.get("/profiles/me").data.decode()
    assert "/auth/google/unlink" in body
