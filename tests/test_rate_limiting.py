"""PR-Q2 — rate-limiter sanity tests.

The bulk of the test suite runs with `TESTING=True` which flips
`RATELIMIT_ENABLED=False` in `create_app`, so the per-route
`@limiter.limit(...)` decorators are no-ops everywhere else. These
tests build a fresh app with limiting deliberately enabled to
exercise the 429 path.
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from uma_ladder import create_app
from uma_ladder.config import TestConfig
from uma_ladder.extensions import db, limiter


class _LimitedTestConfig(TestConfig):
    """Same as TestConfig but with rate-limiting forced ON. Needs to
    be a config class (not a post-`create_app` flip) so the flag is
    in place BEFORE `limiter.init_app` runs — otherwise the in-memory
    storage backend isn't created and `limiter.reset()` asserts."""

    RATELIMIT_ENABLED = True


@pytest.fixture
def limited_app():
    """App with rate-limiting forced on. Each test gets a fresh
    in-memory DB and a reset limiter (so per-IP counters from one
    test don't bleed into the next)."""
    app = create_app(_LimitedTestConfig)
    with app.app_context():
        db.create_all()
        # Reset AFTER init_app has wired the in-memory storage on
        # this app (`init_app` runs inside `create_app`). Counters
        # are module-global so explicit reset is what keeps tests
        # order-independent.
        limiter.reset()
        # Seed achievement catalogue so any route that touches it
        # post-register works. Mirrors what conftest does for the
        # standard `app` fixture.
        from uma_ladder.services import achievements as ach_service

        ach_service.ensure_starter_seed()
        yield app


@pytest.fixture
def limited_client(limited_app) -> FlaskClient:
    return limited_app.test_client()


def test_register_hits_429_after_5_attempts(limited_client: FlaskClient) -> None:
    """5/hour cap on POST /auth/register. The 6th attempt 429s.

    Sending malformed payloads is fine — flask-limiter counts the
    request before the form validates, so each POST consumes one
    slot regardless of whether the registration itself succeeded.
    """
    for i in range(5):
        resp = limited_client.post(
            "/auth/register",
            data={"username": f"x{i}", "password": "password123"},
            follow_redirects=False,
        )
        # 200 (re-render form on validation error) or 302
        # (successful redirect) are both pre-429 territory.
        assert resp.status_code in (200, 302), (
            f"attempt {i + 1}: expected 200/302, got {resp.status_code}"
        )

    sixth = limited_client.post(
        "/auth/register",
        data={"username": "x5", "password": "password123"},
        follow_redirects=False,
    )
    assert sixth.status_code == 429
    assert b"Too many requests" in sixth.data


def test_get_register_does_not_consume_quota(
    limited_client: FlaskClient,
) -> None:
    """The decorator scopes the limit to POST (`methods=["POST"]`).
    GETs for the registration form must not eat the 5/hour budget,
    otherwise the form fails to load after a handful of refreshes."""
    for _ in range(20):
        resp = limited_client.get("/auth/register")
        assert resp.status_code == 200

    # POST budget is still untouched — first POST should succeed.
    resp = limited_client.post(
        "/auth/register",
        data={"username": "first", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302), resp.status_code


def test_login_429_after_20_in_15min(limited_client: FlaskClient) -> None:
    """20 per 15 minutes on POST /auth/login. Each attempt — even
    failures — counts. PR-J10's per-username lockout is independent;
    this is the per-IP cap."""
    for i in range(20):
        resp = limited_client.post(
            "/auth/login",
            data={"username": "ghost", "password": "wrong"},
            follow_redirects=False,
        )
        assert resp.status_code == 200, (
            f"attempt {i + 1}: expected 200 (failed login renders form), "
            f"got {resp.status_code}"
        )

    twenty_first = limited_client.post(
        "/auth/login",
        data={"username": "ghost", "password": "wrong"},
        follow_redirects=False,
    )
    assert twenty_first.status_code == 429


def test_429_template_renders(limited_client: FlaskClient) -> None:
    """The custom 429 handler renders our friendly page with the
    limit string surfaced. Catches regressions where the template
    name drifts or the errorhandler stops getting registered."""
    for _ in range(5):
        limited_client.post(
            "/auth/register",
            data={"username": "y", "password": "password123"},
        )
    resp = limited_client.post(
        "/auth/register",
        data={"username": "y5", "password": "password123"},
    )
    assert resp.status_code == 429
    body = resp.data.decode()
    assert "Slow down" in body
    assert "5 per " in body  # the "5 per hour" hint surfaces


def test_limiter_disabled_in_normal_test_app(app, client) -> None:
    """The default `app` / `client` fixtures (used by every other
    test) come up with RATELIMIT_ENABLED=False — so the rest of the
    suite never trips a 429 by accident."""
    assert app.config.get("RATELIMIT_ENABLED") is False
    # Hammer the register endpoint past its 5/hour budget; nothing
    # should 429 in the default fixture.
    for i in range(10):
        resp = client.post(
            "/auth/register",
            data={"username": f"u{i}", "password": "password123"},
        )
        assert resp.status_code != 429, (
            f"attempt {i + 1}: limiter should be disabled but got 429"
        )
