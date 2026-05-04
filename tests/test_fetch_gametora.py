from __future__ import annotations

import json
from pathlib import Path

import pytest

from uma_ladder.services.fetch_gametora import (
    FakeGameToraTransport,
    GameToraError,
    HttpResponse,
    UrllibGameToraTransport,
    _coerce_character,
    fetch_characters,
    write_characters_snapshot,
)


def _ok(payload: dict | list) -> HttpResponse:
    return HttpResponse(
        ok=True,
        body=json.dumps(payload).encode("utf-8"),
        status_code=200,
        error=None,
    )


def _fail(status_code: int = 500, error: str = "boom") -> HttpResponse:
    return HttpResponse(ok=False, body=b"", status_code=status_code, error=error)


def _row(**overrides) -> dict:
    base = {
        "char_id": 1001,
        "url_name": "special-week",
        "en_name": "Special Week",
        "jp_name": "スペシャルウィーク",
        "playable": True,
        "playable_en": True,
        "playable_ko": True,
        "playable_zh_tw": True,
    }
    base.update(overrides)
    return base


def _transport_with(version: str, characters: list[dict]) -> FakeGameToraTransport:
    return FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok(
                {"characters": version}
            ),
            f"https://gametora.com/data/umamusume/characters.{version}.json": _ok(
                characters
            ),
        }
    )


# ---------- _coerce_character ----------


def test_coerce_happy_path() -> None:
    c = _coerce_character(_row())
    assert c is not None
    assert c.slug == "special-week"
    assert c.name_en == "Special Week"
    assert c.name_jp == "スペシャルウィーク"
    # GameTora addresses character pages by the 6-digit costume id, not
    # the 4-digit char_id — `<char_id>01-<slug>`.
    assert c.profile_url == (
        "https://gametora.com/umamusume/characters/100101-special-week"
    )
    assert c.image_url == (
        "https://gametora.com/images/umamusume/characters/"
        "chara_stand_1001_100101.png"
    )
    assert c.playable is True


def test_coerce_missing_slug_returns_none() -> None:
    assert _coerce_character(_row(url_name=None)) is None


def test_coerce_missing_name_en_returns_none() -> None:
    assert _coerce_character(_row(en_name="")) is None


def test_coerce_no_char_id_no_profile_url_no_image() -> None:
    c = _coerce_character(_row(char_id=None))
    assert c is not None
    assert c.profile_url is None
    assert c.image_url is None


# ---------- fetch_characters ----------


def test_fetch_filters_to_global_by_default() -> None:
    transport = _transport_with(
        "abc123",
        [
            _row(url_name="alice", en_name="Alice", playable_en=True),
            _row(url_name="jp-only", en_name="JP Only", playable_en=False, playable=True),
        ],
    )
    rows = fetch_characters(transport, delay_seconds=0)
    assert [r.slug for r in rows] == ["alice"]


def test_fetch_region_jp_uses_bare_playable() -> None:
    transport = _transport_with(
        "abc123",
        [
            _row(url_name="alice", en_name="Alice", playable=True, playable_en=True),
            _row(url_name="jp-only", en_name="JP Only", playable=True, playable_en=False),
            _row(url_name="ghost", en_name="Ghost NPC", playable=False, playable_en=False),
        ],
    )
    rows = fetch_characters(transport, region="jp", delay_seconds=0)
    assert sorted(r.slug for r in rows) == ["alice", "jp-only"]


def test_fetch_region_all_includes_any_playable() -> None:
    transport = _transport_with(
        "abc123",
        [
            _row(url_name="alice", en_name="Alice", playable=True, playable_en=True),
            _row(url_name="jp-only", en_name="JP Only", playable=True, playable_en=False),
            _row(url_name="ghost", en_name="Ghost", playable=False, playable_en=False),
        ],
    )
    rows = fetch_characters(transport, region="all", delay_seconds=0)
    assert sorted(r.slug for r in rows) == ["alice", "jp-only"]


def test_fetch_unknown_region_falls_back_to_global() -> None:
    transport = _transport_with(
        "abc123",
        [
            _row(url_name="alice", en_name="Alice", playable_en=True),
            _row(url_name="jp-only", en_name="JP Only", playable_en=False, playable=True),
        ],
    )
    rows = fetch_characters(transport, region="mars", delay_seconds=0)
    assert [r.slug for r in rows] == ["alice"]


def test_fetch_includes_non_playable_when_flag_set() -> None:
    transport = _transport_with(
        "abc123",
        [
            _row(url_name="alice", en_name="Alice", playable=True, playable_en=True),
            _row(url_name="ghost", en_name="Ghost", playable=False, playable_en=False),
        ],
    )
    rows = fetch_characters(
        transport, include_non_playable=True, delay_seconds=0
    )
    assert sorted(r.slug for r in rows) == ["alice", "ghost"]


def test_fetch_sorts_by_slug_for_determinism() -> None:
    transport = _transport_with(
        "v",
        [
            _row(url_name="zoo", en_name="Z"),
            _row(url_name="alpha", en_name="A"),
            _row(url_name="middle", en_name="M"),
        ],
    )
    rows = fetch_characters(transport, delay_seconds=0)
    assert [r.slug for r in rows] == ["alpha", "middle", "zoo"]


def test_fetch_skips_unparsable_rows() -> None:
    transport = _transport_with(
        "v",
        [
            _row(url_name="ok", en_name="OK"),
            "not a dict",  # type: ignore[list-item]
            {"weird": "shape"},
        ],
    )
    rows = fetch_characters(transport, delay_seconds=0)
    assert [r.slug for r in rows] == ["ok"]


def test_fetch_calls_manifest_then_data() -> None:
    transport = _transport_with("verX", [_row()])
    fetch_characters(transport, delay_seconds=0)
    assert transport.calls == [
        "https://gametora.com/data/manifests/umamusume.json",
        "https://gametora.com/data/umamusume/characters.verX.json",
    ]


def test_fetch_manifest_missing_key_raises() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok(
                {"other": "yes"}
            ),
        }
    )
    with pytest.raises(GameToraError) as exc:
        fetch_characters(transport, delay_seconds=0)
    assert "characters" in str(exc.value)


def test_fetch_manifest_404_raises() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _fail(
                status_code=404, error="not found"
            ),
        }
    )
    with pytest.raises(GameToraError) as exc:
        fetch_characters(transport, delay_seconds=0)
    assert "manifests" in str(exc.value)


def test_fetch_data_payload_not_list_raises() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok({"characters": "v"}),
            "https://gametora.com/data/umamusume/characters.v.json": _ok({"unexpected": "object"}),
        }
    )
    with pytest.raises(GameToraError):
        fetch_characters(transport, delay_seconds=0)


def test_fetch_data_404_raises() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok({"characters": "v"}),
            "https://gametora.com/data/umamusume/characters.v.json": _fail(
                status_code=404, error="gone"
            ),
        }
    )
    with pytest.raises(GameToraError):
        fetch_characters(transport, delay_seconds=0)


def test_fetch_invalid_json_body_raises() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": HttpResponse(
                ok=True, body=b"not json", status_code=200, error=None
            ),
        }
    )
    with pytest.raises(GameToraError) as exc:
        fetch_characters(transport, delay_seconds=0)
    assert "non-JSON" in str(exc.value)


# ---------- write_characters_snapshot ----------


def test_snapshot_round_trips_through_seed(tmp_path: Path) -> None:
    transport = _transport_with(
        "v",
        [
            _row(url_name="alice", en_name="Alice", jp_name="アリス"),
            _row(url_name="bob", en_name="Bob", jp_name=None, char_id=1002),
        ],
    )
    rows = fetch_characters(transport, delay_seconds=0)

    out = tmp_path / "snap.json"
    write_characters_snapshot(rows, out, region="global")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source"]
    assert payload["attribution"]
    assert payload["fetched_at"]
    assert payload["region"] == "global"
    assert [c["slug"] for c in payload["characters"]] == ["alice", "bob"]
    assert payload["characters"][0]["name_en"] == "Alice"
    assert payload["characters"][0]["name_jp"] == "アリス"
    assert "image_url" in payload["characters"][0]
    assert payload["characters"][0]["image_url"].endswith("chara_stand_1001_100101.png")
    # bob has no name_jp → omitted
    assert "name_jp" not in payload["characters"][1]
    # bob has char_id → profile_url + image_url emitted (6-digit URL)
    assert payload["characters"][1]["profile_url"].endswith("/100201-bob")
    assert payload["characters"][1]["image_url"].endswith("chara_stand_1002_100201.png")


def test_snapshot_consumed_by_seed_characters(app, tmp_path: Path) -> None:
    """End-to-end: fetch → write → seed."""
    from uma_ladder.extensions import db
    from uma_ladder.models import UmaCharacter
    from uma_ladder.services.seed_characters import seed_characters

    transport = _transport_with(
        "v",
        [_row(url_name="alice", en_name="Alice", jp_name="アリス")],
    )
    rows = fetch_characters(transport, delay_seconds=0)

    out = tmp_path / "snap.json"
    write_characters_snapshot(rows, out)
    with app.app_context():
        report = seed_characters(out)
        assert report.inserted == 1
        char = db.session.query(UmaCharacter).filter_by(slug="alice").one()
        assert char.name_en == "Alice"
        assert char.name_jp == "アリス"


# ---------- UrllibGameToraTransport ----------


def test_urllib_transport_constructs_with_default_user_agent() -> None:
    t = UrllibGameToraTransport()
    assert "uma-ladder-fetcher" in t.user_agent
