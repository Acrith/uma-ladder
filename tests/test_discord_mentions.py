"""PR-E1b: Discord <@USERID> mentions for player notifications.

Profiles can store a numeric `discord_user_id` (snowflake). Per-player
events (room codes, results, cancellations, registration removed)
prefix the embed with `<@id>` mentions so participating users get a
desktop / mobile push when an action is available to them.

The `discord_handle` text field stays for display only — mentions
require the numeric ID. No env-var change; no bot token; webhook posts
as before."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftMatchStatus,
    Role,
    Season,
    SeasonStatus,
    UserProfile,
)
from uma_ladder.notifications import services as notif_services
from uma_ladder.notifications.discord import FakeTransport, set_transport
from uma_ladder.notifications.mentions import (
    mention_for_user,
    mention_prefix,
)
from uma_ladder.services import draft as draft_service
from uma_ladder.services import official as official_service
from uma_ladder.services import profiles as profiles_service


def _season() -> Season:
    now = datetime.now(UTC)
    s = Season(
        name="S",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
        status=SeasonStatus.ACTIVE,
    )
    db.session.add(s)
    db.session.commit()
    return s


def _set_discord_id(user_id: int, snowflake: str | None) -> None:
    profile = (
        db.session.query(UserProfile).filter_by(user_id=user_id).one_or_none()
    )
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.session.add(profile)
    profile.discord_user_id = snowflake
    db.session.commit()


# ---------- mention helper unit tests ----------


def test_mention_for_user_returns_empty_when_no_snowflake(
    app: Flask, make_user
) -> None:
    u = make_user(username="alice")
    with app.app_context():
        assert mention_for_user(u["id"]) == ""
        assert mention_for_user(None) == ""


def test_mention_for_user_returns_snowflake_when_set(
    app: Flask, make_user
) -> None:
    u = make_user(username="alice")
    with app.app_context():
        _set_discord_id(u["id"], "111222333444555666")
        assert mention_for_user(u["id"]) == "<@111222333444555666>"


def test_mention_prefix_joins_snowflakes_and_skips_missing(
    app: Flask, make_user
) -> None:
    a = make_user(username="alice")
    b = make_user(username="bob")
    c = make_user(username="cara")
    with app.app_context():
        _set_discord_id(a["id"], "100")
        _set_discord_id(c["id"], "300")
        # bob has no discord_user_id → silently dropped from the prefix
        out = mention_prefix([a["id"], b["id"], c["id"]])
        assert "<@100>" in out
        assert "<@300>" in out
        assert "<@bob>" not in out


def test_mention_prefix_empty_when_nobody_has_id(
    app: Flask, make_user
) -> None:
    a = make_user(username="alice")
    b = make_user(username="bob")
    with app.app_context():
        assert mention_prefix([a["id"], b["id"]]) == ""


# ---------- payload-shape tests ----------


def test_official_room_code_includes_mention_content(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    a = make_user(username="alice")
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)
        _set_discord_id(a["id"], "999")

        s = _season()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="R", organizer_user_id=org["id"]
            )
        )
        official_service.open_registration(race.id)
        official_service.register(race.id, a["id"])
        official_service.set_room_code(race.id, "ROOM-1")

        room_code_call = next(
            payload for _, payload in transport.calls if "Room code" in str(payload)
        )
        assert room_code_call["content"] == "<@999>"
        assert room_code_call["allowed_mentions"] == {"parse": ["users"]}


def test_official_room_code_no_content_when_nobody_has_id(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    a = make_user(username="alice")
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)

        s = _season()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="R", organizer_user_id=org["id"]
            )
        )
        official_service.open_registration(race.id)
        official_service.register(race.id, a["id"])
        official_service.set_room_code(race.id, "ROOM-1")

        room_code_call = next(
            payload for _, payload in transport.calls if "Room code" in str(payload)
        )
        assert "content" not in room_code_call
        assert "allowed_mentions" not in room_code_call


def test_draft_room_code_mentions_both_players(
    app: Flask, make_user
) -> None:
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)
        _set_discord_id(host["id"], "111")
        _set_discord_id(opp["id"], "222")

        s = _season()
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        match.opponent_user_id = opp["id"]
        match.status = DraftMatchStatus.ROOM_CODE_AVAILABLE
        match.room_code = "DRAFT-1"
        db.session.commit()

        notif_services.notify_draft_room_code(match)

        # FakeTransport may have recorded a publish-event call already if
        # `create_match` emits one, so search the calls list for the
        # room-code embed specifically.
        room_code_payloads = [
            p for _, p in transport.calls if "room code" in str(p).lower()
        ]
        assert len(room_code_payloads) >= 1
        last = room_code_payloads[-1]
        assert "<@111>" in last["content"]
        assert "<@222>" in last["content"]


def test_official_race_cancelled_mentions_registered_users(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    a = make_user(username="alice")
    b = make_user(username="bob")
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)
        _set_discord_id(a["id"], "100")
        # bob has no discord_user_id → only alice is mentioned

        s = _season()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="ToCancel", organizer_user_id=org["id"]
            )
        )
        official_service.open_registration(race.id)
        official_service.register(race.id, a["id"])
        official_service.register(race.id, b["id"])
        official_service.cancel_race(race.id, by_user_id=org["id"])

        cancel_payload = next(
            p for _, p in transport.calls if "cancelled" in str(p).lower()
        )
        assert cancel_payload.get("content") == "<@100>"


def test_registration_removed_mentions_only_affected_user(
    app: Flask, make_user
) -> None:
    org = make_user(username="org", role=Role.ORGANIZER)
    a = make_user(username="alice")
    with app.app_context():
        app.config["DISCORD_WEBHOOK_RACE_REGISTRATION_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)
        _set_discord_id(a["id"], "777")

        s = _season()
        race = official_service.create_race(
            official_service.CreateRaceRequest(
                season_id=s.id, name="R", organizer_user_id=org["id"]
            )
        )
        official_service.open_registration(race.id)
        reg = official_service.register(race.id, a["id"])
        official_service.remove_registration(
            race.id, reg.id, by_user_id=org["id"]
        )

        removed_payload = next(
            p for _, p in transport.calls
            if "Registration removed" in str(p)
        )
        assert removed_payload["content"] == "<@777>"


def test_draft_results_mentions_both_players(
    app: Flask, make_user
) -> None:
    host = make_user(username="host")
    opp = make_user(username="opp")
    with app.app_context():
        app.config["DISCORD_WEBHOOK_DRAFT_RESULTS_URL"] = "https://x"
        transport = FakeTransport()
        set_transport(transport)
        _set_discord_id(host["id"], "111")
        _set_discord_id(opp["id"], "222")

        s = _season()
        match = draft_service.create_match(
            draft_service.CreateMatchRequest(
                season_id=s.id,
                host_user_id=host["id"],
                umas_per_player=2,
                preset_pool="custom",
            )
        )
        match.opponent_user_id = opp["id"]
        match.status = DraftMatchStatus.COMPLETED
        db.session.commit()

        notif_services.notify_draft_results(
            match,
            winner_username="host",
            loser_username="opp",
            winner_delta=15,
            loser_delta=-15,
        )
        results_payloads = [
            p for _, p in transport.calls if "complete" in str(p).lower()
        ]
        assert len(results_payloads) == 1
        content = results_payloads[0]["content"]
        assert "<@111>" in content
        assert "<@222>" in content


# ---------- profile update path ----------


def test_profile_update_persists_discord_user_id(
    app: Flask, make_user
) -> None:
    u = make_user(username="alice")
    with app.app_context():
        from uma_ladder.models import User

        user = db.session.get(User, u["id"])
        profiles_service.update_profile(
            user,
            profiles_service.ProfileUpdate(discord_user_id="111222333444555666"),
        )
        profile = (
            db.session.query(UserProfile).filter_by(user_id=u["id"]).one()
        )
        assert profile.discord_user_id == "111222333444555666"


def test_profile_form_rejects_non_numeric_discord_user_id(
    client, app: Flask, make_user
) -> None:
    """The form's regex should bounce a username string — only the
    numeric snowflake is accepted (everything else means a stale
    handle, not a usable mention target)."""
    make_user(username="alice")
    resp = client.post(
        "/auth/login",
        data={"username": "alice", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    resp = client.post(
        "/profiles/me",
        data={
            "csrf_token": "x",
            "discord_user_id": "not-a-snowflake",
        },
    )
    # Form re-renders (200) instead of redirect (302) on validation
    # error — so we don't need to dig into the error text.
    assert resp.status_code == 200
    with app.app_context():
        profile = (
            db.session.query(UserProfile)
            .filter_by(user_id=db.session.query(UserProfile.user_id).scalar())
            .one_or_none()
        )
        if profile is not None:
            assert profile.discord_user_id is None
