from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    RacePreset,
    Season,
    SeasonStatus,
    UmaCharacter,
)
from uma_ladder.models.enums import PresetSource


def _season(app: Flask) -> int:
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S1",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=89),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


def _add_preset(app: Flask) -> None:
    """Seed a small variety of presets so bans during the test don't all
    fall foul of the feasibility check (each ban must remove >=1 preset
    and leave >=1)."""
    with app.app_context():
        rows = [
            ("Sapporo Right", "Sapporo", "Right"),
            ("Tokyo Left", "Tokyo", "Left"),
            ("Hakodate Left", "Hakodate", "Left"),
            ("Niigata Right", "Niigata", "Right"),
        ]
        for name, venue, direction in rows:
            db.session.add(
                RacePreset(
                    source=PresetSource.CUSTOM_BUILTIN,
                    name=name,
                    venue=venue,
                    surface="Turf",
                    distance_meters=2000,
                    distance_category="Medium",
                    direction=direction,
                    course_variant=None,
                    max_runners=18,
                    enabled=True,
                )
            )
        db.session.commit()


def _add_characters(app: Flask) -> tuple[int, int, int]:
    with app.app_context():
        chars = [
            UmaCharacter(slug="char-a", name_en="Char A"),
            UmaCharacter(slug="char-b", name_en="Char B"),
            UmaCharacter(slug="char-c", name_en="Char C"),
        ]
        db.session.add_all(chars)
        db.session.commit()
        return chars[0].id, chars[1].id, chars[2].id


def _add_outfits(app: Flask, *char_ids: int) -> dict[int, int]:
    """Give each character a single default costume so it can be banned."""
    from uma_ladder.models import UmaOutfit

    with app.app_context():
        out = {}
        for cid in char_ids:
            o = UmaOutfit(
                uma_character_id=cid,
                costume_id=cid * 100 + 1,
                title_en=f"costume {cid}",
                released_globally=True,
                enabled=True,
            )
            db.session.add(o)
            db.session.commit()
            out[cid] = o.id
        return out


def _login(client: FlaskClient, username: str, password: str) -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_create_requires_login(client: FlaskClient, app: Flask) -> None:
    _season(app)
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_create_with_no_active_season_redirects_with_flash(
    client: FlaskClient, app: Flask, make_user
) -> None:
    make_user(username="alice", password="password123")
    _login(client, "alice", "password123")
    resp = client.post("/draft/new", data={"umas_per_player": 2, "preset_pool": "custom"})
    assert resp.status_code == 302
    assert "/draft/" in resp.headers["Location"]


def test_full_match_flow_via_routes(client: FlaskClient, app: Flask, make_user) -> None:
    _season(app)
    _add_preset(app)
    char_a, char_b, char_c = _add_characters(app)
    outfits = _add_outfits(app, char_a, char_b, char_c)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    # alice creates
    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        join_code = match.join_code

    # bob joins
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.post("/draft/join", data={"join_code": join_code}, follow_redirects=False)
    assert resp.status_code == 302

    # both ready
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        resp = client.post(f"/draft/{match_id}/ready", follow_redirects=False)
        assert resp.status_code == 302

    # both submit track bans (alice currently logged in)
    resp = client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "venue", "condition_key": "Tokyo"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    resp = client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "direction", "condition_key": "Left"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # randomize
    resp = client.post(f"/draft/{match_id}/randomize", follow_redirects=False)
    assert resp.status_code == 302

    # capture host/opp ids
    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        host_uid = match.host_user_id
        opp_uid = match.opponent_user_id

    # uma bans (currently logged in as bob); each ban targets a specific costume
    resp = client.post(
        f"/draft/{match_id}/uma-ban",
        data={
            "uma_character_id": str(char_a),
            "uma_outfit_id": str(outfits[char_a]),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    resp = client.post(
        f"/draft/{match_id}/uma-ban",
        data={
            "uma_character_id": str(char_b),
            "uma_outfit_id": str(outfits[char_b]),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # host pastes room code
    resp = client.post(
        f"/draft/{match_id}/room-code",
        data={"room_code": "RC-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # results
    resp = client.post(
        f"/draft/{match_id}/results",
        data={
            f"placement_{host_uid}": "1",
            f"placement_{opp_uid}": "2",
            f"uma_character_id_{host_uid}": str(char_c),
            f"uma_character_id_{opp_uid}": "",
            f"custom_uma_name_{opp_uid}": "Some Uma",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    # ladder shows alice (host) on top
    season_id = _season_for(match_id, app)
    resp = client.get(f"/draft/ladder/{season_id}")
    assert resp.status_code == 200
    body = resp.data
    assert body.index(b"alice") < body.index(b"bob")


def _season_for(match_id: int, app: Flask) -> int:
    from uma_ladder.models import DraftMatch

    with app.app_context():
        m = db.session.get(DraftMatch, match_id)
        return m.season_id


def test_dashboard_includes_elo_block(client: FlaskClient, app: Flask) -> None:
    _season(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Draft Elo" in resp.data


def test_uma_ban_tile_picker_marks_oshi_and_banned(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The uma-ban grid must:
    - render one tile per enabled outfit,
    - mark the viewer's Oshi outfit (fuchsia ring class),
    - mark already-banned outfits as disabled + grayscale."""
    _season(app)
    _add_preset(app)
    char_a, char_b, char_c = _add_characters(app)
    outfits = _add_outfits(app, char_a, char_b, char_c)
    alice = make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    # Set alice's oshi to char_a / outfit_a so it should get the fuchsia
    # ring marker on her view of the grid.
    from uma_ladder.services.profiles import ProfileUpdate, update_profile

    with app.app_context():
        from uma_ladder.models import User

        u = db.session.get(User, alice["id"])
        update_profile(
            u,
            ProfileUpdate(
                oshi_character_id=char_a, oshi_outfit_id=outfits[char_a]
            ),
        )

    # Drive the match into uma_ban_phase: alice creates, bob joins, both
    # ready, both track-ban, host randomizes.
    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        join_code = db.session.get(DraftMatch, match_id).join_code

    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post("/draft/join", data={"join_code": join_code})
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        client.post(f"/draft/{match_id}/ready")
    # alice currently logged in
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "venue", "condition_key": "Tokyo"},
    )
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "direction", "condition_key": "Left"},
    )
    client.post(f"/draft/{match_id}/randomize")

    # bob bans char_c's outfit — that tile should appear grayed on alice's view.
    client.post(
        f"/draft/{match_id}/uma-ban",
        data={"uma_outfit_id": str(outfits[char_c])},
    )

    # Alice fetches the page and the tile grid renders.
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()

    # Search bar present.
    assert 'id="uma-tile-search"' in body
    # All three outfits get a tile.
    for cid in (char_a, char_b, char_c):
        assert f'value="{outfits[cid]}"' in body

    # The Oshi marker is on the alice-oshi tile (char_a).
    assert "Oshi" in body
    # The Banned marker is on the bob-banned tile (char_c).
    assert "Banned" in body


# ---------- PR-J4: completed-match card surfaces OCR screenshots ----------


def test_completed_match_renders_ocr_source_screenshots(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """When a draft match's results were submitted via the OCR
    flow, the completed card should show thumbnails of the source
    screenshots so participants can verify the parse."""
    import io

    from uma_ladder.models import (
        DraftMatch,
        DraftMatchStatus,
        Season,
        SeasonStatus,
    )
    from uma_ladder.services import ocr as ocr_service

    _add_preset(app)
    host = make_user(username="host", password="password123")
    opp = make_user(username="opp", password="password123")

    with app.app_context():
        app.config["OCR_PROVIDER"] = "mock"
        from datetime import UTC, datetime, timedelta

        s = db.session.query(Season).first()
        if s is None:
            now = datetime.now(UTC)
            s = Season(
                name="S",
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=10),
                status=SeasonStatus.ACTIVE,
            )
            db.session.add(s)
            db.session.commit()

        match = DraftMatch(
            season_id=s.id,
            host_user_id=host["id"],
            opponent_user_id=opp["id"],
            join_code="J4SCR",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
        )
        db.session.add(match)
        db.session.commit()
        match_id = match.id

        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
            "53de0000000c4944415408d76368686800000005000170d80b240000000049"
            "454e44ae426082"
        )
        from werkzeug.datastructures import FileStorage

        image = ocr_service.save_uploaded_image(
            FileStorage(
                stream=io.BytesIO(png),
                filename="r.png",
                content_type="image/png",
            ),
            uploader_user_id=host["id"],
        )
        attempt = ocr_service.run_parse(image)
        ocr_service.confirm_parse(
            attempt.id,
            confirmed_by_user_id=host["id"],
            draft_match_id=match_id,
        )
        image_id = image.id

    # Opponent loads the completed-match page — the screenshot
    # thumbnail must be present (which also implies the link grants
    # them serve_image access via PR-J4).
    _login(client, "opp", "password123")
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "OCR source screenshot" in body
    assert f"/ocr/uploads/{image_id}" in body


def test_completed_match_without_ocr_omits_screenshot_strip(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Manually-entered (non-OCR) results don't trip the screenshot
    section — no empty card, no broken thumbnails."""
    from uma_ladder.models import (
        DraftMatch,
        DraftMatchStatus,
        Season,
        SeasonStatus,
    )

    _add_preset(app)
    host = make_user(username="host", password="password123")
    make_user(username="opp", password="password123")

    with app.app_context():
        from datetime import UTC, datetime, timedelta

        s = db.session.query(Season).first()
        if s is None:
            now = datetime.now(UTC)
            s = Season(
                name="S",
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=10),
                status=SeasonStatus.ACTIVE,
            )
            db.session.add(s)
            db.session.commit()
        match = DraftMatch(
            season_id=s.id,
            host_user_id=host["id"],
            join_code="J4NOSCR",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.COMPLETED,
        )
        db.session.add(match)
        db.session.commit()
        match_id = match.id

    _login(client, "host", "password123")
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    assert "OCR source screenshot" not in resp.data.decode()


# ---------- PR-J5: track-ban label uses "Distance" / "Direction" ----------


def _add_diverse_presets(app: Flask) -> None:
    """Augment _add_preset with Dirt + Sprint variants so the
    feasibility filter doesn't strip Surface and Distance from the
    track-ban dropdown — both columns need >=2 distinct values for
    a ban to be non-redundant."""
    with app.app_context():
        db.session.add_all(
            [
                RacePreset(
                    source=PresetSource.CUSTOM_BUILTIN,
                    name="Tokyo Sprint",
                    venue="Tokyo",
                    surface="Turf",
                    distance_meters=1200,
                    distance_category="Sprint",
                    direction="Left",
                    course_variant=None,
                    max_runners=18,
                    enabled=True,
                ),
                RacePreset(
                    source=PresetSource.CUSTOM_BUILTIN,
                    name="Sapporo Dirt",
                    venue="Sapporo",
                    surface="Dirt",
                    distance_meters=2000,
                    distance_category="Medium",
                    direction="Right",
                    course_variant=None,
                    max_runners=18,
                    enabled=True,
                ),
            ]
        )
        db.session.commit()


def test_track_ban_dropdown_uses_short_labels(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The dropdown option for distance_category used to render
    'Distance category' (lower-case 'c' from a capitalize filter on
    underscore-replace). The chip label below was the same wrap
    offender. Both now read 'Distance' / 'Direction' / 'Surface' /
    'Venue' so the panel doesn't truncate uma bans."""
    _season(app)
    _add_preset(app)
    _add_diverse_presets(app)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        join_code = db.session.get(DraftMatch, match_id).join_code

    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post("/draft/join", data={"join_code": join_code})
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        client.post(f"/draft/{match_id}/ready")

    import re

    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    sel_html = re.search(
        r'<select id="track-ban-type"[^>]*>.*?</select>', body, re.DOTALL
    )
    assert sel_html, "track-ban-type select not in body"
    sel = sel_html.group(0)
    assert ">Distance<" in sel
    assert ">Direction<" in sel
    assert ">Surface<" in sel
    assert ">Venue<" in sel
    # Old wrap-prone label gone.
    assert ">Distance category<" not in sel


def test_track_ban_panel_chip_uses_glyph_and_label(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """After alice submits a distance_category track ban, the panel
    chip should render 'Banned · Distance' (not 'distance category')
    and the round glyph should be 'Dt' (not 'DC' / 'D')."""
    _season(app)
    _add_preset(app)
    _add_diverse_presets(app)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        join_code = db.session.get(DraftMatch, match_id).join_code

    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post("/draft/join", data={"join_code": join_code})
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        client.post(f"/draft/{match_id}/ready")

    # alice bans Medium (distance_category).
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "distance_category", "condition_key": "Medium"},
    )
    resp = client.get(f"/draft/{match_id}")
    body = resp.data.decode()

    assert "Banned · Distance" in body
    # Glyph chip wraps the literal in a div; substring match suffices.
    assert ">Dt<" in body
    # Old wrap-prone variants gone.
    assert "Banned · distance category" not in body
    assert ">DC<" not in body


# ---------- PR-J6: uma ban must be truly blind during uma_ban_phase ----------


def _drive_to_uma_ban_phase(
    client: FlaskClient, app: Flask, *, host: str, opp: str
) -> int:
    """Helper — alice creates, bob joins, both ready, both
    track-ban, alice randomizes. Returns match_id with status
    UMA_BAN_PHASE and no uma bans yet."""
    _login(client, host, "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    from uma_ladder.models import DraftMatch

    with app.app_context():
        join_code = db.session.get(DraftMatch, match_id).join_code

    client.post("/auth/logout")
    _login(client, opp, "password123")
    client.post("/draft/join", data={"join_code": join_code})
    for username in (opp, host):
        client.post("/auth/logout")
        _login(client, username, "password123")
        client.post(f"/draft/{match_id}/ready")
    client.post("/auth/logout")
    _login(client, host, "password123")
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "venue", "condition_key": "Tokyo"},
    )
    client.post("/auth/logout")
    _login(client, opp, "password123")
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "direction", "condition_key": "Left"},
    )
    client.post(f"/draft/{match_id}/randomize")
    return match_id


def test_uma_ban_phase_hides_opponent_pick_from_view(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """The 5s lobby poll must not leak the opponent's uma ban to
    the still-deciding player. After bob bans char_c, alice's
    detail-page render during uma_ban_phase must NOT mention the
    banned character/outfit name OR mark its tile disabled."""
    _season(app)
    _add_preset(app)
    char_a, char_b, char_c = _add_characters(app)
    outfits = _add_outfits(app, char_a, char_b, char_c)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    match_id = _drive_to_uma_ban_phase(
        client, app, host="alice", opp="bob"
    )

    # bob bans char_c's outfit. alice has not banned yet.
    client.post(
        f"/draft/{match_id}/uma-ban",
        data={"uma_outfit_id": str(outfits[char_c])},
    )
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()

    # Sanity — we ARE still in the blind phase.
    from uma_ladder.models import DraftMatch, DraftMatchStatus

    with app.app_context():
        assert (
            db.session.get(DraftMatch, match_id).status
            == DraftMatchStatus.UMA_BAN_PHASE
        )

    # Leak A: bob's panel must show "picking Uma ban…" — the
    # template flips to that placeholder iff there's no
    # uma_ban for that side in the rendered context. With the
    # bug, alice's render of bob's panel would say "Banned"
    # + character name instead.
    assert "picking Uma ban…" in body, (
        "leak A: opponent panel doesn't show the blind placeholder"
    )
    # Leak B: tile picker must not have a disabled radio for the
    # opponent-banned outfit. Alice has banned NOTHING yet, so a
    # disabled picker radio in her view can only come from a leak.
    import re

    disabled_picker_radios = re.findall(
        r'<input\s+type="radio"\s+name="uma_outfit_id"\b[^>]*?\bdisabled\b',
        body,
        re.DOTALL,
    )
    assert disabled_picker_radios == [], (
        f"leak B: {len(disabled_picker_radios)} picker tile(s) "
        f"rendered as disabled in alice's view"
    )


def test_uma_ban_reveal_after_phase_advances(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Once both submit and the phase auto-advances to
    room_code_pending, both bans are visible — the blind filter
    only fires during uma_ban_phase itself, not after reveal."""
    _season(app)
    _add_preset(app)
    char_a, char_b, char_c = _add_characters(app)
    outfits = _add_outfits(app, char_a, char_b, char_c)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    match_id = _drive_to_uma_ban_phase(
        client, app, host="alice", opp="bob"
    )

    # Both submit uma bans — bob bans C, alice bans B.
    client.post(
        f"/draft/{match_id}/uma-ban",
        data={"uma_outfit_id": str(outfits[char_c])},
    )
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(
        f"/draft/{match_id}/uma-ban",
        data={"uma_outfit_id": str(outfits[char_b])},
    )

    # Phase should now be past uma_ban_phase. Alice loads — both
    # banned-uma chips should be present.
    from uma_ladder.models import DraftMatch, DraftMatchStatus

    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        assert match.status != DraftMatchStatus.UMA_BAN_PHASE

    resp = client.get(f"/draft/{match_id}")
    body = resp.data.decode()
    assert "Char C" in body, "reveal: opponent ban must be visible post-phase"
    assert "Char B" in body, "reveal: own ban must remain visible"


def test_uma_ban_own_pick_visible_to_self_during_phase(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Self-suppression check: the blind filter must NOT hide the
    current user's own ban from themselves. Alice bans char_b,
    bob hasn't banned yet, alice reloads — char_b must show on
    alice's side panel."""
    _season(app)
    _add_preset(app)
    char_a, char_b, char_c = _add_characters(app)
    outfits = _add_outfits(app, char_a, char_b, char_c)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    match_id = _drive_to_uma_ban_phase(
        client, app, host="alice", opp="bob"
    )

    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(
        f"/draft/{match_id}/uma-ban",
        data={"uma_outfit_id": str(outfits[char_b])},
    )
    resp = client.get(f"/draft/{match_id}")
    body = resp.data.decode()

    # Phase still blind for bob (he hasn't banned).
    from uma_ladder.models import DraftMatch, DraftMatchStatus

    with app.app_context():
        assert (
            db.session.get(DraftMatch, match_id).status
            == DraftMatchStatus.UMA_BAN_PHASE
        )

    assert "Char B" in body, "self-suppression: alice can't see her own ban"


def test_track_ban_phase_still_shows_opponent_track_ban(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Track bans were never blind. The PR-J6 filter only fires for
    UMA-typed bans during uma_ban_phase — make sure the implementation
    doesn't accidentally drop opponent track-bans during the
    track-ban phase."""
    _season(app)
    _add_preset(app)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        join_code = db.session.get(DraftMatch, match_id).join_code

    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post("/draft/join", data={"join_code": join_code})
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        client.post(f"/draft/{match_id}/ready")

    # bob bans Tokyo. alice loads in track_ban_phase — Tokyo should
    # be visible in bob's panel chip.
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "venue", "condition_key": "Tokyo"},
    )
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    resp = client.get(f"/draft/{match_id}")
    body = resp.data.decode()
    assert "Banned · Venue" in body
    assert "Tokyo" in body


# ---------- PR-J3: ban forms must survive the 5s lobby poll ----------


def test_track_ban_form_marked_hx_preserve(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Regression — second-to-ban users were losing their selections
    every 5s because the lobby poll did an outerHTML swap on the
    whole detail body, re-rendering the form from scratch. The fix
    is `hx-preserve="true"` on the in-progress form so htmx keeps the
    live DOM (and the user's mid-edit state) across swaps. If a
    refactor drops the attribute, this test fails."""
    _season(app)
    _add_preset(app)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        join_code = db.session.get(DraftMatch, match_id).join_code

    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post("/draft/join", data={"join_code": join_code})
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        client.post(f"/draft/{match_id}/ready")

    # alice is logged in and in track-ban phase. Her form must be
    # tagged hx-preserve so the 5s poll doesn't wipe her input.
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'id="draft-track-ban-form"' in body
    assert 'hx-preserve="true"' in body


def test_uma_ban_form_marked_hx_preserve(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Same regression as track-ban, blind-uma-ban variant."""
    _season(app)
    _add_preset(app)
    char_a, char_b, char_c = _add_characters(app)
    _add_outfits(app, char_a, char_b, char_c)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")

    _login(client, "alice", "password123")
    resp = client.post(
        "/draft/new",
        data={"umas_per_player": 2, "preset_pool": "custom"},
        follow_redirects=False,
    )
    match_id = int(resp.headers["Location"].rsplit("/", 1)[-1])

    from uma_ladder.models import DraftMatch

    with app.app_context():
        join_code = db.session.get(DraftMatch, match_id).join_code

    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post("/draft/join", data={"join_code": join_code})
    for username in ("bob", "alice"):
        client.post("/auth/logout")
        _login(client, username, "password123")
        client.post(f"/draft/{match_id}/ready")
    # alice -> track ban, bob -> track ban, alice randomize.
    client.post("/auth/logout")
    _login(client, "alice", "password123")
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "venue", "condition_key": "Tokyo"},
    )
    client.post("/auth/logout")
    _login(client, "bob", "password123")
    client.post(
        f"/draft/{match_id}/track-ban",
        data={"ban_type": "direction", "condition_key": "Left"},
    )
    client.post(f"/draft/{match_id}/randomize")

    # bob is in uma_ban_phase — his form must carry hx-preserve.
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'id="draft-uma-ban-form"' in body
    assert 'hx-preserve="true"' in body


# ---------- PR-I6: room-code phase polish ----------


def test_opponent_can_post_room_code(client: FlaskClient, app: Flask, make_user) -> None:
    """Either match participant — not just the match host — can
    submit the in-game room code. Use case: opponent has stronger
    umas trained for the rolled track, so they host the lobby."""
    from uma_ladder.models import DraftMatch, DraftMatchStatus

    _season(app)
    make_user(username="alice", password="password123")  # host
    make_user(username="bob", password="password123")    # opponent

    # Set up a match in ROOM_CODE_PENDING directly (skipping the ban
    # phase scaffolding — service layer is the contract under test).
    with app.app_context():
        preset = RacePreset(
            source=PresetSource.G1_IMPORT,
            name="Tokyo G1",
            grade="G1",
            venue="Tokyo",
            surface="Turf",
            distance_meters=2000,
            distance_category="Medium",
            direction="Left",
            course_variant=None,
            max_runners=18,
            enabled=True,
        )
        db.session.add(preset)
        db.session.commit()
        from uma_ladder.services import auth as auth_service

        host_id = auth_service.find_user_by_username("alice").id
        opp_id = auth_service.find_user_by_username("bob").id
        s = db.session.query(Season).first()
        m = DraftMatch(
            season_id=s.id,
            host_user_id=host_id,
            opponent_user_id=opp_id,
            join_code="JOINCODE1",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.ROOM_CODE_PENDING,
            selected_preset_id=preset.id,
        )
        db.session.add(m)
        db.session.commit()
        match_id = m.id

    # Bob (opponent) submits the room code.
    _login(client, "bob", "password123")
    resp = client.post(
        f"/draft/{match_id}/room-code",
        data={"room_code": "FROM-OPP"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        match = db.session.get(DraftMatch, match_id)
        assert match.room_code == "FROM-OPP"
        assert match.status == DraftMatchStatus.ROOM_CODE_AVAILABLE


def test_room_code_card_shows_setup_walkthrough(
    client: FlaskClient, app: Flask, make_user
) -> None:
    """Once a preset is rolled, the room-code card carries a
    collapsible walkthrough of the in-game setup steps with values
    pulled from the rolled preset + race conditions."""
    from uma_ladder.models import DraftMatch, DraftMatchStatus

    _season(app)
    make_user(username="alice", password="password123")
    make_user(username="bob", password="password123")
    with app.app_context():
        preset = RacePreset(
            source=PresetSource.CUSTOM_BUILTIN,
            name="Sapporo Long Custom",
            venue="Sapporo",
            surface="Turf",
            distance_meters=3600,
            distance_category="Long",
            direction="Right",
            course_variant="Inner",
            max_runners=14,
            enabled=True,
        )
        db.session.add(preset)
        db.session.commit()
        from uma_ladder.services import auth as auth_service

        host_id = auth_service.find_user_by_username("alice").id
        opp_id = auth_service.find_user_by_username("bob").id
        s = db.session.query(Season).first()
        m = DraftMatch(
            season_id=s.id,
            host_user_id=host_id,
            opponent_user_id=opp_id,
            join_code="JOINCODE2",
            umas_per_player=2,
            preset_pool="custom",
            status=DraftMatchStatus.ROOM_CODE_PENDING,
            selected_preset_id=preset.id,
            race_season="Winter",
            weather="Snowy",
            ground_condition="Soft",
        )
        db.session.add(m)
        db.session.commit()
        match_id = m.id

    _login(client, "alice", "password123")
    resp = client.get(f"/draft/{match_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Walkthrough heading.
    assert "In-game room setup walkthrough" in body
    # Custom-track branch — the user picks Trainers Cup tab.
    assert "Trainers Cup (Custom)" in body
    # Track conditions echoed exactly so host can copy across.
    assert "Sapporo" in body
    assert "3600m" in body
    assert "Long" in body
    assert "Right" in body
    assert "Inner" in body
    # Race-day conditions appear in advanced-mode steps.
    assert "Winter" in body
    assert "Snowy" in body
    assert "Soft" in body
    # umas_per_player echoed.
    assert "<strong class=\"text-cyan-200\">2</strong>" in body
    # Public-off warning.
    assert "Make public: Off" in body
