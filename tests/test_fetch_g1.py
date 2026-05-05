"""G1 race fetcher mapping logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uma_ladder.services.fetch_gametora import (
    FakeGameToraTransport,
    GameToraError,
    HttpResponse,
    _coerce_g1,
    _distance_category,
    fetch_g1_races,
    write_g1_races_snapshot,
)


def _ok(payload) -> HttpResponse:
    return HttpResponse(
        ok=True,
        body=json.dumps(payload).encode("utf-8"),
        status_code=200,
        error=None,
    )


def _race(**overrides) -> dict:
    base = {
        "name_en": "Sample G1",
        "name_jp": "サンプル",
        "url_name": "sample-g1",
        "grade": 100,
        "track": 10006,  # Tokyo
        "direction": 2,  # Left
        "terrain": 1,  # Turf
        "distance": 2400,
        "entries": 18,
    }
    base.update(overrides)
    return base


def test_distance_category_buckets() -> None:
    assert _distance_category(1200) == "Sprint"
    assert _distance_category(1400) == "Sprint"
    assert _distance_category(1600) == "Mile"
    assert _distance_category(1800) == "Mile"
    assert _distance_category(2000) == "Medium"
    assert _distance_category(2400) == "Medium"
    assert _distance_category(2500) == "Long"
    assert _distance_category(3200) == "Long"


def test_coerce_g1_happy_path() -> None:
    g = _coerce_g1(_race())
    assert g is not None
    assert g.name_en == "Sample G1"
    assert g.venue == "Tokyo"
    assert g.surface == "Turf"
    assert g.direction == "Left"
    assert g.distance_meters == 2400
    assert g.distance_category == "Medium"
    assert g.max_runners == 18
    assert g.profile_url.endswith("/races/sample-g1")


def test_coerce_g1_skips_non_g1() -> None:
    assert _coerce_g1(_race(grade=200)) is None  # G2
    assert _coerce_g1(_race(grade=300)) is None  # G3


def test_coerce_g1_skips_foreign_venue() -> None:
    # 10201 = Longchamp (foreign).
    assert _coerce_g1(_race(track=10201)) is None


def test_coerce_g1_dirt_terrain() -> None:
    g = _coerce_g1(_race(terrain=2, distance=1600))
    assert g.surface == "Dirt"
    assert g.distance_category == "Mile"


def test_coerce_g1_right_direction() -> None:
    g = _coerce_g1(_race(direction=1, track=10005))  # Nakayama, Right
    assert g.direction == "Right"
    assert g.venue == "Nakayama"


def test_fetch_g1_races_keeps_distinct_names_at_same_track() -> None:
    """Different G1s sharing one physical configuration both survive
    the fetcher (e.g. Tokyo Yushun + Japanese Oaks at Tokyo 2400m turf
    left). Repeating the *same* race name (region / season variants
    upstream) still collapses to one row."""
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok(
                {"races": "v"}
            ),
            "https://gametora.com/data/umamusume/races.v.json": _ok(
                [
                    _race(name_en="Tokyo Yushun", track=10006, distance=2400, direction=2),
                    # Same physical track config as Yushun, different race.
                    _race(name_en="Japanese Oaks", track=10006, distance=2400, direction=2),
                    # Same race name as Yushun → genuine duplicate, drop.
                    _race(name_en="Tokyo Yushun", track=10006, distance=2400, direction=2),
                    _race(name_en="Other", track=10009, distance=2000, direction=1),
                    _race(name_en="G2 race", grade=200),
                ]
            ),
        }
    )
    rows = fetch_g1_races(transport, delay_seconds=0)
    names = sorted(r.name_en for r in rows)
    assert names == ["Japanese Oaks", "Other", "Tokyo Yushun"]


def test_fetch_g1_manifest_missing_key_raises() -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok({"other": "yes"}),
        }
    )
    with pytest.raises(GameToraError) as exc:
        fetch_g1_races(transport, delay_seconds=0)
    assert "races" in str(exc.value)


def test_snapshot_round_trip(tmp_path: Path) -> None:
    transport = FakeGameToraTransport(
        responses={
            "https://gametora.com/data/manifests/umamusume.json": _ok({"races": "v"}),
            "https://gametora.com/data/umamusume/races.v.json": _ok(
                [_race(name_en="Tokyo Yushun", url_name="tokyo-yushun")]
            ),
        }
    )
    rows = fetch_g1_races(transport, delay_seconds=0)
    out = tmp_path / "snap.json"
    write_g1_races_snapshot(rows, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source"]
    assert "GameTora" in payload["attribution"]
    assert payload["fetched_at"]
    assert len(payload["races"]) == 1
    r = payload["races"][0]
    assert r["name"] == "Tokyo Yushun"
    assert r["grade"] == "G1"
    assert r["venue"] == "Tokyo"
    assert r["external_source_url"].endswith("/races/tokyo-yushun")
