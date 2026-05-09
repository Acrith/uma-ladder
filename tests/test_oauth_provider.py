"""Provider-layer tests for ``services.oauth`` (PR-K1).

Exercises PKCE/state helpers and the Discord OAuth flow end-to-end
against a ``FakeHttpTransport``. No network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse

import pytest

from uma_ladder.services.oauth import (
    DiscordOAuthProvider,
    FakeHttpTransport,
    OAuthExchangeError,
    generate_pkce_pair,
    generate_state,
)

# ─── helpers ─────────────────────────────────────────────────────


def test_generate_state_is_unique_and_long_enough() -> None:
    a = generate_state()
    b = generate_state()
    assert a != b
    # token_urlsafe(32) → at least 32 chars (no padding)
    assert len(a) >= 32


def test_generate_pkce_pair_matches_rfc7636_s256() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    # Verifiers should not contain padding
    assert "=" not in verifier
    assert "=" not in challenge


# ─── DiscordOAuthProvider.authorization_url ──────────────────────


def test_authorization_url_includes_required_params() -> None:
    provider = DiscordOAuthProvider(
        client_id="cid", client_secret="csecret", transport=FakeHttpTransport()
    )
    url = provider.authorization_url(
        state="state-token",
        code_challenge="challenge-token",
        redirect_uri="https://example.test/auth/discord/callback",
    )
    assert url.startswith("https://discord.com/oauth2/authorize?")
    qs = urllib.parse.parse_qs(url.split("?", 1)[1])
    assert qs["client_id"] == ["cid"]
    assert qs["redirect_uri"] == [
        "https://example.test/auth/discord/callback"
    ]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["identify"]
    assert qs["state"] == ["state-token"]
    assert qs["code_challenge"] == ["challenge-token"]
    assert qs["code_challenge_method"] == ["S256"]


# ─── DiscordOAuthProvider.exchange_code ─────────────────────────


def _token_body(access_token: str = "AT") -> str:
    return json.dumps(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 604800,
            "scope": "identify",
        }
    )


def _profile_body(
    *, id: str = "123456789012345678", username: str = "alice", avatar: str | None = None,
    global_name: str | None = None,
) -> str:
    body = {"id": id, "username": username}
    if avatar is not None:
        body["avatar"] = avatar
    if global_name is not None:
        body["global_name"] = global_name
    return json.dumps(body)


def test_exchange_code_returns_provider_profile_on_happy_path() -> None:
    transport = FakeHttpTransport()
    transport.script(
        method="POST", url_substring="oauth2/token", status_code=200,
        body=_token_body("ACCESS-1"),
    )
    transport.script(
        method="GET", url_substring="users/@me", status_code=200,
        body=_profile_body(
            id="999000111222333444",
            username="alice",
            global_name="Alice on the Internet",
            avatar="abc123",
        ),
    )

    provider = DiscordOAuthProvider(
        client_id="cid", client_secret="csecret", transport=transport
    )
    profile = provider.exchange_code(
        code="thecode",
        code_verifier="verifier-x",
        redirect_uri="https://example.test/auth/discord/callback",
    )
    assert profile.provider == "discord"
    assert profile.external_id == "999000111222333444"
    # Prefer global_name (display) over username
    assert profile.external_username == "Alice on the Internet"
    assert profile.avatar_url == (
        "https://cdn.discordapp.com/avatars/999000111222333444/abc123.png"
    )

    # Token-exchange POST shape
    method, url, data, _ = transport.calls[0]
    assert method == "POST"
    assert "oauth2/token" in url
    assert data is not None
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "thecode"
    assert data["code_verifier"] == "verifier-x"
    assert data["redirect_uri"] == (
        "https://example.test/auth/discord/callback"
    )
    assert data["client_id"] == "cid"
    assert data["client_secret"] == "csecret"

    # Profile fetch carries the bearer
    method, url, _, headers = transport.calls[1]
    assert method == "GET"
    assert "users/@me" in url
    assert headers is not None
    assert headers["Authorization"] == "Bearer ACCESS-1"


def test_exchange_code_falls_back_to_username_when_no_global_name() -> None:
    transport = FakeHttpTransport()
    transport.script(
        method="POST", url_substring="oauth2/token",
        body=_token_body(),
    )
    transport.script(
        method="GET", url_substring="users/@me",
        body=_profile_body(username="legacy_user"),
    )
    provider = DiscordOAuthProvider(
        client_id="c", client_secret="s", transport=transport
    )
    profile = provider.exchange_code(
        code="x", code_verifier="y", redirect_uri="https://example.test/cb"
    )
    assert profile.external_username == "legacy_user"


def test_exchange_code_raises_when_token_endpoint_4xx() -> None:
    transport = FakeHttpTransport()
    transport.script(
        method="POST", url_substring="oauth2/token", status_code=400,
        body=json.dumps({"error": "invalid_grant"}),
    )
    provider = DiscordOAuthProvider(
        client_id="c", client_secret="s", transport=transport
    )
    with pytest.raises(OAuthExchangeError) as exc:
        provider.exchange_code(
            code="bad", code_verifier="y",
            redirect_uri="https://example.test/cb",
        )
    assert "400" in str(exc.value)


def test_exchange_code_raises_when_profile_missing_id() -> None:
    transport = FakeHttpTransport()
    transport.script(
        method="POST", url_substring="oauth2/token",
        body=_token_body(),
    )
    transport.script(
        method="GET", url_substring="users/@me",
        body=json.dumps({"username": "no-id-user"}),
    )
    provider = DiscordOAuthProvider(
        client_id="c", client_secret="s", transport=transport
    )
    with pytest.raises(OAuthExchangeError):
        provider.exchange_code(
            code="x", code_verifier="y",
            redirect_uri="https://example.test/cb",
        )


def test_exchange_code_raises_when_token_response_missing_access_token() -> None:
    transport = FakeHttpTransport()
    transport.script(
        method="POST", url_substring="oauth2/token",
        body=json.dumps({"token_type": "Bearer"}),
    )
    provider = DiscordOAuthProvider(
        client_id="c", client_secret="s", transport=transport
    )
    with pytest.raises(OAuthExchangeError):
        provider.exchange_code(
            code="x", code_verifier="y",
            redirect_uri="https://example.test/cb",
        )


def test_no_avatar_yields_none_avatar_url() -> None:
    transport = FakeHttpTransport()
    transport.script(
        method="POST", url_substring="oauth2/token",
        body=_token_body(),
    )
    transport.script(
        method="GET", url_substring="users/@me",
        body=_profile_body(),  # no avatar key
    )
    provider = DiscordOAuthProvider(
        client_id="c", client_secret="s", transport=transport
    )
    profile = provider.exchange_code(
        code="x", code_verifier="y", redirect_uri="https://example.test/cb"
    )
    assert profile.avatar_url is None
