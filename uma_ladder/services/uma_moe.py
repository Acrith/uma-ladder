"""uma.moe trainer-summary integration.

Pulls a *small, cosmetic* slice of the uma.moe v4 profile JSON onto
Uma Ladder profiles. We never reverse-engineer Cygames directly; we
read uma.moe's response. See ``docs/uma_moe_integration.md`` for the
full API map and the rationale behind this scope.

Design intent
-------------
- Best-effort enrichment, never blocking. Any failure (404, network
  error, malformed JSON, transport exception) returns ``None`` and
  the page renders without the card.
- Friend code is the lookup key (12-digit ``viewer_id`` upstream),
  reusing the existing ``UserProfile.friend_code`` column.
- Cache table absorbs repeated reads + caches negatives so a typo'd
  friend code doesn't keep hammering uma.moe.
- Transport is injectable (``FakeUmaMoeTransport`` for tests) so the
  test suite never hits the real service.
- Polite citizenship: identifying User-Agent, single-flight per cache
  miss, no background polling.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import UmaMoeCache

DEFAULT_BASE_URL = "https://uma.moe"
DEFAULT_TIMEOUT = 10.0
DEFAULT_TTL_HOURS = 12
USER_AGENT = (
    "uma-ladder/0.1 (+https://github.com/Acrith/uma-ladder; "
    "polite single-flight enrichment of public profile data)"
)


# ---------- Transport ----------


@dataclass(frozen=True)
class HttpResponse:
    ok: bool
    status_code: int  # 0 means transport-level failure (DNS, timeout, ...)
    body: bytes
    error: str | None


class UmaMoeTransport(ABC):
    @abstractmethod
    def get(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> HttpResponse:
        ...


class UrllibUmaMoeTransport(UmaMoeTransport):
    def __init__(self, *, user_agent: str = USER_AGENT) -> None:
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
                    status_code=resp.status,
                    body=body,
                    error=None,
                )
        except urllib.error.HTTPError as exc:
            # 4xx / 5xx — read the body so we can cache the negative.
            try:
                body = exc.read() or b""
            except Exception:  # noqa: BLE001
                body = b""
            return HttpResponse(
                ok=False, status_code=exc.code, body=body, error=str(exc)
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return HttpResponse(
                ok=False, status_code=0, body=b"", error=str(exc)
            )


@dataclass
class FakeUmaMoeTransport(UmaMoeTransport):
    """Test transport. ``responses`` maps URL → HttpResponse."""

    responses: dict[str, HttpResponse]

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> HttpResponse:  # noqa: ARG002
        self.calls.append(url)
        if url in self.responses:
            return self.responses[url]
        return HttpResponse(
            ok=False, status_code=404, body=b"", error="no fake response"
        )


def get_transport() -> UmaMoeTransport:
    """Resolve transport from the Flask app extensions, defaulting to
    UrllibUmaMoeTransport. Tests inject via ``set_transport``."""
    transport = current_app.extensions.get("uma_ladder.uma_moe_transport")
    if transport is None:
        transport = UrllibUmaMoeTransport()
        current_app.extensions["uma_ladder.uma_moe_transport"] = transport
    return transport


def set_transport(transport: UmaMoeTransport | None) -> None:
    if transport is None:
        current_app.extensions.pop("uma_ladder.uma_moe_transport", None)
    else:
        current_app.extensions["uma_ladder.uma_moe_transport"] = transport


# ---------- Public surface ----------


@dataclass(frozen=True)
class TrainerSummary:
    """The five fields we display on the public profile card. All
    optional individually — the upstream may omit any field, in which
    case we render '—'."""

    friend_code: str
    trainer_name: str | None
    circle_name: str | None
    total_fans: int | None
    gain_7d: int | None
    gain_30d: int | None
    alltime_rank: int | None
    fetched_at: datetime


def _ttl() -> timedelta:
    hours = current_app.config.get("UMA_MOE_CACHE_TTL_HOURS", DEFAULT_TTL_HOURS)
    return timedelta(hours=int(hours))


def _base_url() -> str:
    return str(current_app.config.get("UMA_MOE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")


def _normalize_friend_code(raw: str | None) -> str | None:
    """Strip whitespace + non-digit characters. Friend codes upstream
    are 12-digit numeric viewer_ids; users sometimes paste with
    separators."""
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return digits


def fetch_trainer_summary(
    friend_code: str | None,
    *,
    max_age: timedelta | None = None,
) -> TrainerSummary | None:
    """Resolve a TrainerSummary for the given friend code, using cache
    when fresh and never raising into the caller.

    Returns ``None`` for: missing/invalid friend code, 404 trainer,
    network failure, malformed JSON. The cache row is written on every
    transport call (positive or negative) so we don't hammer uma.moe
    on repeat misses.
    """
    fc = _normalize_friend_code(friend_code)
    if fc is None:
        return None

    ttl = max_age if max_age is not None else _ttl()
    now = datetime.now(UTC)

    cached = db.session.scalar(
        select(UmaMoeCache).where(UmaMoeCache.friend_code == fc)
    )
    if cached is not None:
        fetched_at = cached.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        if (now - fetched_at) < ttl:
            return _summary_from_cache(cached)

    # Cache miss or expired — go to the transport.
    url = f"{_base_url()}/api/v4/user/profile/{fc}"
    try:
        resp = get_transport().get(url)
    except Exception:  # noqa: BLE001
        # Transport itself shouldn't raise (UrllibUmaMoeTransport catches
        # everything), but defend in depth.
        return None

    payload_json: str | None = None
    if resp.ok:
        try:
            json.loads(resp.body.decode("utf-8"))
            payload_json = resp.body.decode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload_json = None

    # Upsert the cache row regardless of outcome — caching negatives is
    # what stops typos from re-hitting uma.moe on every page view.
    if cached is None:
        cached = UmaMoeCache(friend_code=fc)
        db.session.add(cached)
    cached.fetched_at = now
    cached.status = resp.status_code
    cached.payload_json = payload_json
    db.session.commit()

    return _summary_from_cache(cached)


def _summary_from_cache(row: UmaMoeCache) -> TrainerSummary | None:
    if row.status != 200 or not row.payload_json:
        return None
    try:
        data = json.loads(row.payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    trainer = data.get("trainer") or {}
    circle = data.get("circle") or {}
    fan_history = data.get("fan_history") or {}
    alltime = fan_history.get("alltime") or {}
    rolling = fan_history.get("rolling") or {}

    fetched_at = row.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)

    return TrainerSummary(
        friend_code=row.friend_code,
        trainer_name=_str_or_none(trainer.get("name")),
        circle_name=_str_or_none(circle.get("name")),
        total_fans=_int_or_none(alltime.get("total_fans")),
        gain_7d=_int_or_none(rolling.get("gain_7d")),
        gain_30d=_int_or_none(rolling.get("gain_30d")),
        alltime_rank=_int_or_none(alltime.get("rank")),
        fetched_at=fetched_at,
    )


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int_or_none(v: Any) -> int | None:
    if isinstance(v, bool):  # bool is subclass of int — exclude
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None
