"""Outfit-aware uma-ban semantics + result validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import (
    DraftBanType,
    DraftMatchStatus,
    RacePreset,
    Season,
    SeasonStatus,
    UmaCharacter,
    UmaOutfit,
)
from uma_ladder.models.enums import PresetSource
from uma_ladder.services import draft as draft_service
from uma_ladder.services.auth import RegistrationRequest, register_user


def _setup(app: Flask):
    """Spin up a season + two users + a preset; returns (season_id, host, opp)."""
    with app.app_context():
        now = datetime.now(UTC)
        s = Season(
            name="S",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=10),
            status=SeasonStatus.ACTIVE,
        )
        db.session.add(s)
        db.session.commit()
        host = register_user(
            RegistrationRequest(username="alice", password="password123")
        ).id
        opp = register_user(
            RegistrationRequest(username="bob", password="password123")
        ).id
        db.session.add(
            RacePreset(
                source=PresetSource.CUSTOM_BUILTIN,
                name="P",
                venue="Sapporo",
                surface="Turf",
                distance_meters=2000,
                distance_category="Medium",
                direction="Right",
                course_variant=None,
                max_runners=18,
                enabled=True,
            )
        )
        db.session.commit()
        return s.id, host, opp


def _drive_to_uma_ban(host_id: int, opp_id: int, season_id: int) -> int:
    match = draft_service.create_match(
        draft_service.CreateMatchRequest(
            season_id=season_id,
            host_user_id=host_id,
            umas_per_player=2,
            preset_pool="custom",
        )
    )
    draft_service.join_match(match.id, opp_id)
    draft_service.ready_up(match.id, host_id)
    draft_service.ready_up(match.id, opp_id)
    for uid in (host_id, opp_id):
        draft_service.submit_track_ban(
            match.id,
            uid,
            ban_type=DraftBanType.VENUE,
            condition_key="__skip__",
        )
    draft_service.randomize_preset(match.id)
    return match.id


def _make_char_with_outfits(slug: str, n_outfits: int) -> tuple[int, list[int]]:
    c = UmaCharacter(slug=slug, name_en=slug.replace("-", " ").title())
    db.session.add(c)
    db.session.commit()
    outfits = []
    for i in range(n_outfits):
        o = UmaOutfit(
            uma_character_id=c.id,
            costume_id=10000 + c.id * 100 + i + 1,
            title_en=f"{slug} outfit {i + 1}",
            released_globally=True,
            enabled=True,
        )
        db.session.add(o)
        db.session.commit()
        outfits.append(o.id)
    return c.id, outfits


# ---------- submit_uma_ban with outfit ----------


def test_uma_ban_with_outfit_recorded(app: Flask) -> None:
    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, outfits_a = _make_char_with_outfits("char-a", 2)
        char_b, _ = _make_char_with_outfits("char-b", 1)
        # Host bans Char A · outfit 1 specifically.
        ban = draft_service.submit_uma_ban(
            match_id, host, char_a, uma_outfit_id=outfits_a[0]
        )
        assert ban.uma_character_id == char_a
        assert ban.uma_outfit_id == outfits_a[0]


def test_uma_ban_outfit_must_match_character(app: Flask) -> None:
    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, _ = _make_char_with_outfits("char-a", 1)
        char_b, outfits_b = _make_char_with_outfits("char-b", 1)
        with pytest.raises(draft_service.UnknownBanTargetError):
            draft_service.submit_uma_ban(
                match_id, host, char_a, uma_outfit_id=outfits_b[0]
            )


def test_uma_ban_unknown_outfit(app: Flask) -> None:
    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, _ = _make_char_with_outfits("char-a", 1)
        with pytest.raises(draft_service.UnknownBanTargetError):
            draft_service.submit_uma_ban(
                match_id, host, char_a, uma_outfit_id=9999
            )


# ---------- result validation ----------


def test_uma_ban_requires_specific_outfit(app: Flask) -> None:
    """Banning an entire character is no longer allowed — the ban must
    target one specific costume from the character's repertoire."""
    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, _ = _make_char_with_outfits("char-a", 2)
        with pytest.raises(draft_service.UnknownBanTargetError):
            draft_service.submit_uma_ban(match_id, host, char_a)


def test_legacy_whole_character_ban_still_blocks_at_result(app: Flask) -> None:
    """Defensive: existing rows in the DB might predate the
    'must-target-outfit' rule, so result validation still rejects a result
    whose character matches a whole-character ban row inserted directly."""
    from uma_ladder.models import DraftBanType, DraftMatchBan

    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, outfits_a = _make_char_with_outfits("char-a", 2)
        char_b, outfits_b = _make_char_with_outfits("char-b", 1)
        # Host's ban inserted directly with outfit_id=None (simulates
        # legacy data from before this rule landed).
        db.session.add(
            DraftMatchBan(
                draft_match_id=match_id,
                user_id=host,
                ban_type=DraftBanType.UMA,
                uma_character_id=char_a,
                uma_outfit_id=None,
                locked_at=datetime.now(UTC),
            )
        )
        db.session.commit()
        # Opp bans Char B (specific outfit, the supported path).
        draft_service.submit_uma_ban(
            match_id, opp, char_b, uma_outfit_id=outfits_b[0]
        )
        draft_service.set_room_code(match_id, "RC", notify=False)

        with pytest.raises(draft_service.BannedCharacterUsedError):
            draft_service.submit_results(
                match_id,
                [
                    draft_service.DraftResultLine(user_id=host, placement=1),
                    draft_service.DraftResultLine(
                        user_id=opp,
                        placement=2,
                        uma_character_id=char_a,
                        uma_outfit_id=outfits_a[0],
                    ),
                ],
                confirmed_by_user_id=host,
                notify=False,
            )


def test_outfit_specific_ban_allows_other_outfit(app: Flask) -> None:
    """Ban with a specific outfit lets the other outfit of that char through."""
    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, outfits_a = _make_char_with_outfits("char-a", 2)
        char_b, outfits_b = _make_char_with_outfits("char-b", 1)
        # Host bans Char A · outfit 1 specifically.
        draft_service.submit_uma_ban(
            match_id, host, char_a, uma_outfit_id=outfits_a[0]
        )
        draft_service.submit_uma_ban(
            match_id, opp, char_b, uma_outfit_id=outfits_b[0]
        )
        draft_service.set_room_code(match_id, "RC", notify=False)

        # Using outfit 2 of char A — allowed.
        match = draft_service.submit_results(
            match_id,
            [
                draft_service.DraftResultLine(user_id=host, placement=1),
                draft_service.DraftResultLine(
                    user_id=opp,
                    placement=2,
                    uma_character_id=char_a,
                    uma_outfit_id=outfits_a[1],
                ),
            ],
            confirmed_by_user_id=host,
            notify=False,
        )
        assert match.status == DraftMatchStatus.COMPLETED


def test_outfit_specific_ban_blocks_that_outfit(app: Flask) -> None:
    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, outfits_a = _make_char_with_outfits("char-a", 2)
        char_b, outfits_b = _make_char_with_outfits("char-b", 1)
        draft_service.submit_uma_ban(
            match_id, host, char_a, uma_outfit_id=outfits_a[0]
        )
        draft_service.submit_uma_ban(
            match_id, opp, char_b, uma_outfit_id=outfits_b[0]
        )
        draft_service.set_room_code(match_id, "RC", notify=False)

        with pytest.raises(draft_service.BannedCharacterUsedError):
            draft_service.submit_results(
                match_id,
                [
                    draft_service.DraftResultLine(user_id=host, placement=1),
                    draft_service.DraftResultLine(
                        user_id=opp,
                        placement=2,
                        uma_character_id=char_a,
                        uma_outfit_id=outfits_a[0],  # banned exactly
                    ),
                ],
                confirmed_by_user_id=host,
                notify=False,
            )


def test_banned_uma_helpers_with_outfit_bans(app: Flask) -> None:
    season_id, host, opp = _setup(app)
    with app.app_context():
        match_id = _drive_to_uma_ban(host, opp, season_id)
        char_a, outfits_a = _make_char_with_outfits("char-a", 1)
        char_b, outfits_b = _make_char_with_outfits("char-b", 1)
        draft_service.submit_uma_ban(
            match_id, host, char_b, uma_outfit_id=outfits_b[0]
        )
        draft_service.submit_uma_ban(
            match_id, opp, char_a, uma_outfit_id=outfits_a[0]
        )

        # No char-only bans now that the service requires an outfit.
        assert draft_service.banned_uma_character_ids(match_id) == set()
        assert draft_service.banned_uma_outfit_ids(match_id) == {
            outfits_a[0],
            outfits_b[0],
        }
