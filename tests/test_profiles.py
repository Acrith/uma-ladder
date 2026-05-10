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


# ─── PR-P1 — Avatar border picker ───────────────────────────────


def test_avatar_border_accepts_palette_tone(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """A valid palette key persists and shows up on subsequent
    page loads."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.post(
        "/profiles/me",
        data={"avatar_border": "cyan"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        profile = profiles_service.get_or_create_profile(user)
        assert profile.avatar_border == "cyan"


def test_avatar_border_empty_clears_choice(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Picking 'Default' (empty value) wipes the border back to
    NULL — no leftover style on the avatar."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    # First set a tone
    client.post(
        "/profiles/me",
        data={"avatar_border": "fuchsia"},
        follow_redirects=False,
    )
    # Then clear it
    client.post(
        "/profiles/me",
        data={"avatar_border": ""},
        follow_redirects=False,
    )
    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        profile = profiles_service.get_or_create_profile(user)
        assert profile.avatar_border is None


def test_avatar_border_rejects_unknown_tone_at_service(
    app: Flask, make_user
) -> None:
    """Service-layer guard against arbitrary strings — defends in
    depth even if the form layer is bypassed (DevTools, direct
    POST, etc)."""
    user = make_user(username="alice", password="password123")
    with app.app_context(), pytest.raises(
        profiles_service.UnknownAvatarBorderError
    ):
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        profiles_service.update_profile(
            u,
            profiles_service.ProfileUpdate(avatar_border="injected-xss"),
        )


def test_public_profile_renders_picked_border_class(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """When a tone is set, the matching ring-{color}-400 class
    shows up in the rendered HTML."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    client.post(
        "/profiles/me",
        data={"avatar_border": "violet"},
        follow_redirects=False,
    )
    body = client.get("/profiles/alice").data.decode()
    assert "ring-violet-400" in body


def test_avatar_border_overrides_oshi_ring_when_both_set(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-P1.1 — explicit user-picked border wins over the
    oshi-derived default. Without this, near-everyone in an
    Umamusume community ends up locked to fuchsia (because
    near-everyone picks an oshi), making the border feature
    invisible. The picker has to actually surface the chosen
    tone for the typical user."""
    make_user(username="alice", password="password123")
    oshi_id = _make_character(app, "gold-ship")
    _login(client, "alice", "password123")
    # Pick a NON-fuchsia tone alongside an oshi.
    client.post(
        "/profiles/me",
        data={
            "oshi_character_id": str(oshi_id),
            "avatar_border": "emerald",
        },
        follow_redirects=False,
    )
    body = client.get("/profiles/alice").data.decode()
    # Border wins on the avatar — emerald ring renders.
    assert "ring-emerald-400" in body
    # Oshi-fuchsia is NOT applied to the avatar (the fuchsia
    # used elsewhere on the profile — oshi pill background — is
    # bg-fuchsia-500/10, not ring-fuchsia-400, so this assertion
    # is specific to the avatar ring.)
    assert "ring-fuchsia-400" not in body


def test_oshi_provides_default_ring_when_no_border_picked(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Symmetric guard: a user with an oshi but no border choice
    still gets the fuchsia oshi ring as the default tone — the
    PR-P1.1 priority reversal must NOT break this case."""
    make_user(username="alice", password="password123")
    oshi_id = _make_character(app, "gold-ship")
    _login(client, "alice", "password123")
    client.post(
        "/profiles/me",
        data={"oshi_character_id": str(oshi_id)},
        follow_redirects=False,
    )
    body = client.get("/profiles/alice").data.decode()
    assert "ring-fuchsia-400" in body


def test_profile_editor_renders_swatch_grid_picker(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-P1.1 — the picker is a visual swatch grid, not a
    native <select>. Confirms each tone's swatch class lands in
    the rendered HTML so users can actually see what they're
    picking."""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    body = client.get("/profiles/me").data.decode()
    # A few representative swatches across the palette
    assert "bg-cyan-400" in body
    assert "bg-fuchsia-400" in body
    assert "bg-amber-400" in body
    assert "bg-pink-400" in body
    # Default option's dashed-border style
    assert "border-dashed" in body
    # Hidden radio inputs (sr-only is the visibility class)
    assert 'name="avatar_border"' in body
    assert "sr-only" in body


def test_history_tab_route_renders(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    resp = client.get("/profiles/alice/history")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Tab nav present + active tab is Matches.
    assert "/profiles/alice" in body
    assert "/profiles/alice/history" in body
    assert "/profiles/alice/achievements" in body


def test_achievements_tab_route_renders(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    resp = client.get("/profiles/alice/achievements")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Catalogue" in body
    # The full catalogue is shown, including locked entries that
    # the user hasn't earned. PR-P3 deliberately diverges from
    # PR-P2's "show only unlocked" rule on this dedicated tab.
    assert "Locked" in body
    # All starter achievements appear (representative samples).
    assert "Founding Member" in body
    assert "Discord Linked" in body


def test_achievements_tab_distinguishes_unlocked_from_locked(
    client: FlaskClient, app: Flask, make_user
) -> None:
    user = make_user(username="alice", password="password123")
    from uma_ladder.services import achievements as achievements_service

    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
    resp = client.get("/profiles/alice/achievements")
    body = resp.data.decode()
    # Granted entry's name still appears.
    assert "Founding Member" in body
    # Earned timestamp surfaces — locked entries don't have one,
    # so this is a safe-ish positive marker for the unlocked
    # render branch.
    assert "Earned" in body


def test_tab_count_badge_shows_unlocked_count(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-P3 — the Achievements tab nav shows a badge with the
    number of unlocked achievements. Helps visitors see at a
    glance how active a profile is."""
    user = make_user(username="alice", password="password123")
    from uma_ladder.services import achievements as achievements_service

    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
        achievements_service.grant(u, "link_discord")
    body = client.get("/profiles/alice").data.decode()
    # Tab nav badge: "Achievements 2" (HTML formatting may vary,
    # so check for the bare digit near the tab label).
    # The number 2 should appear at least once in the body in a
    # context that maps to the achievement count badge.
    achievements_link_idx = body.find("/profiles/alice/achievements")
    assert achievements_link_idx > 0
    # Look for ">2<" within ~200 chars of the link — the tab
    # template renders "<span ...>2</span>" right after the
    # label.
    nearby = body[achievements_link_idx:achievements_link_idx + 400]
    assert ">2<" in nearby


def test_overview_shows_only_unlocked_achievements(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """PR-P3 reaffirms PR-P2's Overview rule: locked entries
    don't render on the Overview tab even after we shipped the
    /achievements full catalogue."""
    user = make_user(username="alice", password="password123")
    from uma_ladder.services import achievements as achievements_service

    with app.app_context():
        from uma_ladder.models import User as _User

        u = db.session.get(_User, user["id"])
        achievements_service.grant(u, "founding_member")
    body = client.get("/profiles/alice").data.decode()
    # Granted entry shows.
    assert "Founding Member" in body
    # Locked-only entries don't.
    assert "First Official Win" not in body
    assert "Season Champion" not in body


def test_avatar_border_persists_across_unrelated_save(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """If the user previously picked a tone, then saves the form
    with no avatar_border field changed, the tone should remain.
    (This catches a regression where the form default-value
    handling clears the choice on subsequent saves.)"""
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    client.post(
        "/profiles/me",
        data={"avatar_border": "amber"},
        follow_redirects=False,
    )
    # Save a different field; explicitly include the existing
    # avatar_border so the form re-submits its current state
    # (the rendered <select>'s current value).
    client.post(
        "/profiles/me",
        data={
            "display_name": "Renamed Alice",
            "avatar_border": "amber",
        },
        follow_redirects=False,
    )
    with app.app_context():
        user = profiles_service.find_user_by_username("alice")
        profile = profiles_service.get_or_create_profile(user)
        assert profile.avatar_border == "amber"
        assert profile.display_name == "Renamed Alice"
