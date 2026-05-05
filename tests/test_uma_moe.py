"""uma.moe trainer-summary integration.

Cosmetic enrichment of public profiles. Failures must never propagate
to the request handler — every test exercises the fallback paths as
much as the happy path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import Role, UmaMoeCache
from uma_ladder.services import uma_moe as uma_moe_service
from uma_ladder.services.uma_moe import (
    FakeUmaMoeTransport,
    HttpResponse,
    set_transport,
)

FIXTURE = (
    Path(__file__).parent / "fixtures" / "uma_moe_profile_sample.json"
).read_bytes()


def _ok_response(body: bytes = FIXTURE) -> HttpResponse:
    return HttpResponse(ok=True, status_code=200, body=body, error=None)


def _404() -> HttpResponse:
    return HttpResponse(
        ok=False, status_code=404, body=b'{"error":"not found","status":404}',
        error="HTTP Error 404",
    )


def _network_error() -> HttpResponse:
    return HttpResponse(ok=False, status_code=0, body=b"", error="dns failure")


def _profile_url(friend_code: str) -> str:
    return f"https://uma.moe/api/v4/user/profile/{friend_code}"


# ---------- service-level tests ----------


def test_happy_path_returns_summary(app: Flask) -> None:
    fc = "111122223333"
    with app.app_context():
        set_transport(FakeUmaMoeTransport({_profile_url(fc): _ok_response()}))
        summary = uma_moe_service.fetch_trainer_summary(fc)
        assert summary is not None
        assert summary.friend_code == fc
        assert summary.trainer_name == "TestTrainer"
        assert summary.circle_name == "TestClub"
        assert summary.total_fans == 150000000
        assert summary.gain_7d == 2000000
        assert summary.gain_30d == 8500000
        assert summary.alltime_rank == 12345


def test_invalid_friend_code_returns_none_without_calling_transport(
    app: Flask,
) -> None:
    transport = FakeUmaMoeTransport({})
    with app.app_context():
        set_transport(transport)
        assert uma_moe_service.fetch_trainer_summary(None) is None
        assert uma_moe_service.fetch_trainer_summary("") is None
        assert uma_moe_service.fetch_trainer_summary("   ") is None
        assert uma_moe_service.fetch_trainer_summary("not-numeric") is None
        assert transport.calls == []


def test_separators_are_stripped(app: Flask) -> None:
    """Users sometimes paste friend codes as `1111-2222-3333`."""
    fc_raw = "1111-2222-3333"
    fc_clean = "111122223333"
    with app.app_context():
        transport = FakeUmaMoeTransport({_profile_url(fc_clean): _ok_response()})
        set_transport(transport)
        summary = uma_moe_service.fetch_trainer_summary(fc_raw)
        assert summary is not None
        assert summary.friend_code == fc_clean
        # Transport saw the cleaned URL.
        assert transport.calls == [_profile_url(fc_clean)]


def test_404_caches_negative_and_returns_none(app: Flask) -> None:
    fc = "999988887777"
    transport = FakeUmaMoeTransport({_profile_url(fc): _404()})
    with app.app_context():
        set_transport(transport)
        assert uma_moe_service.fetch_trainer_summary(fc) is None
        # Cache row written with status=404.
        cached = db.session.get(UmaMoeCache, fc)
        assert cached is not None
        assert cached.status == 404
        assert cached.payload_json is None
        # Second call within TTL doesn't re-hit transport.
        assert uma_moe_service.fetch_trainer_summary(fc) is None
        assert len(transport.calls) == 1


def test_network_failure_returns_none_and_caches(app: Flask) -> None:
    fc = "111122223333"
    transport = FakeUmaMoeTransport({_profile_url(fc): _network_error()})
    with app.app_context():
        set_transport(transport)
        assert uma_moe_service.fetch_trainer_summary(fc) is None
        cached = db.session.get(UmaMoeCache, fc)
        assert cached is not None
        assert cached.status == 0  # transport-level


def test_malformed_json_returns_none(app: Flask) -> None:
    fc = "111122223333"
    bad = HttpResponse(ok=True, status_code=200, body=b"not json {{", error=None)
    with app.app_context():
        set_transport(FakeUmaMoeTransport({_profile_url(fc): bad}))
        assert uma_moe_service.fetch_trainer_summary(fc) is None


def test_cache_hit_within_ttl_skips_transport(app: Flask) -> None:
    fc = "111122223333"
    transport = FakeUmaMoeTransport({_profile_url(fc): _ok_response()})
    with app.app_context():
        set_transport(transport)
        # First call → 1 transport hit.
        uma_moe_service.fetch_trainer_summary(fc)
        # Second call inside TTL → 0 additional hits.
        uma_moe_service.fetch_trainer_summary(fc)
        assert len(transport.calls) == 1


def test_expired_cache_refetches(app: Flask) -> None:
    fc = "111122223333"
    transport = FakeUmaMoeTransport({_profile_url(fc): _ok_response()})
    with app.app_context():
        set_transport(transport)
        uma_moe_service.fetch_trainer_summary(fc)
        # Backdate the cache row so TTL has elapsed.
        cached = db.session.get(UmaMoeCache, fc)
        cached.fetched_at = datetime.now(UTC) - timedelta(hours=99)
        db.session.commit()
        uma_moe_service.fetch_trainer_summary(fc)
        assert len(transport.calls) == 2


def test_max_age_override(app: Flask) -> None:
    """Caller can force a refetch by passing max_age=timedelta(0)."""
    fc = "111122223333"
    transport = FakeUmaMoeTransport({_profile_url(fc): _ok_response()})
    with app.app_context():
        set_transport(transport)
        uma_moe_service.fetch_trainer_summary(fc)
        uma_moe_service.fetch_trainer_summary(fc, max_age=timedelta(0))
        assert len(transport.calls) == 2


# ---------- public profile rendering ----------


def test_public_profile_renders_card_when_friend_code_set(
    client: FlaskClient, app: Flask, make_user
) -> None:
    fc = "111122223333"
    user = make_user(username="acrith", role=Role.USER)
    with app.app_context():
        # Set friend_code via the service so we go through the same code path.
        from uma_ladder.models import User
        from uma_ladder.services import profiles as profiles_service
        u = db.session.get(User, user["id"])
        profiles_service.update_profile(
            u, profiles_service.ProfileUpdate(friend_code=fc)
        )
        set_transport(FakeUmaMoeTransport({_profile_url(fc): _ok_response()}))

    resp = client.get("/profiles/acrith")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "In-game stats" in body
    assert "TestClub" in body
    assert "150,000,000" in body  # total_fans
    assert "2,000,000" in body  # gain_7d
    assert "Global rank" in body
    assert "via uma.moe" in body


def test_public_profile_links_trainer_name_to_uma_moe(
    client: FlaskClient, app: Flask, make_user
) -> None:
    fc = "111122223333"
    user = make_user(username="acrith2", role=Role.USER)
    with app.app_context():
        from uma_ladder.models import User
        from uma_ladder.services import profiles as profiles_service
        u = db.session.get(User, user["id"])
        profiles_service.update_profile(
            u, profiles_service.ProfileUpdate(friend_code=fc)
        )
        set_transport(FakeUmaMoeTransport({_profile_url(fc): _ok_response()}))

    resp = client.get("/profiles/acrith2")
    body = resp.data.decode()
    # Trainer name links out to uma.moe with the friend code.
    assert f'href="https://uma.moe/profile/{fc}"' in body
    assert 'target="_blank"' in body
    assert 'rel="noopener noreferrer"' in body


def test_public_profile_friend_code_is_click_to_copy(
    client: FlaskClient, app: Flask, make_user
) -> None:
    fc = "111122223333"
    user = make_user(username="copyme", role=Role.USER)
    with app.app_context():
        from uma_ladder.models import User
        from uma_ladder.services import profiles as profiles_service
        u = db.session.get(User, user["id"])
        profiles_service.update_profile(
            u, profiles_service.ProfileUpdate(friend_code=fc)
        )
    resp = client.get("/profiles/copyme")
    body = resp.data.decode()
    # Friend-code pill carries the data-copy attribute the JS handler hooks.
    assert f'data-copy="{fc}"' in body
    assert 'Click to copy' in body


def test_public_profile_skips_card_when_no_friend_code(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="bob", role=Role.USER)
    resp = client.get("/profiles/bob")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "In-game stats" not in body


def test_public_profile_silent_on_uma_moe_404(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """A 404 (e.g. typo'd friend code) must not break the page."""
    fc = "999988887777"
    user = make_user(username="dave", role=Role.USER)
    with app.app_context():
        from uma_ladder.models import User
        from uma_ladder.services import profiles as profiles_service
        u = db.session.get(User, user["id"])
        profiles_service.update_profile(
            u, profiles_service.ProfileUpdate(friend_code=fc)
        )
        set_transport(FakeUmaMoeTransport({_profile_url(fc): _404()}))

    resp = client.get("/profiles/dave")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "In-game stats" not in body
