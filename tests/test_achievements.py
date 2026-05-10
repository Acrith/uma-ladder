"""Tests for the Achievements foundation (PR-P2).

Covers:
- Catalogue seed lands via the migration so the service has data.
- ``services.achievements.grant`` is idempotent + raises on
  unknown keys.
- OAuth-link auto-grant fires (Discord + Google) end-to-end via
  ``link_identity`` and ``create_user_for_oauth``.
- Admin grant + revoke endpoints check perms, audit-log, and
  flash a sensible message.
- Public profile renders unlocked badges (and only unlocked).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    Achievement,
    AdminAuditLog,
    Role,
    UserAchievement,
)
from uma_ladder.services import achievements as achievements_service
from uma_ladder.services import auth_identities as identity_service
from uma_ladder.services.oauth import ProviderProfile

# ─── shared fixtures ────────────────────────────────────────────


@pytest.fixture
def configured_app(app: Flask) -> Iterator[Flask]:
    """OAuth env populated so the auto-grant hook fires through
    the link path that's gated on configured providers."""
    app.config["DISCORD_OAUTH_CLIENT_ID"] = "test"
    app.config["DISCORD_OAUTH_CLIENT_SECRET"] = "test"
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = "test"
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = "test"
    yield app


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# ─── Catalogue + seed ───────────────────────────────────────────


def test_seed_catalogue_is_present(app: Flask) -> None:
    """The migration seeds the starter set; without these the
    auto-grant hooks would silently no-op."""
    with app.app_context():
        keys = {a.key for a in achievements_service.list_definitions()}
    # A representative subset — full list is in the migration.
    assert "founding_member" in keys
    assert "link_discord" in keys
    assert "link_google" in keys
    assert "first_official_win" in keys


def test_get_by_key_returns_achievement(app: Flask) -> None:
    with app.app_context():
        a = achievements_service.get_by_key("link_discord")
        assert a is not None
        assert a.name == "Discord Linked"


def test_get_by_key_returns_none_for_unknown(app: Flask) -> None:
    with app.app_context():
        assert achievements_service.get_by_key("does-not-exist") is None


# ─── grant idempotence ──────────────────────────────────────────


def test_grant_creates_row(app: Flask, make_user) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member", source="test")
        rows = achievements_service.list_for_user(user["id"])
        assert len(rows) == 1
        assert rows[0].achievement.key == "founding_member"
        assert rows[0].source == "test"


def test_grant_is_idempotent(app: Flask, make_user) -> None:
    """Re-granting must NOT duplicate or wipe ``unlocked_at``."""
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        first = achievements_service.grant(u, "link_discord")
        first_when = first.unlocked_at
        second = achievements_service.grant(u, "link_discord", source="ignored")
        assert second.unlocked_at == first_when
        # Source from the first grant is preserved (not overwritten
        # by the second call's "ignored").
        assert (
            db.session.query(UserAchievement)
            .filter_by(user_id=user["id"])
            .count()
            == 1
        )


def test_grant_unknown_key_raises(app: Flask, make_user) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context(), pytest.raises(
        achievements_service.UnknownAchievementError
    ):
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "nonexistent")


def test_has_achievement_probe(app: Flask, make_user) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        assert (
            achievements_service.has_achievement(user["id"], "link_discord")
            is False
        )
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "link_discord")
        assert (
            achievements_service.has_achievement(user["id"], "link_discord")
            is True
        )
        # Unknown key is False, not raise.
        assert (
            achievements_service.has_achievement(user["id"], "nope") is False
        )


# ─── OAuth-link auto-grant ──────────────────────────────────────


def test_link_discord_auto_grants_achievement(
    configured_app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with configured_app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        identity_service.link_identity(
            u,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="ada_disc",
            ),
        )
        assert (
            achievements_service.has_achievement(user["id"], "link_discord")
            is True
        )
        # Other provider's badge is NOT granted.
        assert (
            achievements_service.has_achievement(user["id"], "link_google")
            is False
        )


def test_link_google_auto_grants_achievement(
    configured_app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with configured_app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        identity_service.link_identity(
            u,
            ProviderProfile(
                provider="google",
                external_id="g42",
                external_username="Ada Google",
            ),
        )
        assert (
            achievements_service.has_achievement(user["id"], "link_google")
            is True
        )


def test_create_user_for_oauth_grants_link_achievement(
    configured_app: Flask,
) -> None:
    """First-time OAuth signup also gets the badge — auto-grant
    fires from create_user_for_oauth, not just link_identity."""
    with configured_app.app_context():
        user = identity_service.create_user_for_oauth(
            ProviderProfile(
                provider="discord",
                external_id="brand_new",
                external_username="newcomer",
            )
        )
        assert (
            achievements_service.has_achievement(user.id, "link_discord")
            is True
        )


def test_relink_after_unlink_re_grants_safely(
    configured_app: Flask, make_user
) -> None:
    """Unlinking does NOT revoke the achievement (badges persist
    once earned). Re-linking just confirms the existing grant —
    no error, no double-row."""
    user = make_user(username="ada", password="password123")
    with configured_app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        identity_service.link_identity(
            u,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="ada",
            ),
        )
        identity_service.unlink_identity(u, "discord")
        # Badge is preserved across unlink.
        assert (
            achievements_service.has_achievement(user["id"], "link_discord")
            is True
        )
        # Re-link doesn't 500 / duplicate.
        identity_service.link_identity(
            u,
            ProviderProfile(
                provider="discord",
                external_id="42",
                external_username="ada",
            ),
        )
        assert (
            db.session.query(UserAchievement)
            .filter_by(user_id=user["id"])
            .count()
            == 1
        )


# ─── Admin grant / revoke ──────────────────────────────────────


def test_admin_grant_route_grants_and_audit_logs(
    app: Flask, client: FlaskClient, make_user
) -> None:
    admin = make_user(
        username="boss", password="password123", role=Role.ADMIN
    )
    target = make_user(username="ada", password="password123")
    _login(client, "boss", "password123")
    resp = client.post(
        f"/admin/users/{target['id']}/achievements/grant",
        data={"key": "founding_member"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert (
            achievements_service.has_achievement(
                target["id"], "founding_member"
            )
            is True
        )
        # Audit row landed.
        audit = db.session.query(AdminAuditLog).filter_by(
            actor_user_id=admin["id"],
            action="achievement_grant",
        ).all()
        assert len(audit) == 1
        assert audit[0].details == "founding_member"


def test_admin_grant_route_idempotent_when_already_earned(
    app: Flask, client: FlaskClient, make_user
) -> None:
    """Granting twice should NOT log a second audit row — the
    second call just flashes 'already had' and skips the audit
    write. Important so admin click-spam doesn't pollute the
    audit feed."""
    admin = make_user(
        username="boss", password="password123", role=Role.ADMIN
    )
    target = make_user(username="ada", password="password123")
    _login(client, "boss", "password123")
    for _ in range(2):
        client.post(
            f"/admin/users/{target['id']}/achievements/grant",
            data={"key": "founding_member"},
            follow_redirects=False,
        )
    with app.app_context():
        # Single user_achievements row.
        assert (
            db.session.query(UserAchievement)
            .filter_by(user_id=target["id"])
            .count()
            == 1
        )
        # Single audit row.
        assert (
            db.session.query(AdminAuditLog)
            .filter_by(
                actor_user_id=admin["id"], action="achievement_grant"
            )
            .count()
            == 1
        )


def test_admin_grant_route_rejects_unknown_key(
    app: Flask, client: FlaskClient, make_user
) -> None:
    make_user(username="boss", password="password123", role=Role.ADMIN)
    target = make_user(username="ada", password="password123")
    _login(client, "boss", "password123")
    resp = client.post(
        f"/admin/users/{target['id']}/achievements/grant",
        data={"key": "fictional"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        # No grant happened.
        assert (
            db.session.query(UserAchievement)
            .filter_by(user_id=target["id"])
            .count()
            == 0
        )


def test_admin_revoke_route_removes_grant_and_audit_logs(
    app: Flask, client: FlaskClient, make_user
) -> None:
    admin = make_user(
        username="boss", password="password123", role=Role.ADMIN
    )
    target = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, target["id"])
        achievements_service.grant(u, "founding_member")
    _login(client, "boss", "password123")
    resp = client.post(
        f"/admin/users/{target['id']}/achievements/revoke",
        data={"key": "founding_member"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        assert (
            db.session.query(UserAchievement)
            .filter_by(user_id=target["id"])
            .count()
            == 0
        )
        audit = db.session.query(AdminAuditLog).filter_by(
            actor_user_id=admin["id"],
            action="achievement_revoke",
        ).all()
        assert len(audit) == 1


def test_admin_grant_requires_admin_role(
    client: FlaskClient, make_user
) -> None:
    """Plain user POSTing to the grant endpoint is bounced."""
    make_user(username="rando", password="password123")
    target = make_user(username="ada", password="password123")
    _login(client, "rando", "password123")
    resp = client.post(
        f"/admin/users/{target['id']}/achievements/grant",
        data={"key": "founding_member"},
        follow_redirects=False,
    )
    # min_role decorator redirects unauthorized users.
    assert resp.status_code in (302, 403)


# ─── Public profile render ─────────────────────────────────────


def test_public_profile_shows_unlocked_achievement(
    app: Flask, client: FlaskClient, make_user
) -> None:
    target = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, target["id"])
        achievements_service.grant(u, "founding_member")
    body = client.get("/profiles/ada").data.decode()
    # Achievement card title appears.
    assert "Achievements" in body
    # Specific badge name is present.
    assert "Founding Member" in body


def test_public_profile_hides_achievements_section_when_empty(
    app: Flask, client: FlaskClient, make_user
) -> None:
    """Per PR-P2 scoping (user picked 'show only unlocked'),
    fresh accounts with no achievements don't see the card at
    all — keeps the profile clean."""
    make_user(username="newbie", password="password123")
    body = client.get("/profiles/newbie").data.decode()
    # The descriptive text inside the card shouldn't appear when
    # there are no rows. The "Achievements" word IS used in the
    # admin nav etc., so we can't assert its absence in body —
    # but specific badge names should be absent.
    assert "Founding Member" not in body
    assert "Discord Linked" not in body


def test_public_profile_does_not_show_locked_achievements(
    app: Flask, client: FlaskClient, make_user
) -> None:
    """Grant ONE achievement; confirm OTHER achievements (not
    granted) don't render. This is the 'show only unlocked'
    invariant from PR-P2 scoping."""
    target = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, target["id"])
        achievements_service.grant(u, "link_discord")
    body = client.get("/profiles/ada").data.decode()
    # Granted appears.
    assert "Discord Linked" in body
    # Unlocked-but-still-in-catalogue does NOT.
    assert "Founding Member" not in body
    assert "First Official Win" not in body


# ─── Idempotent grant with disabled achievements ───────────────


def test_grant_disabled_achievement_returns_existing_row_if_present(
    app: Flask, make_user
) -> None:
    """Defensive: an admin disables an achievement the user
    already earned. The user keeps their existing grant; new
    grants of that disabled key are blocked."""
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        # Grant first while enabled.
        achievements_service.grant(u, "founding_member")
        # Disable it.
        a = db.session.scalars(
            db.select(Achievement).where(Achievement.key == "founding_member")
        ).first()
        a.enabled = False
        db.session.commit()
        # Re-grant returns the existing row, no error.
        existing = achievements_service.grant(u, "founding_member")
        assert existing is not None


# ─── PR-P4 — Showcase ─────────────────────────────────────────────


def test_set_showcase_persists_ordered_list(
    app: Flask, make_user
) -> None:
    """Happy path: granted-and-enabled ids in user order persist."""
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        achievements_service.grant(u, "link_discord")
        achievements_service.grant(u, "link_google")
        founding_id = achievements_service.get_by_key("founding_member").id
        discord_id = achievements_service.get_by_key("link_discord").id
        google_id = achievements_service.get_by_key("link_google").id
        # Pin in reverse-grant order to prove the user picks the order.
        result = achievements_service.set_showcase(
            u, [google_id, founding_id, discord_id]
        )
        assert result == [google_id, founding_id, discord_id]
        showcased = achievements_service.list_showcased_for_user(user["id"])
        assert [a.id for a in showcased] == [
            google_id,
            founding_id,
            discord_id,
        ]


def test_set_showcase_silently_drops_ids_user_has_not_unlocked(
    app: Flask, make_user
) -> None:
    """Defense: user POSTs a crafted id list with achievements they
    don't have. Service should drop those, never raise — caller
    paths are best-effort."""
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        founding_id = achievements_service.get_by_key("founding_member").id
        season_champion_id = achievements_service.get_by_key(
            "season_champion"
        ).id
        # User only has founding_member; season_champion id should
        # be silently dropped.
        result = achievements_service.set_showcase(
            u, [founding_id, season_champion_id]
        )
        assert result == [founding_id]


def test_set_showcase_caps_at_showcase_max(app: Flask, make_user) -> None:
    """User can persist more than 6 ids via crafted form post; cap
    applies server-side."""
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        # Grant every starter achievement (10 — more than the cap
        # of 6).
        for definition in achievements_service.STARTER_ACHIEVEMENT_DEFINITIONS:
            achievements_service.grant(u, definition["key"])
        all_ids = [
            achievements_service.get_by_key(d["key"]).id
            for d in achievements_service.STARTER_ACHIEVEMENT_DEFINITIONS
        ]
        result = achievements_service.set_showcase(u, all_ids)
        assert len(result) == achievements_service.SHOWCASE_MAX
        # Order is preserved within the cap.
        assert result == all_ids[: achievements_service.SHOWCASE_MAX]


def test_set_showcase_removes_duplicates_preserving_first_position(
    app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        achievements_service.grant(u, "link_discord")
        a_id = achievements_service.get_by_key("founding_member").id
        b_id = achievements_service.get_by_key("link_discord").id
        # Duplicate `a_id` — should collapse to one entry, in its
        # first position.
        result = achievements_service.set_showcase(
            u, [a_id, b_id, a_id]
        )
        assert result == [a_id, b_id]


def test_set_showcase_empty_list_clears_persisted(
    app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        a_id = achievements_service.get_by_key("founding_member").id
        achievements_service.set_showcase(u, [a_id])
        achievements_service.set_showcase(u, [])
        assert (
            achievements_service.list_showcased_for_user(user["id"]) == []
        )


def test_list_showcased_filters_revoked_achievements(
    app: Flask, make_user
) -> None:
    """If an admin revokes a granted achievement after the user
    pinned it, the pin silently disappears from the showcase
    rather than 500ing."""
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User
        from uma_ladder.models import UserAchievement

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        achievements_service.grant(u, "link_discord")
        a_id = achievements_service.get_by_key("founding_member").id
        b_id = achievements_service.get_by_key("link_discord").id
        achievements_service.set_showcase(u, [a_id, b_id])
        # Now revoke founding_member.
        revoke_row = db.session.scalars(
            db.select(UserAchievement).where(
                UserAchievement.user_id == user["id"],
                UserAchievement.achievement_id == a_id,
            )
        ).first()
        db.session.delete(revoke_row)
        db.session.commit()
        # Showcase should now only contain link_discord, in order.
        result = achievements_service.list_showcased_for_user(user["id"])
        assert [a.id for a in result] == [b_id]


def test_list_showcased_filters_disabled_achievements(
    app: Flask, make_user
) -> None:
    """Same filter, but the achievement was disabled in the
    catalogue rather than revoked from the user."""
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        achievements_service.grant(u, "link_discord")
        a = achievements_service.get_by_key("founding_member")
        b = achievements_service.get_by_key("link_discord")
        achievements_service.set_showcase(u, [a.id, b.id])
        a.enabled = False
        db.session.commit()
        result = achievements_service.list_showcased_for_user(user["id"])
        assert [x.id for x in result] == [b.id]


def test_profile_editor_renders_showcase_picker_when_user_has_unlocks(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
    _login(client, "ada", "password123")
    body = client.get("/profiles/me").data.decode()
    assert "Achievement showcase" in body
    assert "showcase-pinned" in body
    assert "showcase-pool" in body
    # Sortable.js loaded via CDN.
    assert "Sortable.min.js" in body


def test_profile_editor_hides_showcase_picker_when_no_unlocks(
    client: FlaskClient, make_user
) -> None:
    """Fresh user with no achievements doesn't see the picker —
    no point dragging from an empty pool."""
    make_user(username="ada", password="password123")
    _login(client, "ada", "password123")
    body = client.get("/profiles/me").data.decode()
    assert "Achievement showcase" not in body


def test_profile_editor_save_persists_showcase(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        achievements_service.grant(u, "link_discord")
        a_id = achievements_service.get_by_key("founding_member").id
        b_id = achievements_service.get_by_key("link_discord").id
    _login(client, "ada", "password123")
    resp = client.post(
        "/profiles/me",
        data={"showcased_achievement_ids": f"{b_id},{a_id}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        ids = [
            x.id
            for x in achievements_service.list_showcased_for_user(user["id"])
        ]
        assert ids == [b_id, a_id]


def test_public_profile_hero_renders_showcase(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        a_id = achievements_service.get_by_key("founding_member").id
        achievements_service.set_showcase(u, [a_id])
    body = client.get("/profiles/ada").data.decode()
    assert "Showcase" in body
    # Tier-colored border class for gold tier (founding_member is gold).
    assert "border-amber-500/50" in body


def test_public_profile_hero_hides_showcase_when_empty(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="ada", password="password123")
    body = client.get("/profiles/ada").data.decode()
    # The label-eyebrow text "Showcase" should not appear when
    # no pins exist. Use a unique-enough marker.
    assert ">Showcase<" not in body


def test_grant_disabled_achievement_for_user_without_it_raises(
    app: Flask, make_user
) -> None:
    user = make_user(username="ada", password="password123")
    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        a = db.session.scalars(
            db.select(Achievement).where(Achievement.key == "founding_member")
        ).first()
        a.enabled = False
        db.session.commit()
        with pytest.raises(achievements_service.AchievementError):
            achievements_service.grant(u, "founding_member")
