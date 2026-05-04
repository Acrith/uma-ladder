"""Politely fetch Uma character data from GameTora.

Run as a one-off CLI command (``flask uma fetch-gametora-characters``)
when GameTora updates and you want to refresh the local seed snapshot.
**Never** call this from a request handler.

How it works
------------
GameTora is a Next.js app that loads its data via a versioned manifest:

  1. ``/data/manifests/umamusume.json`` maps a logical key (e.g.
     ``"characters"``) to a content-hash version like ``"426c1dbb"``.
  2. ``/data/umamusume/<key>.<version>.json`` returns the actual data.

The version changes whenever the upstream data is regenerated, so the
manifest gives us a stable indirection. If the manifest itself moves,
this fetcher will surface a clear error and the user updates the
shape — that's the design: deliberate, re-runnable, well-defined,
fragile only at known seams.

Attribution + politeness
------------------------
- Single request per logical resource (1 for manifest, 1 for the data
  blob). No iteration that fans out to per-character pages.
- Identifying User-Agent including a contact link.
- Sleep ``DELAY_SECONDS`` between requests when more than one is needed.

The output JSON is written in the same shape that
``services.seed_characters`` consumes, so fetch + commit + run
``flask uma seed-characters`` is the full refresh path.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GAMETORA_BASE = "https://gametora.com"
DEFAULT_USER_AGENT = (
    "uma-ladder-fetcher/0.1 (+https://github.com/Acrith/uma-ladder; "
    "polite one-off snapshot refresh)"
)
DEFAULT_TIMEOUT = 20.0
DELAY_SECONDS = 1.0
DATA_ATTRIBUTION = (
    "Character data sourced from GameTora "
    "(https://gametora.com/umamusume/characters); "
    "not affiliated with Cygames."
)


class GameToraError(Exception):
    pass


# ---------- Transport ----------


@dataclass(frozen=True)
class HttpResponse:
    ok: bool
    body: bytes
    status_code: int | None
    error: str | None


class GameToraTransport(ABC):
    @abstractmethod
    def get(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> HttpResponse:
        ...


class UrllibGameToraTransport(GameToraTransport):
    def __init__(self, *, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self.user_agent = user_agent

    def get(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> HttpResponse:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return HttpResponse(
                    ok=200 <= resp.status < 300,
                    body=body,
                    status_code=resp.status,
                    error=None,
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                ok=False, body=b"", status_code=exc.code, error=str(exc)
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return HttpResponse(ok=False, body=b"", status_code=None, error=str(exc))


@dataclass
class FakeGameToraTransport(GameToraTransport):
    """Test transport. ``responses`` maps URL → HttpResponse."""

    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> HttpResponse:  # noqa: ARG002
        self.calls.append(url)
        if url not in self.responses:
            return HttpResponse(
                ok=False, body=b"", status_code=404, error=f"no fake response for {url}"
            )
        return self.responses[url]


# ---------- Data shape mapping ----------


# Logical region → GameTora column suffix. "global" maps to the EN/global
# server, which is what most western players actually have access to.
REGION_FIELD: dict[str, str] = {
    "global": "en",
    "en": "en",
    "ko": "ko",
    "zh_tw": "zh_tw",
    "tw": "zh_tw",
    "jp": "",  # the bare `playable` flag tracks the JP server
}


@dataclass(frozen=True)
class FetchedCharacter:
    slug: str
    name_en: str
    name_jp: str | None
    profile_url: str | None
    image_url: str | None
    char_id: int | None
    playable: bool

    def to_seed_row(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "slug": self.slug,
            "name_en": self.name_en,
        }
        if self.name_jp:
            out["name_jp"] = self.name_jp
        if self.profile_url:
            out["profile_url"] = self.profile_url
        if self.image_url:
            out["image_url"] = self.image_url
        return out


def _build_image_url(char_id: int | None) -> str | None:
    """Default-outfit stand image URL on GameTora's CDN.

    Pattern: `chara_stand_<char_id>_<char_id>01.png` — the trailing 01 is
    the default-outfit suffix. Confirmed by HEAD requests during fetcher
    development.
    """
    if char_id is None:
        return None
    return (
        f"{GAMETORA_BASE}/images/umamusume/characters/"
        f"chara_stand_{char_id}_{char_id}01.png"
    )


def _is_playable_in_region(raw: dict[str, Any], region: str) -> bool:
    """`playable_<suffix>` for region-aware filter; bare `playable` for JP."""
    suffix = REGION_FIELD.get(region)
    if suffix is None:
        return False
    field = "playable" if not suffix else f"playable_{suffix}"
    return bool(raw.get(field, False))


def _coerce_character(raw: dict[str, Any]) -> FetchedCharacter | None:
    """Map one GameTora row to our internal shape. Returns None if unusable."""
    slug = raw.get("url_name")
    name_en = raw.get("en_name")
    if not slug or not name_en:
        return None
    char_id = raw.get("char_id") if isinstance(raw.get("char_id"), int) else None
    # GameTora's character pages are addressed by the 6-digit costume id
    # (`char_id * 100 + outfit_suffix`). The default-outfit URL is the
    # canonical character page, so we anchor profile_url there.
    profile_url = (
        f"{GAMETORA_BASE}/umamusume/characters/{char_id * 100 + 1}-{slug}"
        if char_id is not None
        else None
    )
    return FetchedCharacter(
        slug=slug,
        name_en=name_en,
        name_jp=raw.get("jp_name"),
        profile_url=profile_url,
        image_url=_build_image_url(char_id),
        char_id=char_id,
        playable=bool(raw.get("playable", False)),
    )


# ---------- Fetcher ----------


def fetch_characters(
    transport: GameToraTransport | None = None,
    *,
    region: str = "global",
    include_non_playable: bool = False,
    delay_seconds: float = DELAY_SECONDS,
) -> list[FetchedCharacter]:
    """Fetch the upstream character list and map it to our internal shape.

    Returns rows sorted by slug for deterministic output.

    `region` selects which server's playable flag we filter by. Default is
    ``"global"`` (Cygames' EN/global server) so the seeded list matches
    what most western players actually have access to. Pass ``"jp"`` to
    include everything that's playable on the Japan server, ``"all"`` to
    skip the region filter entirely. Unknown values fall back to global.

    `include_non_playable` adds event/NPC characters regardless of the
    region filter — usually you want this off.
    """
    if region not in REGION_FIELD and region != "all":
        region = "global"

    t = transport or UrllibGameToraTransport()

    manifest = _get_json(t, f"{GAMETORA_BASE}/data/manifests/umamusume.json")
    version = manifest.get("characters")
    if not isinstance(version, str) or not version:
        raise GameToraError(
            "manifest missing 'characters' key — upstream layout may have changed"
        )

    if delay_seconds:
        time.sleep(delay_seconds)

    raw = _get_json(
        t, f"{GAMETORA_BASE}/data/umamusume/characters.{version}.json"
    )
    if not isinstance(raw, list):
        raise GameToraError("characters payload is not a list")

    characters: list[FetchedCharacter] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        coerced = _coerce_character(row)
        if coerced is None:
            continue
        if include_non_playable:
            characters.append(coerced)
            continue
        if region == "all":
            if coerced.playable:
                characters.append(coerced)
            continue
        if _is_playable_in_region(row, region):
            characters.append(coerced)

    characters.sort(key=lambda c: c.slug)
    return characters


def _get_json(transport: GameToraTransport, url: str) -> Any:
    resp = transport.get(url)
    if not resp.ok:
        raise GameToraError(
            f"GET {url} failed: {resp.error or f'HTTP {resp.status_code}'}"
        )
    try:
        return json.loads(resp.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GameToraError(f"GET {url} returned non-JSON: {exc}") from exc


# ---------- Snapshot writer ----------


def write_characters_snapshot(
    characters: Sequence[FetchedCharacter],
    out_path: Path,
    *,
    source_label: str = "gametora_v1",
    region: str | None = None,
) -> None:
    """Write a JSON file in the shape consumed by services.seed_characters."""
    payload: dict[str, Any] = {
        "source": source_label,
        "attribution": DATA_ATTRIBUTION,
        "fetched_at": datetime.now(UTC).isoformat(),
        "characters": [c.to_seed_row() for c in characters],
    }
    if region:
        payload["region"] = region
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------- G1 race fetcher ----------

# Track id → venue. Mapping derived from cross-referencing GameTora's
# /data/umamusume/races.<v>.json against the i18n strings in chunk
# 2573 (which lists racetrack names in this order). Foreign venues
# (Longchamp, Santa Anita, Del Mar) are intentionally omitted: our
# `VENUES` enum only covers JRA + Oi, which matches the racetracks
# Uma Ladder's user community actually uses.
G1_TRACK_ID_TO_VENUE: dict[int, str] = {
    10001: "Sapporo",
    10002: "Hakodate",
    10003: "Niigata",
    10004: "Fukushima",
    10005: "Nakayama",
    10006: "Tokyo",
    10007: "Chukyo",
    10008: "Kyoto",
    10009: "Hanshin",
    10010: "Kokura",
    10101: "Oi",
}

# direction (1/2) → enum value. The values 3/4 ("Straight"/"Stretch") don't
# appear in upstream G1 data; they only exist on the custom-race appendix.
G1_DIRECTION_MAP: dict[int, str] = {1: "Right", 2: "Left"}

# terrain 1/2 → surface enum value.
G1_TERRAIN_MAP: dict[int, str] = {1: "Turf", 2: "Dirt"}


def _distance_category(meters: int) -> str:
    """Standard Uma category buckets: Sprint ≤1400, Mile ≤1800,
    Medium ≤2400, Long otherwise."""
    if meters <= 1400:
        return "Sprint"
    if meters <= 1800:
        return "Mile"
    if meters <= 2400:
        return "Medium"
    return "Long"


@dataclass(frozen=True)
class FetchedG1Race:
    name_en: str
    name_jp: str | None
    venue: str
    surface: str
    distance_meters: int
    distance_category: str
    direction: str
    max_runners: int
    profile_url: str | None

    def to_seed_row(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name_en,
            "grade": "G1",
            "venue": self.venue,
            "surface": self.surface,
            "distance_meters": self.distance_meters,
            "distance_category": self.distance_category,
            "direction": self.direction,
            "course_variant": None,
            "max_runners": self.max_runners,
        }
        if self.profile_url:
            out["external_source_url"] = self.profile_url
        return out


def _coerce_g1(raw: dict[str, Any]) -> FetchedG1Race | None:
    if raw.get("grade") != 100:
        return None
    track_id = raw.get("track")
    venue = G1_TRACK_ID_TO_VENUE.get(track_id) if isinstance(track_id, int) else None
    if venue is None:
        # Unknown / foreign venue — skip rather than guess.
        return None
    direction = G1_DIRECTION_MAP.get(raw.get("direction"))
    terrain = G1_TERRAIN_MAP.get(raw.get("terrain"))
    distance = raw.get("distance")
    if direction is None or terrain is None or not isinstance(distance, int):
        return None
    name_en = raw.get("name_en") or raw.get("name_jp")
    if not name_en:
        return None
    max_runners = raw.get("entries") or 18
    if not isinstance(max_runners, int) or max_runners <= 0:
        max_runners = 18
    url_name = raw.get("url_name")
    profile_url = (
        f"{GAMETORA_BASE}/umamusume/races/{url_name}" if url_name else None
    )
    return FetchedG1Race(
        name_en=name_en,
        name_jp=raw.get("name_jp"),
        venue=venue,
        surface=terrain,
        distance_meters=distance,
        distance_category=_distance_category(distance),
        direction=direction,
        max_runners=max_runners,
        profile_url=profile_url,
    )


def fetch_g1_races(
    transport: GameToraTransport | None = None,
    *,
    delay_seconds: float = DELAY_SECONDS,
) -> list[FetchedG1Race]:
    """Fetch the upstream race list and reduce it to G1s on supported
    venues. Foreign-venue G1s (Arc de Triomphe, etc.) are filtered out."""
    t = transport or UrllibGameToraTransport()
    manifest = _get_json(t, f"{GAMETORA_BASE}/data/manifests/umamusume.json")
    version = manifest.get("races")
    if not isinstance(version, str) or not version:
        raise GameToraError(
            "manifest missing 'races' key — upstream layout may have changed"
        )

    if delay_seconds:
        time.sleep(delay_seconds)

    raw = _get_json(t, f"{GAMETORA_BASE}/data/umamusume/races.{version}.json")
    if not isinstance(raw, list):
        raise GameToraError("races payload is not a list")

    out: list[FetchedG1Race] = []
    seen: set[tuple[str, str, int, str]] = set()  # natural-key dedupe
    for row in raw:
        if not isinstance(row, dict):
            continue
        coerced = _coerce_g1(row)
        if coerced is None:
            continue
        # Some races have multiple instances per year (Sprinters Stakes
        # at Nakayama + Niigata) — dedupe by natural key.
        key = (
            coerced.venue,
            coerced.surface,
            coerced.distance_meters,
            coerced.direction,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(coerced)

    out.sort(key=lambda g: (g.venue, g.distance_meters, g.name_en))
    return out


def write_g1_races_snapshot(
    races: Sequence[FetchedG1Race],
    out_path: Path,
    *,
    source_label: str = "gametora_v1",
) -> None:
    payload: dict[str, Any] = {
        "source": source_label,
        "attribution": (
            "Race data sourced from GameTora "
            "(https://gametora.com/umamusume/races); not affiliated with "
            "Cygames. Maintain attribution when redistributing."
        ),
        "fetched_at": datetime.now(UTC).isoformat(),
        "races": [g.to_seed_row() for g in races],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------- Outfit fetcher ----------


@dataclass(frozen=True)
class FetchedOutfit:
    char_id: int
    costume_id: int
    title_en: str | None
    title_jp: str | None
    image_url: str | None
    rarity: int | None
    released_globally: bool

    def to_seed_row(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "char_id": self.char_id,
            "costume_id": self.costume_id,
            "released_globally": self.released_globally,
        }
        if self.title_en:
            out["title_en"] = self.title_en
        if self.title_jp:
            out["title_jp"] = self.title_jp
        if self.image_url:
            out["image_url"] = self.image_url
        if self.rarity is not None:
            out["rarity"] = self.rarity
        return out


def _outfit_image_url(char_id: int, costume_id: int) -> str:
    return (
        f"{GAMETORA_BASE}/images/umamusume/characters/"
        f"chara_stand_{char_id}_{costume_id}.png"
    )


def _coerce_outfit(raw: dict[str, Any], region: str) -> FetchedOutfit | None:
    char_id = raw.get("char_id")
    # GameTora's URL slug + image filename use `card_id`. The `costume`
    # field is a different game-internal id that *usually* matches but
    # diverges for "alternate version" outfits (Cheerleader skins, RUN&WIN,
    # etc.) — using costume there 404s the image. Always prefer card_id.
    costume_id = raw.get("card_id") or raw.get("costume")
    if not isinstance(char_id, int) or not isinstance(costume_id, int):
        return None

    suffix = REGION_FIELD.get(region)
    if region == "all" or suffix is None or not suffix:
        released_globally = True
    else:
        released_globally = bool(raw.get(f"release_{suffix}"))

    title_en = raw.get("title_en_gl") or raw.get("title_en")
    title_jp = raw.get("title_jp") or raw.get("title")
    rarity = raw.get("rarity") if isinstance(raw.get("rarity"), int) else None
    return FetchedOutfit(
        char_id=char_id,
        costume_id=costume_id,
        title_en=title_en,
        title_jp=title_jp,
        image_url=_outfit_image_url(char_id, costume_id),
        rarity=rarity,
        released_globally=released_globally,
    )


def fetch_outfits(
    transport: GameToraTransport | None = None,
    *,
    region: str = "global",
    only_released: bool = True,
    delay_seconds: float = DELAY_SECONDS,
) -> list[FetchedOutfit]:
    """Fetch the upstream character-card list and reduce it to outfit metadata."""
    if region not in REGION_FIELD and region != "all":
        region = "global"

    t = transport or UrllibGameToraTransport()
    manifest = _get_json(t, f"{GAMETORA_BASE}/data/manifests/umamusume.json")
    version = manifest.get("character-cards")
    if not isinstance(version, str) or not version:
        raise GameToraError(
            "manifest missing 'character-cards' key — upstream layout may have changed"
        )

    if delay_seconds:
        time.sleep(delay_seconds)

    raw = _get_json(
        t, f"{GAMETORA_BASE}/data/umamusume/character-cards.{version}.json"
    )
    if not isinstance(raw, list):
        raise GameToraError("character-cards payload is not a list")

    outfits: list[FetchedOutfit] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        coerced = _coerce_outfit(row, region)
        if coerced is None:
            continue
        if only_released and not coerced.released_globally:
            continue
        outfits.append(coerced)

    outfits.sort(key=lambda o: (o.char_id, o.costume_id))
    return outfits


def write_outfits_snapshot(
    outfits: Sequence[FetchedOutfit],
    out_path: Path,
    *,
    source_label: str = "gametora_v1",
    region: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "source": source_label,
        "attribution": DATA_ATTRIBUTION,
        "fetched_at": datetime.now(UTC).isoformat(),
        "outfits": [o.to_seed_row() for o in outfits],
    }
    if region:
        payload["region"] = region
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
