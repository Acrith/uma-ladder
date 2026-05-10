"""Tests for the unified Rankings page (PR-N1).

Covers:
- ``/rankings`` smoke + active-season default + mode tabs.
- Season-picker fallback when ``?season=`` is missing or unknown.
- Official + Draft tabs render the right ladder source.
- Pagination respects 25-rows-per-page boundary.
- Legacy redirects from /official/ladder/<id> + /draft/ladder/<id>
  preserve the season + the right mode.
- Top nav exposes the Rankings link.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftEloChange,
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceStatus,
    OfficialRaceVisibility,
    RacePreset,
    Season,
    SeasonStatus,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services.auth import RegistrationRequest, register_user

# ─── helpers ─────────────────────────────────────────────────────


def _make_season(
    app: Flask, name: str = "S1", status: str = SeasonStatus.ACTIVE
) -> int:
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name=name,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=89),
            status=status,
        )
        db.session.add(s)
        db.session.commit()
        return s.id


def _make_preset(app: Flask) -> int:
    with app.app_context():
        p = RacePreset(
            source=PresetSource.G1_IMPORT,
            name="Tokyo G1",
            venue="Tokyo",
            surface="Turf",
            distance_meters=2000,
            distance_category="Medium",
            direction="Left",
            course_variant=None,
            max_runners=18,
            enabled=True,
        )
        db.session.add(p)
        db.session.commit()
        return p.id


def _seed_official_result(
    app: Flask,
    *,
    season_id: int,
    preset_id: int,
    organizer_id: int,
    user_id: int,
    placement: int,
    points: int,
) -> None:
    """Bypass the registration/results-form chain — drop a completed
    race + result row directly so the ladder query has data."""
    with app.app_context():
        race = OfficialRace(
            season_id=season_id,
            name=f"Race for placement {placement}",
            organizer_user_id=organizer_id,
            preset_id=preset_id,
            status=OfficialRaceStatus.COMPLETED,
            visibility=OfficialRaceVisibility.PUBLIC,
        )
        db.session.add(race)
        db.session.flush()
        db.session.add(
            OfficialRaceResult(
                official_race_id=race.id,
                user_id=user_id,
                placement=placement,
                points=points,
            )
        )
        db.session.commit()


def _seed_draft_elo(
    app: Flask,
    *,
    season_id: int,
    user_id: int,
    opponent_user_id: int,
    rating: int,
) -> None:
    """Stamp a DraftEloChange so season_elo_ladder picks it up.
    DraftEloChange has NOT NULL draft_match_id + opponent_user_id +
    delta, so seed a minimal completed DraftMatch + opponent stub
    to anchor it. rating_after is what the ladder reads."""
    from uma_ladder.models import DraftMatch, DraftMatchStatus

    with app.app_context():
        match = DraftMatch(
            season_id=season_id,
            host_user_id=user_id,
            opponent_user_id=opponent_user_id,
            join_code=f"TEST-{user_id}-{rating}",
            status=DraftMatchStatus.COMPLETED,
        )
        db.session.add(match)
        db.session.flush()
        db.session.add(
            DraftEloChange(
                user_id=user_id,
                opponent_user_id=opponent_user_id,
                season_id=season_id,
                draft_match_id=match.id,
                rating_before=1500,
                rating_after=rating,
                delta=rating - 1500,
                outcome=1.0,
            )
        )
        db.session.commit()


# ─── /rankings smoke ─────────────────────────────────────────────


def test_rankings_index_anonymous_ok(client: FlaskClient) -> None:
    """Public — no login wall, mirrors how /profiles/* works."""
    resp = client.get("/rankings/")
    assert resp.status_code == 200


def test_rankings_no_season_renders_empty_state(
    client: FlaskClient,
) -> None:
    """No active season + nothing in ?season= → page still renders,
    body shows the 'pick a season' empty state."""
    resp = client.get("/rankings/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "No season selected" in body or "Pick a season" in body


def test_rankings_with_active_season_defaults_to_it(
    app: Flask, client: FlaskClient
) -> None:
    """No ?season= → falls back to the active season. Header should
    name it."""
    _make_season(app, name="Season Auto-Active")
    body = client.get("/rankings/").data.decode()
    assert "Season Auto-Active" in body


# ─── Mode tabs ──────────────────────────────────────────────────


def _register(app: Flask, username: str) -> int:
    """Register and return id while still inside the app context, so
    callers don't trip over DetachedInstanceError on .id access."""
    with app.app_context():
        u = register_user(
            RegistrationRequest(username=username, password="password123")
        )
        return u.id


def test_rankings_default_mode_is_official(
    app: Flask, client: FlaskClient
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    uid = _register(app, "acrith")
    _seed_official_result(
        app,
        season_id=sid,
        preset_id=pid,
        organizer_id=uid,
        user_id=uid,
        placement=1,
        points=10,
    )
    body = client.get(f"/rankings/?season={sid}").data.decode()
    # Official-mode column header
    assert "Points" in body
    assert "acrith" in body


def test_rankings_draft_mode_renders_elo_table(
    app: Flask, client: FlaskClient
) -> None:
    sid = _make_season(app)
    uid = _register(app, "ferro")
    opp = _register(app, "stub_opponent")
    _seed_draft_elo(
        app, season_id=sid, user_id=uid, opponent_user_id=opp, rating=1620
    )
    body = client.get(f"/rankings/?season={sid}&mode=draft").data.decode()
    assert "Rating" in body
    assert "ferro" in body
    assert "1620" in body


def test_rankings_invalid_mode_falls_back_to_official(
    app: Flask, client: FlaskClient
) -> None:
    """Garbage in ?mode= shouldn't 500 or render a third unknown
    table — silently fall back to the safe default."""
    sid = _make_season(app)
    pid = _make_preset(app)
    uid = _register(app, "fallback_user")
    _seed_official_result(
        app,
        season_id=sid,
        preset_id=pid,
        organizer_id=uid,
        user_id=uid,
        placement=1,
        points=10,
    )
    resp = client.get(f"/rankings/?season={sid}&mode=banana")
    assert resp.status_code == 200
    # Official-mode column header confirms fallback (Draft mode
    # would show "Rating" instead).
    assert b"Points" in resp.data
    assert b"Rating" not in resp.data


# ─── Pagination ─────────────────────────────────────────────────


def test_rankings_paginates_at_25_per_page(
    app: Flask, client: FlaskClient
) -> None:
    sid = _make_season(app)
    pid = _make_preset(app)
    org_id = _register(app, "org")
    # 30 distinct users → 30 ladder rows → 2 pages.
    for i in range(30):
        uid = _register(app, f"player{i:02d}")
        _seed_official_result(
            app,
            season_id=sid,
            preset_id=pid,
            organizer_id=org_id,
            user_id=uid,
            placement=1,
            points=100 - i,
        )

    body_p1 = client.get(f"/rankings/?season={sid}&page=1").data.decode()
    body_p2 = client.get(f"/rankings/?season={sid}&page=2").data.decode()

    # Page 1 carries the top scorer; page 2 has the bottom of the list.
    assert "player00" in body_p1
    assert "player00" not in body_p2
    # Page nav present once we have multiple pages
    assert "Page 1 / 2" in body_p1
    assert "Page 2 / 2" in body_p2


# ─── Season picker honours unknown id ───────────────────────────


def test_rankings_unknown_season_falls_back_to_empty_state(
    app: Flask, client: FlaskClient
) -> None:
    """Unknown ?season= shouldn't 500 — surface the 'pick a season'
    UI instead so the user can recover via the dropdown."""
    body = client.get("/rankings/?season=999999").data.decode()
    assert "No season selected" in body or "Pick a season" in body


# ─── Legacy redirects ───────────────────────────────────────────


def test_legacy_official_ladder_redirects_to_rankings(
    app: Flask, client: FlaskClient
) -> None:
    sid = _make_season(app)
    resp = client.get(f"/official/ladder/{sid}", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert "/rankings/" in loc
    assert f"season={sid}" in loc
    assert "mode=official" in loc


def test_legacy_draft_ladder_redirects_to_rankings(
    app: Flask, client: FlaskClient
) -> None:
    sid = _make_season(app)
    resp = client.get(f"/draft/ladder/{sid}", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert "/rankings/" in loc
    assert f"season={sid}" in loc
    assert "mode=draft" in loc


# ─── Nav exposure ───────────────────────────────────────────────


def test_top_nav_includes_rankings_link(client: FlaskClient) -> None:
    body = client.get("/").data.decode()
    assert "/rankings/" in body
    assert "Rankings" in body


# ─── Per-club scope filter (PR-O1) ──────────────────────────────


def _set_club_id(app: Flask, user_id: int, club_id: int | None) -> None:
    """Stamp UserProfile.club_id directly. In production this is set
    by services.profiles.sync_club_id_from_trainer; tests skip the
    uma.moe roundtrip."""
    from uma_ladder.models import User as _User
    from uma_ladder.services import profiles as profiles_service

    with app.app_context():
        user = db.session.get(_User, user_id)
        assert user is not None
        profile = profiles_service.get_or_create_profile(user)
        profile.club_id = club_id
        db.session.commit()


def _login(client: FlaskClient, username: str, password: str = "password123") -> None:
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_scope_toggle_hidden_when_anonymous(
    app: Flask, client: FlaskClient
) -> None:
    """No login → no toggle, regardless of ?scope= in URL."""
    _make_season(app)
    body = client.get("/rankings/").data.decode()
    assert "My club" not in body


def test_scope_toggle_hidden_when_user_has_no_club(
    app: Flask, client: FlaskClient
) -> None:
    """Logged in but no UserProfile.club_id synced → toggle stays
    hidden. Server-side guard also kicks in if the user types
    ?scope=club anyway (we silently fall back to all)."""
    _make_season(app)
    _register(app, "loner")
    _login(client, "loner")
    body = client.get("/rankings/").data.decode()
    assert "My club" not in body


def test_scope_toggle_shown_when_user_in_club(
    app: Flask, client: FlaskClient
) -> None:
    _make_season(app)
    uid = _register(app, "clubber")
    _set_club_id(app, uid, club_id=42)
    _login(client, "clubber")
    body = client.get("/rankings/").data.decode()
    assert "My club" in body
    assert "All players" in body


def test_scope_club_filters_official_ladder_to_club_members(
    app: Flask, client: FlaskClient
) -> None:
    """The whole point of the feature: ?scope=club restricts the
    official ladder to users sharing the viewer's club_id.

    We log in as a third "viewer" user (no results, just sets the
    club context for the request) so the logged-in name doesn't
    pollute substring assertions in the navbar.
    """
    sid = _make_season(app)
    pid = _make_preset(app)
    insider = _register(app, "insider")
    outsider = _register(app, "outsider")
    organizer = _register(app, "org_dummy")
    viewer = _register(app, "viewer")
    _set_club_id(app, insider, club_id=100)
    _set_club_id(app, outsider, club_id=200)  # different club
    _set_club_id(app, viewer, club_id=100)  # same club as insider
    _seed_official_result(
        app,
        season_id=sid,
        preset_id=pid,
        organizer_id=organizer,
        user_id=insider,
        placement=1,
        points=50,
    )
    _seed_official_result(
        app,
        season_id=sid,
        preset_id=pid,
        organizer_id=organizer,
        user_id=outsider,
        placement=1,
        points=80,  # higher, so they top the unfiltered ladder
    )
    _login(client, "viewer")

    # Default (no scope) — both visible, outsider on top by points.
    body_all = client.get(f"/rankings/?season={sid}").data.decode()
    assert "insider" in body_all
    assert "outsider" in body_all
    assert body_all.index("outsider") < body_all.index("insider")

    # scope=club — outsider gone, insider remains (viewer too if
    # they had any results, but they don't — fine).
    body_club = client.get(
        f"/rankings/?season={sid}&scope=club"
    ).data.decode()
    assert "insider" in body_club
    assert "outsider" not in body_club
    # Header reflects "club members" wording
    assert "club member" in body_club


def test_scope_club_filters_draft_ladder_too(
    app: Flask, client: FlaskClient
) -> None:
    """Filter applies symmetrically to the draft tab."""
    sid = _make_season(app)
    in_club = _register(app, "draft_in")
    out_club = _register(app, "draft_out")
    opp_in = _register(app, "draft_opp_in")
    opp_out = _register(app, "draft_opp_out")
    _set_club_id(app, in_club, club_id=100)
    _set_club_id(app, out_club, club_id=200)
    _seed_draft_elo(
        app, season_id=sid, user_id=in_club, opponent_user_id=opp_in,
        rating=1700,
    )
    _seed_draft_elo(
        app, season_id=sid, user_id=out_club, opponent_user_id=opp_out,
        rating=1800,
    )
    _login(client, "draft_in")

    body = client.get(
        f"/rankings/?season={sid}&mode=draft&scope=club"
    ).data.decode()
    assert "draft_in" in body
    assert "draft_out" not in body


def test_scope_club_silently_falls_back_when_no_club(
    app: Flask, client: FlaskClient
) -> None:
    """Anonymous viewer or user without club_id who lands on a
    URL with ?scope=club should NOT see an empty page — the
    server falls back to the regular ladder. Defends against
    stale URLs after a user leaves their club."""
    sid = _make_season(app)
    pid = _make_preset(app)
    organizer = _register(app, "org_fb")
    other = _register(app, "other_fb")
    _seed_official_result(
        app,
        season_id=sid,
        preset_id=pid,
        organizer_id=organizer,
        user_id=other,
        placement=1,
        points=10,
    )
    # NOTE: not logged in.
    body = client.get(
        f"/rankings/?season={sid}&scope=club"
    ).data.decode()
    # Falls back to all-players view → other_fb still visible.
    assert "other_fb" in body
    # Toggle still hidden (anonymous).
    assert "My club" not in body


def test_scope_toggle_preserves_mode(
    app: Flask, client: FlaskClient
) -> None:
    """Clicking 'My club' on the Draft tab should land on
    ?mode=draft&scope=club, not silently snap back to official."""
    sid = _make_season(app)
    uid = _register(app, "preserve_user")
    _set_club_id(app, uid, club_id=100)
    _login(client, "preserve_user")

    body = client.get(f"/rankings/?season={sid}&mode=draft").data.decode()
    # The 'My club' link in the toggle on the Draft view must
    # carry mode=draft, not default to mode=official.
    assert "mode=draft&amp;scope=club" in body or "scope=club&amp;mode=draft" in body
