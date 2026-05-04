from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from uma_ladder.extensions import db
from uma_ladder.models import UmaCharacter, UmaOutfit
from uma_ladder.services import profiles as profiles_service
from uma_ladder.services.fetch_gametora import (
    FakeGameToraTransport,
    HttpResponse,
    _coerce_outfit,
    fetch_outfits,
)
from uma_ladder.services.seed_outfits import seed_outfits


def _ok(payload) -> HttpResponse:
    return HttpResponse(
        ok=True,
        body=json.dumps(payload).encode("utf-8"),
        status_code=200,
        error=None,
    )


def _outfit_row(**overrides) -> dict:
    base = {
        "char_id": 1001,
        "costume": 100101,
        "card_id": 100101,
        "title": None,
        "title_en": None,
        "title_en_gl": None,
        "title_jp": None,
        "rarity": 3,
        "release": "2021-02-24",
        "release_en": "2025-06-26",
        "release_ko": None,
    }
    base.update(overrides)
    return base


# ---------- _coerce_outfit ----------


def test_coerce_global_release_flag() -> None:
    o = _coerce_outfit(_outfit_row(release_en="2025-06-26"), region="global")
    assert o is not None
    assert o.released_globally is True
    assert o.image_url.endswith("chara_stand_1001_100101.png")


def test_coerce_no_global_release() -> None:
    o = _coerce_outfit(_outfit_row(release_en=None), region="global")
    assert o is not None
    assert o.released_globally is False


def test_coerce_jp_treats_as_released() -> None:
    o = _coerce_outfit(_outfit_row(release_en=None), region="jp")
    assert o is not None
    assert o.released_globally is True


def test_coerce_invalid_ids_returns_none() -> None:
    assert _coerce_outfit(_outfit_row(char_id=None), region="global") is None
    assert _coerce_outfit(_outfit_row(costume=None, card_id=None), region="global") is None


# ---------- fetch_outfits ----------


def test_fetch_outfits_filters_unreleased_by_default() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok(
                {"character-cards": "vX"}
            ),
            "https://gametora.com/data/umamusume/character-cards.vX.json": _ok(
                [
                    _outfit_row(release_en="2025-01-01", costume=100101, card_id=100101),
                    _outfit_row(release_en=None, costume=100102, card_id=100102),
                ]
            ),
        }
    )
    outfits = fetch_outfits(transport, delay_seconds=0)
    assert [o.costume_id for o in outfits] == [100101]


def test_fetch_outfits_include_unreleased() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok(
                {"character-cards": "vX"}
            ),
            "https://gametora.com/data/umamusume/character-cards.vX.json": _ok(
                [
                    _outfit_row(release_en="2025-01-01", costume=100101, card_id=100101),
                    _outfit_row(release_en=None, costume=100102, card_id=100102),
                ]
            ),
        }
    )
    outfits = fetch_outfits(transport, only_released=False, delay_seconds=0)
    assert [o.costume_id for o in outfits] == [100101, 100102]


# ---------- seed_outfits ----------


def _seed_outfit_file(tmp_path: Path, outfits: list[dict]) -> Path:
    p = tmp_path / "outfits.json"
    p.write_text(json.dumps({"outfits": outfits}), encoding="utf-8")
    return p


def _make_character(app: Flask, char_id: int = 1001, slug: str = "special-week"):
    with app.app_context():
        c = UmaCharacter(
            slug=slug,
            name_en=slug.replace("-", " ").title(),
            profile_url=f"https://gametora.com/umamusume/characters/{char_id}-{slug}",
        )
        db.session.add(c)
        db.session.commit()
        return c.id


def test_seed_outfits_inserts_then_skips(app: Flask, tmp_path: Path) -> None:
    char_local = _make_character(app)
    src = _seed_outfit_file(
        tmp_path,
        [
            {
                "char_id": 1001,
                "costume_id": 100101,
                "title_en": "Default",
                "image_url": "https://example/100101.png",
                "rarity": 3,
                "released_globally": True,
            },
            {
                "char_id": 1001,
                "costume_id": 100102,
                "title_en": "Special Dreamer",
                "image_url": "https://example/100102.png",
                "rarity": 3,
                "released_globally": True,
            },
        ],
    )
    with app.app_context():
        first = seed_outfits(src)
        assert (first.inserted, first.updated, first.skipped) == (2, 0, 0)
        second = seed_outfits(src)
        assert (second.inserted, second.updated, second.skipped) == (0, 0, 2)
        rows = db.session.query(UmaOutfit).filter_by(uma_character_id=char_local).all()
        assert {r.costume_id for r in rows} == {100101, 100102}


def test_seed_outfits_orphan_when_character_missing(app: Flask, tmp_path: Path) -> None:
    src = _seed_outfit_file(
        tmp_path,
        [{"char_id": 9999, "costume_id": 999901, "released_globally": True}],
    )
    with app.app_context():
        report = seed_outfits(src)
        assert report.orphaned == 1
        assert report.inserted == 0


def test_seed_outfits_prune_disables_missing(app: Flask, tmp_path: Path) -> None:
    char_local = _make_character(app)
    src1 = _seed_outfit_file(
        tmp_path,
        [
            {"char_id": 1001, "costume_id": 100101, "released_globally": True},
            {"char_id": 1001, "costume_id": 100102, "released_globally": True},
        ],
    )
    src2 = tmp_path / "later.json"
    src2.write_text(
        json.dumps(
            {
                "outfits": [
                    {"char_id": 1001, "costume_id": 100101, "released_globally": True}
                ]
            }
        ),
        encoding="utf-8",
    )
    with app.app_context():
        seed_outfits(src1)
        report = seed_outfits(src2, prune_missing=True)
        assert report.pruned == 1
        rows = (
            db.session.query(UmaOutfit)
            .filter_by(uma_character_id=char_local)
            .order_by(UmaOutfit.costume_id)
            .all()
        )
        assert [(r.costume_id, r.enabled) for r in rows] == [
            (100101, True),
            (100102, False),
        ]


# ---------- profile service ----------


def test_update_profile_outfit_must_match_character(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        c1 = UmaCharacter(slug="a", name_en="A")
        c2 = UmaCharacter(slug="b", name_en="B")
        db.session.add_all([c1, c2])
        db.session.commit()
        outfit_for_c2 = UmaOutfit(
            uma_character_id=c2.id,
            costume_id=200101,
            title_en="Wrong",
            released_globally=True,
        )
        db.session.add(outfit_for_c2)
        db.session.commit()

        from uma_ladder.models import User

        user = db.session.get(User, info["id"])
        with pytest.raises(profiles_service.UnknownOutfitError):
            profiles_service.update_profile(
                user,
                profiles_service.ProfileUpdate(
                    oshi_character_id=c1.id,
                    oshi_outfit_id=outfit_for_c2.id,
                ),
            )


def test_update_profile_clearing_character_clears_outfit(
    app: Flask, make_user
) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        c = UmaCharacter(slug="a", name_en="A")
        db.session.add(c)
        db.session.commit()
        o = UmaOutfit(uma_character_id=c.id, costume_id=100101, released_globally=True)
        db.session.add(o)
        db.session.commit()

        from uma_ladder.models import User

        user = db.session.get(User, info["id"])
        # Set them
        profiles_service.update_profile(
            user,
            profiles_service.ProfileUpdate(
                oshi_character_id=c.id, oshi_outfit_id=o.id
            ),
        )
        # Clear the character; outfit must clear too
        profile = profiles_service.update_profile(
            user, profiles_service.ProfileUpdate()
        )
        assert profile.oshi_character_id is None
        assert profile.oshi_outfit_id is None


def test_resolve_oshi_image_prefers_outfit(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        c = UmaCharacter(
            slug="a", name_en="A", image_url="https://example/char.png"
        )
        db.session.add(c)
        db.session.commit()
        o = UmaOutfit(
            uma_character_id=c.id,
            costume_id=100101,
            image_url="https://example/outfit.png",
            released_globally=True,
        )
        db.session.add(o)
        db.session.commit()

        from uma_ladder.models import User

        user = db.session.get(User, info["id"])
        profiles_service.update_profile(
            user,
            profiles_service.ProfileUpdate(
                oshi_character_id=c.id, oshi_outfit_id=o.id
            ),
        )
        profile = profiles_service.get_or_create_profile(user)
        assert profiles_service.resolve_oshi_image(profile) == "https://example/outfit.png"


def test_resolve_oshi_image_falls_back_to_character(app: Flask, make_user) -> None:
    info = make_user(username="alice", password="password123")
    with app.app_context():
        c = UmaCharacter(
            slug="a", name_en="A", image_url="https://example/char.png"
        )
        db.session.add(c)
        db.session.commit()

        from uma_ladder.models import User

        user = db.session.get(User, info["id"])
        profiles_service.update_profile(
            user, profiles_service.ProfileUpdate(oshi_character_id=c.id)
        )
        profile = profiles_service.get_or_create_profile(user)
        assert profiles_service.resolve_oshi_image(profile) == "https://example/char.png"


# ---------- routes ----------


def test_partial_outfits_route_requires_login(client) -> None:
    resp = client.get("/profiles/_partials/outfits")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_partial_outfits_route_returns_options(client, app: Flask, make_user) -> None:
    make_user(username="alice", password="password123")
    with app.app_context():
        c = UmaCharacter(slug="a", name_en="A")
        db.session.add(c)
        db.session.commit()
        c_id = c.id
        db.session.add(
            UmaOutfit(
                uma_character_id=c_id,
                costume_id=100101,
                title_en="The Default",
                released_globally=True,
            )
        )
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "alice", "password": "password123"},
    )
    resp = client.get(f"/profiles/_partials/outfits?character_id={c_id}")
    assert resp.status_code == 200
    assert b"The Default" in resp.data
