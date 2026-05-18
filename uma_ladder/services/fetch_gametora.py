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
import re as _re
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
    # Keep the same race name from showing up twice (the upstream payload
    # occasionally lists a race once per server / season variant — those
    # *are* the same race and shouldn't double-seed). Distinct race names
    # are kept even when they share track conditions: Tokyo Yushun and
    # Japanese Oaks both run Tokyo 2400m turf left and both deserve a
    # row.
    seen_names: set[tuple[str, str]] = set()  # (name_en, venue)
    for row in raw:
        if not isinstance(row, dict):
            continue
        coerced = _coerce_g1(row)
        if coerced is None:
            continue
        key = (coerced.name_en, coerced.venue)
        if key in seen_names:
            continue
        seen_names.add(key)
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


# ---------- Skill fetcher ----------


@dataclass(frozen=True)
class FetchedSkill:
    gametora_id: int
    name_en: str
    name_jp: str | None
    description_en: str | None
    description_jp: str | None
    icon_id: int | None
    image_url: str | None
    rarity: int | None
    is_unique: bool
    is_inherited: bool
    parent_gametora_id: int | None

    def to_seed_row(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "gametora_id": self.gametora_id,
            "name_en": self.name_en,
            "is_unique": self.is_unique,
            "is_inherited": self.is_inherited,
        }
        if self.name_jp:
            out["name_jp"] = self.name_jp
        if self.description_en:
            out["description_en"] = self.description_en
        if self.description_jp:
            out["description_jp"] = self.description_jp
        if self.icon_id is not None:
            out["icon_id"] = self.icon_id
        if self.image_url:
            out["image_url"] = self.image_url
        if self.rarity is not None:
            out["rarity"] = self.rarity
        if self.parent_gametora_id is not None:
            out["parent_gametora_id"] = self.parent_gametora_id
        return out


def _skill_image_url(icon_id: int | None) -> str | None:
    """GameTora's CDN serves skill icons at a stable URL keyed by the
    ``iconid`` field. Confirmed working against multiple icon ids during
    fetcher development."""
    if icon_id is None:
        return None
    return f"{GAMETORA_BASE}/images/umamusume/skill_icons/utx_ico_skill_{icon_id}.png"


def _coerce_skill(
    raw: dict[str, Any], *, parent_id: int | None = None, is_inherited: bool = False
) -> FetchedSkill | None:
    """Map one GameTora skill row to our internal shape.

    Each upstream row describes the canonical skill plus an optional
    ``gene_version`` sub-row for the inherited variant. We call
    `_coerce_skill` once for the main row (parent_id=None,
    is_inherited=False), then once per gene_version (parent_id=main.id,
    is_inherited=True).

    The ``endesc`` field is GameTora's curated EN copy; ``desc_en`` is
    machine-translated and noisier, so prefer endesc for display.
    """
    skill_id = raw.get("id")
    # Prefer `name_en` (the polished Global EN localisation) over
    # `enname` (a JP-romanised label that diverges for many skills, e.g.
    # "Archline Professor" vs the Global "Professor of Curvature").
    # `enname` is the fallback only when name_en is missing.
    name_en = raw.get("name_en") or raw.get("enname")
    if not isinstance(skill_id, int) or not name_en:
        return None
    char = raw.get("char")
    is_unique = bool(isinstance(char, list) and char)
    icon_id = raw.get("iconid") if isinstance(raw.get("iconid"), int) else None
    return FetchedSkill(
        gametora_id=skill_id,
        name_en=name_en,
        name_jp=raw.get("jpname") or raw.get("name_jp"),
        # Same logic for descriptions: `desc_en` is the curated Global
        # copy, `endesc` is the older / less polished JP-EN version.
        description_en=raw.get("desc_en") or raw.get("endesc"),
        description_jp=raw.get("jpdesc") or raw.get("desc_jp"),
        icon_id=icon_id,
        image_url=_skill_image_url(icon_id),
        rarity=raw.get("rarity") if isinstance(raw.get("rarity"), int) else None,
        is_unique=is_unique and not is_inherited,
        is_inherited=is_inherited,
        parent_gametora_id=parent_id,
    )


def fetch_skills(
    transport: GameToraTransport | None = None,
    *,
    delay_seconds: float = DELAY_SECONDS,
) -> list[FetchedSkill]:
    """Fetch the upstream skills list, including inherited (gene_version)
    variants as separate rows.

    Returns rows sorted by gametora_id for deterministic output.
    """
    t = transport or UrllibGameToraTransport()
    manifest = _get_json(t, f"{GAMETORA_BASE}/data/manifests/umamusume.json")
    version = manifest.get("skills")
    if not isinstance(version, str) or not version:
        raise GameToraError(
            "manifest missing 'skills' key — upstream layout may have changed"
        )

    if delay_seconds:
        time.sleep(delay_seconds)

    raw = _get_json(t, f"{GAMETORA_BASE}/data/umamusume/skills.{version}.json")
    if not isinstance(raw, list):
        raise GameToraError("skills payload is not a list")

    skills: list[FetchedSkill] = []
    seen_ids: set[int] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        main = _coerce_skill(row)
        if main is None or main.gametora_id in seen_ids:
            continue
        skills.append(main)
        seen_ids.add(main.gametora_id)

        gene = row.get("gene_version")
        if isinstance(gene, dict):
            inherited = _coerce_skill(
                gene, parent_id=main.gametora_id, is_inherited=True
            )
            if inherited is not None and inherited.gametora_id not in seen_ids:
                skills.append(inherited)
                seen_ids.add(inherited.gametora_id)

    skills.sort(key=lambda s: s.gametora_id)
    return skills


# ---------- Skill condition fetcher (PR-SK2) -----------------------
#
# GameTora encodes each skill's activation rules in
# ``condition_groups[].condition``, a DSL with `&` (AND) and `@` (OR)
# operators over atomic predicates like ``rotation==1`` or
# ``ground_type==2``. Effects are listed as ``effects[]`` with a
# ``type`` (1=Speed, 2=Stamina, 3=Power, 4=Guts, 5=Wisdom; other
# types are race-state modifiers we don't track) and a ``value``
# in 10000ths (600000 → +60).
#
# Static-condition skills (predicates that depend only on race
# context — surface, weather, season, direction, distance band,
# strategy, venue, standard-distance-ness) map cleanly to our
# `SkillCondition` schema. Anything with race-state predicates
# (race phase, current placement, accumulated time, random
# triggers, etc.) is marked `is_dynamic=True` so items 5/6 know
# they can't predict whether the skill will fire.

_DIRECTION_FROM_CODE: dict[int, str] = {1: "Right", 2: "Left"}
_SURFACE_FROM_CODE: dict[int, str] = {1: "Turf", 2: "Dirt"}
_WEATHER_FROM_CODE: dict[int, str] = {
    1: "Sunny", 2: "Cloudy", 3: "Rainy", 4: "Snowy",
}
_SEASON_FROM_CODE: dict[int, str | None] = {
    1: "Spring", 2: "Summer", 3: "Autumn", 4: "Winter",
    # 5 = Sakura — game-internal sub-season, not in our RaceSeason enum.
    # Treated as "no season constraint" upstream when it appears alongside
    # season==1 in an OR (e.g. Spring Runner), so we map it to None here
    # and let the OR collapser handle the rest.
    5: None,
}
_DISTANCE_FROM_CODE: dict[int, str] = {
    1: "Sprint", 2: "Mile", 3: "Medium", 4: "Long",
}
_STRATEGY_FROM_CODE: dict[int, str] = {
    1: "Front", 2: "Pace", 3: "Late", 4: "End",
}
# PR-SK7 — ground_condition codes. Firm = dry; Good / Soft /
# Heavy = progressively wetter. Wet Conditions skills (which OR
# over {2, 3, 4}) still fall back to dynamic because the schema
# carries a single value per axis — the parser sees three
# distinct mapped values and gives up on the static slot.
_GROUND_CONDITION_FROM_CODE: dict[int, str] = {
    1: "Firm", 2: "Good", 3: "Soft", 4: "Heavy",
}

# `is_basis_distance` is the "standard distance" flag (1600 / 2000 /
# 2400 / 3200 are the Core/standard distances). The 0/1 here maps
# directly to our SkillCondition.is_standard_distance bool.
_BASIS_FROM_CODE: dict[int, bool] = {0: False, 1: True}

# Effect type code → SkillCondition column suffix.
_EFFECT_TYPE_TO_STAT: dict[int, str] = {
    1: "speed", 2: "stamina", 3: "power", 4: "guts", 5: "wisdom",
}

# Atomic predicates that map cleanly to our static schema. Anything
# else in the condition string forces is_dynamic=True.
_STATIC_PREDICATE_KEYS: frozenset[str] = frozenset({
    "rotation", "ground_type", "weather", "season", "distance_type",
    "running_style", "is_basis_distance", "track_id",
    "ground_condition",
    # PR-SK8 — Sympathy / Lone Wolf use `same_skill_horse_count`
    # which evaluates against a cross-result count we compute at
    # display time. Static in the sense that the count is knowable
    # without runtime race-state.
    "same_skill_horse_count",
    # PR-SK9 — Inner / Outer Post Proficiency + Lucky Seven use
    # `post_number` (gate bracket 1..8). Computed at display time
    # from result.gate + race.participant_count.
    "post_number",
    # `always` is technically just an unconditional truth value, but
    # we treat it as a no-op when parsing — appearing in a condition
    # alongside no other predicates means "applies in every race".
    "always",
})

# PR-SK8 — `ground_condition` supports the inverse pattern: a skill
# can "apply except on Firm tracks" either via `!=1` or via the
# OR-over-(n-1)-codes shape `==2@==3@==4`. Codes here let the
# parser detect either shape and project to `ground_condition_exclude`.
_GROUND_CONDITION_ALL_CODES: frozenset[int] = frozenset({1, 2, 3, 4})

_ATOMIC_RE = _re.compile(r"^([a-z_]+)(==|!=|>=|<=|>|<)(-?\d+)$")


@dataclass(frozen=True)
class FetchedSkillCondition:
    gametora_id: int
    # All predicate fields default to None = "skill doesn't care
    # about this axis". For the catalog row to apply at all, the
    # SkillCondition needs a matching UmaSkill row already in
    # `uma_skills` (seeder skips entries without one).
    direction: str | None = None
    surface: str | None = None
    weather: str | None = None
    season: str | None = None
    distance_category: str | None = None
    strategy: str | None = None
    venue: str | None = None
    is_standard_distance: bool | None = None
    ground_condition: str | None = None  # PR-SK7
    # PR-SK8 — Wet Conditions (`==2@==3@==4`) + `!=1` patterns.
    ground_condition_exclude: str | None = None
    # PR-SK8 — Sympathy (>=5) + Lone Wolf (==1). The catalog
    # carries the bounds; consumers count actual holders at
    # render time.
    min_holders: int | None = None
    max_holders: int | None = None
    # PR-SK9 — Inner Post Proficiency (`post_number<=3`) +
    # Outer Post (`>=6`) + Lucky Seven (`==7`). Bracket bounds;
    # bracket per result is computed at render time from gate +
    # race.participant_count.
    min_post_number: int | None = None
    max_post_number: int | None = None
    buff_speed: int = 0
    buff_stamina: int = 0
    buff_power: int = 0
    buff_guts: int = 0
    buff_wisdom: int = 0
    is_dynamic: bool = False
    notes: str | None = None

    def to_seed_row(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "gametora_id": self.gametora_id,
            "is_dynamic": self.is_dynamic,
        }
        for key in (
            "direction", "surface", "weather", "season",
            "distance_category", "strategy", "venue",
            "is_standard_distance", "ground_condition",
            "ground_condition_exclude",
            "min_holders", "max_holders",
            "min_post_number", "max_post_number",
        ):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        for stat in ("speed", "stamina", "power", "guts", "wisdom"):
            val = getattr(self, f"buff_{stat}")
            if val != 0:
                out[f"buff_{stat}"] = val
        if self.notes:
            out["notes"] = self.notes
        return out


def _parse_atomic(atom: str) -> tuple[str, str, int] | None:
    """Parse `lhs<op>rhs` into a 3-tuple. None if malformed.
    Only integer right-hand sides are supported — that's all
    the upstream DSL uses."""
    m = _ATOMIC_RE.match(atom)
    if m is None:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def _parse_condition_group(
    group: dict[str, Any]
) -> tuple[dict[str, list[tuple[str, int]]], bool]:
    """Extract per-key predicate atoms from one condition_group.
    Returns (predicates, is_dynamic). `predicates` maps
    static-predicate keys to the list of (op, rhs) tuples seen
    across atoms. `is_dynamic=True` means at least one atom
    references a race-state predicate we can't evaluate from
    static race context.

    PR-SK8 — operator-aware: we now keep the original op so
    downstream resolvers can distinguish `==` from `!=` /
    `>=` / `<=`. Each key supports a different operator set:

    - Enum keys (rotation, surface, weather, ...) accept `==`
      and (for ground_condition) `!=`.
    - `same_skill_horse_count` accepts `==`, `>=`, `<=`.
    - Anything else with a non-`==` op falls back to dynamic."""
    cond_str = group.get("condition") or ""
    if not cond_str:
        # Empty condition = "always" in upstream parlance.
        return {}, False

    predicates: dict[str, list[tuple[str, int]]] = {}
    is_dynamic = False

    for atom in _re.split(r"[@&]", cond_str):
        atom = atom.strip()
        if not atom:
            continue
        parsed = _parse_atomic(atom)
        if parsed is None:
            # Couldn't parse — treat conservatively as dynamic.
            is_dynamic = True
            continue
        lhs, op, rhs = parsed
        if lhs == "always":
            # No constraint; ignore.
            continue
        if lhs not in _STATIC_PREDICATE_KEYS:
            is_dynamic = True
            continue
        # Per-key operator support. Mixed-operator atoms on the
        # same key are conservative-dynamic (we can't combine
        # `==2` and `!=4` into a single column value safely).
        allowed_ops = _ALLOWED_OPS_BY_KEY.get(lhs, frozenset({"=="}))
        if op not in allowed_ops:
            is_dynamic = True
            continue
        predicates.setdefault(lhs, []).append((op, rhs))

    return predicates, is_dynamic


# PR-SK8 — operators each static key is willing to consume.
# Keys not listed default to {"=="}.
_ALLOWED_OPS_BY_KEY: dict[str, frozenset[str]] = {
    "ground_condition": frozenset({"==", "!="}),
    "same_skill_horse_count": frozenset({"==", ">=", "<="}),
    "post_number": frozenset({"==", ">=", "<="}),
}


def _resolve_predicate_value(
    key: str, values: list[int]
) -> tuple[Any, bool]:
    """Map a list of raw upstream codes (== op only) to a single
    schema value + a dynamic-fallback flag. Multiple values in an
    OR alternative collapse to one when our enum can drop a
    redundant code (e.g. season==1@season==5 → Spring, since
    season==5 is the game's Sakura that we don't model)."""
    if not values:
        return None, False

    if key == "rotation":
        mapped = {_DIRECTION_FROM_CODE.get(v) for v in values}
    elif key == "ground_type":
        mapped = {_SURFACE_FROM_CODE.get(v) for v in values}
    elif key == "weather":
        mapped = {_WEATHER_FROM_CODE.get(v) for v in values}
    elif key == "season":
        mapped = {_SEASON_FROM_CODE.get(v) for v in values}
    elif key == "distance_type":
        mapped = {_DISTANCE_FROM_CODE.get(v) for v in values}
    elif key == "running_style":
        mapped = {_STRATEGY_FROM_CODE.get(v) for v in values}
    elif key == "is_basis_distance":
        mapped = {_BASIS_FROM_CODE.get(v) for v in values}
    elif key == "track_id":
        mapped = {G1_TRACK_ID_TO_VENUE.get(v) for v in values}
    elif key == "ground_condition":
        mapped = {_GROUND_CONDITION_FROM_CODE.get(v) for v in values}
    else:
        return None, True

    # Drop unmapped codes (e.g. Sakura's season==5 collapses to None).
    mapped.discard(None)
    if not mapped:
        return None, True
    if len(mapped) == 1:
        return mapped.pop(), False
    # Genuinely OR'd over distinct meaningful values — schema can
    # only carry one. Fall back to dynamic.
    return None, True


def _resolve_ground_condition(
    atoms: list[tuple[str, int]],
) -> tuple[str | None, str | None, bool]:
    """PR-SK8 — special-case resolver for `ground_condition`,
    which uniquely supports both positive (`==N`) and inverse
    (`==X@==Y@==Z` n-1-OR, or explicit `!=N`) shapes.

    Returns (positive, excluded, is_dynamic).
    - positive: column `ground_condition` value, e.g. "Firm".
    - excluded: column `ground_condition_exclude` value, e.g. "Firm".
    - is_dynamic: True if we couldn't reduce to one or the other."""
    eq_codes = {rhs for op, rhs in atoms if op == "=="}
    ne_codes = {rhs for op, rhs in atoms if op == "!="}

    if eq_codes and ne_codes:
        # Mixed predicates on the same key — too complex to express.
        return None, None, True

    if ne_codes:
        # `!=X` form — single excluded value supported.
        if len(ne_codes) != 1:
            return None, None, True
        code = next(iter(ne_codes))
        excluded = _GROUND_CONDITION_FROM_CODE.get(code)
        return None, excluded, excluded is None

    # Only `==` atoms remain.
    if len(eq_codes) == 1:
        code = next(iter(eq_codes))
        positive = _GROUND_CONDITION_FROM_CODE.get(code)
        return positive, None, positive is None

    # Multi-value OR. If exactly one valid code is MISSING from
    # the full enum, treat as the inverse shape:
    # `==Good@==Soft@==Heavy` → exclude=Firm.
    missing = _GROUND_CONDITION_ALL_CODES - eq_codes
    if len(missing) == 1:
        code = next(iter(missing))
        excluded = _GROUND_CONDITION_FROM_CODE.get(code)
        return None, excluded, excluded is None

    # Anything else (2-of-4, 0-of-4) is too complex for one
    # column — fall back to dynamic.
    return None, None, True


def _resolve_holder_count(
    atoms: list[tuple[str, int]],
) -> tuple[int | None, int | None, bool]:
    """PR-SK8 — resolver for `same_skill_horse_count`. Returns
    (min_holders, max_holders, is_dynamic). Supports the two
    operator shapes upstream actually uses:

    - `>=N` → min_holders=N (Sympathy: at least 5 holders).
    - `==N` → min=max=N (Lone Wolf: exactly 1 holder = uma alone).
    - `<=N` → max_holders=N (defensive; not seen in current data).

    Multiple atoms on the same key are dynamic — we don't combine
    `>=3` AND `<=7` into a range here."""
    if len(atoms) != 1:
        return None, None, True
    op, n = atoms[0]
    if op == "==":
        return n, n, False
    if op == ">=":
        return n, None, False
    if op == "<=":
        return None, n, False
    return None, None, True


def _resolve_post_number(
    atoms: list[tuple[str, int]],
) -> tuple[int | None, int | None, bool]:
    """PR-SK9 — resolver for `post_number` (gate bracket). Same
    shape as `_resolve_holder_count` but distinct because the
    column names differ and the render-time computation differs
    (bracket is computed from gate + participants, not counted)."""
    if len(atoms) != 1:
        return None, None, True
    op, n = atoms[0]
    if op == "==":
        return n, n, False
    if op == ">=":
        return n, None, False
    if op == "<=":
        return None, n, False
    return None, None, True


def _coerce_skill_condition(
    skill_row: dict[str, Any],
) -> FetchedSkillCondition | None:
    """Map a single GameTora skill row to a `FetchedSkillCondition`
    if there's enough signal. Skills with no condition_groups
    return None (we don't track always-on no-effect entries)."""
    gid = skill_row.get("id")
    if not isinstance(gid, int):
        return None
    groups = skill_row.get("condition_groups") or []
    if not isinstance(groups, list) or not groups:
        return None

    if len(groups) > 1:
        # Multiple OR'd condition groups — schema can't express
        # alternative predicate sets, fall back to dynamic.
        is_dynamic = True
        primary_group = groups[0]
    else:
        is_dynamic = False
        primary_group = groups[0]

    predicates, group_dynamic = _parse_condition_group(primary_group)
    if group_dynamic:
        is_dynamic = True

    resolved: dict[str, Any] = {}

    # PR-SK8 — ground_condition handles its own resolver because
    # it's the only axis with both `==` and `!=` shapes plus the
    # n-1-OR-collapses-to-exclude pattern.
    if "ground_condition" in predicates:
        positive, excluded, force_dynamic = _resolve_ground_condition(
            predicates.pop("ground_condition")
        )
        if force_dynamic:
            is_dynamic = True
        else:
            if positive is not None:
                resolved["ground_condition"] = positive
            if excluded is not None:
                resolved["ground_condition_exclude"] = excluded

    # PR-SK8 — same_skill_horse_count (Sympathy / Lone Wolf)
    # projects to min/max holder bounds.
    if "same_skill_horse_count" in predicates:
        mn, mx, force_dynamic = _resolve_holder_count(
            predicates.pop("same_skill_horse_count")
        )
        if force_dynamic:
            is_dynamic = True
        else:
            if mn is not None:
                resolved["min_holders"] = mn
            if mx is not None:
                resolved["max_holders"] = mx

    # PR-SK9 — post_number (gate bracket) projects to min/max
    # post-number bounds. Inner Post Proficiency = <=3, Outer
    # = >=6, Lucky Seven = ==7. Bracket itself is computed at
    # render time from result.gate + race.participant_count.
    if "post_number" in predicates:
        mn, mx, force_dynamic = _resolve_post_number(
            predicates.pop("post_number")
        )
        if force_dynamic:
            is_dynamic = True
        else:
            if mn is not None:
                resolved["min_post_number"] = mn
            if mx is not None:
                resolved["max_post_number"] = mx

    # Remaining keys use the legacy `==`-only resolver.
    for key in (
        "rotation", "ground_type", "weather", "season",
        "distance_type", "running_style", "is_basis_distance",
        "track_id",
    ):
        if key not in predicates:
            continue
        # The legacy resolver expects a list of raw ints, not
        # (op, rhs) tuples — strip ops (all `==` here per the
        # ALLOWED_OPS_BY_KEY default).
        codes = [rhs for op, rhs in predicates[key] if op == "=="]
        value, force_dynamic = _resolve_predicate_value(key, codes)
        if force_dynamic:
            is_dynamic = True
            continue
        # Schema column name differs from upstream predicate key.
        col = {
            "rotation": "direction",
            "ground_type": "surface",
            "weather": "weather",
            "season": "season",
            "distance_type": "distance_category",
            "running_style": "strategy",
            "is_basis_distance": "is_standard_distance",
            "track_id": "venue",
        }[key]
        resolved[col] = value

    buffs = {f"buff_{s}": 0 for s in ("speed", "stamina", "power", "guts", "wisdom")}
    for eff in primary_group.get("effects") or []:
        if not isinstance(eff, dict):
            continue
        t = eff.get("type")
        v = eff.get("value")
        if not isinstance(t, int) or not isinstance(v, int):
            continue
        stat = _EFFECT_TYPE_TO_STAT.get(t)
        if stat is None:
            continue
        # value is in 10000ths of a stat point.
        buffs[f"buff_{stat}"] += v // 10000

    notes = skill_row.get("endesc") or skill_row.get("desc_en") or None
    if notes:
        notes = notes[:256]

    return FetchedSkillCondition(
        gametora_id=gid,
        is_dynamic=is_dynamic,
        notes=notes,
        **resolved,
        **buffs,
    )


def fetch_skill_conditions(
    transport: GameToraTransport | None = None,
    *,
    delay_seconds: float = DELAY_SECONDS,
) -> list[FetchedSkillCondition]:
    """Fetch the upstream skills payload and project each row to
    a `FetchedSkillCondition`. Skipped rows (no condition_groups,
    no id) are silently dropped — `seed-skill-conditions` only
    needs the rows that have something to say.

    Reuses the same single-skills-request the existing
    `fetch_skills` does, so this adds one network round-trip
    (manifest+skills) regardless of catalog size."""
    t = transport or UrllibGameToraTransport()
    manifest = _get_json(t, f"{GAMETORA_BASE}/data/manifests/umamusume.json")
    version = manifest.get("skills")
    if not isinstance(version, str) or not version:
        raise GameToraError(
            "manifest missing 'skills' key — upstream layout may have changed"
        )
    if delay_seconds:
        time.sleep(delay_seconds)
    raw = _get_json(t, f"{GAMETORA_BASE}/data/umamusume/skills.{version}.json")
    if not isinstance(raw, list):
        raise GameToraError("skills payload is not a list")

    conditions: list[FetchedSkillCondition] = []
    seen: set[int] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        coerced = _coerce_skill_condition(row)
        if coerced is None or coerced.gametora_id in seen:
            continue
        conditions.append(coerced)
        seen.add(coerced.gametora_id)
        # The gene_version (inherited) variant of each skill carries
        # the same condition_groups as the main row, so we re-coerce
        # under the inherited id too.
        gene = row.get("gene_version")
        if isinstance(gene, dict):
            inherited = _coerce_skill_condition(gene)
            if inherited and inherited.gametora_id not in seen:
                conditions.append(inherited)
                seen.add(inherited.gametora_id)

    conditions.sort(key=lambda c: c.gametora_id)
    return conditions


def write_skill_conditions_snapshot(
    conditions: Sequence[FetchedSkillCondition],
    out_path: Path,
    *,
    source_label: str = "gametora_v1",
) -> None:
    payload: dict[str, Any] = {
        "source": source_label,
        "attribution": (
            "Skill condition data sourced from GameTora "
            "(https://gametora.com/umamusume/skills); not affiliated "
            "with Cygames. Maintain attribution when redistributing."
        ),
        "fetched_at": datetime.now(UTC).isoformat(),
        "conditions": [c.to_seed_row() for c in conditions],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_skills_snapshot(
    skills: Sequence[FetchedSkill],
    out_path: Path,
    *,
    source_label: str = "gametora_v1",
) -> None:
    payload: dict[str, Any] = {
        "source": source_label,
        "attribution": (
            "Skill data sourced from GameTora "
            "(https://gametora.com/umamusume/skills); not affiliated with "
            "Cygames. Maintain attribution when redistributing."
        ),
        "fetched_at": datetime.now(UTC).isoformat(),
        "skills": [s.to_seed_row() for s in skills],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
