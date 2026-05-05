"""Resolve Discord `<@USERID>` mention strings for participating users.

Single source of truth for mention rendering — every per-player
notification (room code, results, cancellation, registration removed)
prefixes its embed with the output of `mention_prefix(...)`. Centralised
so a future bot-token + OAuth flow can replace the lookup without
touching every notify_* call site.

Currently reads ``UserProfile.discord_user_id`` (a numeric snowflake the
user pastes in themselves). When that's missing the helpers degrade to
empty strings — the embed still posts, just without the @-ping.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select

from ..extensions import db
from ..models import UserProfile


def _snowflakes_for(user_ids: Iterable[int]) -> list[str]:
    """Look up `discord_user_id` for the given users in one query.
    Preserves no particular order — duplicates are dropped so a user
    registered twice (unlikely but harmless) only gets one ping.
    """
    ids = [uid for uid in user_ids if uid is not None]
    if not ids:
        return []
    rows = db.session.scalars(
        select(UserProfile.discord_user_id).where(
            UserProfile.user_id.in_(ids),
            UserProfile.discord_user_id.is_not(None),
        )
    ).all()
    seen: set[str] = set()
    out: list[str] = []
    for sf in rows:
        if sf and sf not in seen:
            seen.add(sf)
            out.append(sf)
    return out


def mention_for_user(user_id: int | None) -> str:
    """Single-user shorthand. Empty string when no snowflake on file."""
    if user_id is None:
        return ""
    snowflakes = _snowflakes_for([user_id])
    if not snowflakes:
        return ""
    return f"<@{snowflakes[0]}>"


def mention_prefix(user_ids: Iterable[int | None]) -> str:
    """Space-separated `<@id> <@id>` block for a payload's `content`
    field. Empty string when none of the users have a snowflake on
    file — Discord ignores empty content so this is safe to always
    inject into the payload.
    """
    snowflakes = _snowflakes_for([uid for uid in user_ids if uid is not None])
    if not snowflakes:
        return ""
    return " ".join(f"<@{sf}>" for sf in snowflakes)
