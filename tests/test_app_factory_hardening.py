"""PR-K2.2 hardening tests:

1. ORM cascade — `db.session.delete(user)` succeeds when the user
   has child rows (UserProfile, AuthIdentity) protected by NOT
   NULL + ON DELETE CASCADE FKs. Without `passive_deletes=True`
   on the backref, the ORM emits `UPDATE child SET user_id=NULL`
   first and trips the constraint. Caught on prod 2026-05-09 when
   cleaning up a stray Discord-OAuth-created account.

2. ProxyFix — `url_for(..., _external=True)` returns `https://`
   when `X-Forwarded-Proto: https` is on the request, mimicking
   Fly's edge proxy behavior. Without ProxyFix, Flask sees the
   plaintext hop and emits `http://`, which broke Discord OAuth's
   `redirect_uri` byte-exact match.
"""

from __future__ import annotations

from flask import Flask, request, url_for

from uma_ladder.extensions import db
from uma_ladder.models import AuthIdentity, User, UserProfile
from uma_ladder.services import auth_identities as identity_service
from uma_ladder.services.auth import RegistrationRequest, register_user
from uma_ladder.services.oauth import ProviderProfile

# ─── ORM cascade ─────────────────────────────────────────────────


def test_delete_user_cascades_to_profile_and_identities(app: Flask) -> None:
    """The DB has ON DELETE CASCADE on user_profiles.user_id and
    auth_identities.user_id, but the ORM has to trust that — see
    `passive_deletes=True` + `cascade="all, delete"` on the
    `backref()` in the models. Regression: without those flags,
    SQLAlchemy emits `UPDATE child SET user_id=NULL` before the
    cascade and fails the NOT NULL FK constraint."""
    with app.app_context():
        user = register_user(
            RegistrationRequest(username="doomed", password="password123")
        )
        # UserProfile inserted lazily — link a Discord identity to
        # also force-create a UserProfile for the user.
        identity_service.link_identity(
            user,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="doomed_disc",
            ),
        )
        user_id = user.id

        # Sanity: child rows do exist
        assert (
            db.session.query(UserProfile).filter_by(user_id=user_id).count()
            == 1
        )
        assert (
            db.session.query(AuthIdentity).filter_by(user_id=user_id).count()
            == 1
        )

        # The actual test — delete should not raise.
        db.session.delete(user)
        db.session.commit()

        assert db.session.get(User, user_id) is None
        assert (
            db.session.query(UserProfile).filter_by(user_id=user_id).count()
            == 0
        )
        assert (
            db.session.query(AuthIdentity).filter_by(user_id=user_id).count()
            == 0
        )


# ─── ProxyFix ───────────────────────────────────────────────────


def test_url_for_emits_https_when_x_forwarded_proto_set(app: Flask) -> None:
    """Mimics Fly's edge proxy: TLS terminated, request reaches
    Flask over plaintext HTTP with `X-Forwarded-Proto: https`.
    With ProxyFix wired (PR-K2.2), `request.scheme` is `https`
    and `url_for(_external=True)` emits an `https://` URL. Without
    ProxyFix, Flask sees the plaintext hop and emits `http://`
    — which broke Discord OAuth's `redirect_uri` match on first
    deploy.

    Goes through `test_client` so the request traverses the WSGI
    middleware stack (where ProxyFix is wrapped). A direct
    `test_request_context` would bypass middleware entirely.
    """

    @app.get("/_test_proxy_fix")
    def _probe() -> str:
        return f"{request.scheme}|{url_for('auth.login', _external=True)}"

    client = app.test_client()

    # Without forwarded headers: plaintext, http URL.
    plain = client.get("/_test_proxy_fix").data.decode()
    assert plain.startswith("http|http://"), (
        f"baseline (no proxy headers) should be http, got {plain!r}"
    )

    # With forwarded headers: ProxyFix rewrites the WSGI environ
    # so `request.scheme == "https"` and url_for emits https.
    forwarded = client.get(
        "/_test_proxy_fix",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "umaladder.moe",
        },
    ).data.decode()
    assert forwarded.startswith("https|https://"), (
        f"expected https with X-Forwarded-Proto=https, got {forwarded!r}"
    )
    assert "umaladder.moe" in forwarded
