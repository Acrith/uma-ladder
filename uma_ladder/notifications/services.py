"""Event hooks: build payloads for each notification type and dispatch.

Each public function here is a one-shot call invoked from the relevant
domain service (services/official.py, services/draft.py). Payloads are
plain Discord embed JSON. Domain code never imports `discord` directly —
it goes through these functions so the payload shape stays in one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from flask import current_app

from ..models import (
    AdminAuditLog,
    DiscordNotificationAttempt,
    DraftMatch,
    NotificationEvent,
    NotificationTarget,
    OfficialRace,
    OfficialRaceRegistration,
    RacePreset,
)
from ..services.official import LadderRow
from .discord import send_event
from .mentions import mention_prefix


def _public_url(path: str) -> str | None:
    """Build an absolute URL by stitching `path` onto APP_BASE_URL.
    Returns None when no base URL is configured — callers should
    skip the link rather than emit a relative href into a Discord
    embed (which Discord rejects)."""
    base = (current_app.config.get("APP_BASE_URL") or "").rstrip("/")
    if not base:
        return None
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _ts(dt: datetime | None, *, style: str = "F", relative: bool = False) -> str:
    """Discord dynamic-timestamp tag — `<t:unix:style>`.

    Discord renders these in each viewer's local timezone + locale.
    Style codes:
      F  — full date+time, e.g. "Sunday, 10 May 2026 17:44"
      f  — short date+time, "10 May 2026 17:44"
      D  — long date, "10 May 2026"
      d  — short date, "05/10/2026"
      T  — long time, "17:44:30"
      t  — short time, "17:44"
      R  — relative, "in 4 days" / "3 hours ago"

    Pass `relative=True` to append a `· <t:…:R>` block, useful for
    "Scheduled" fields where both the absolute and relative readings
    add value. Returns a literal "—" for None inputs so callers can
    drop the field guard.
    """
    if dt is None:
        return "—"
    # SQLite strips tzinfo on read; rest of the codebase treats naive
    # datetimes as UTC (cf. services/official._as_utc). Mirror that
    # so the epoch we hand Discord matches the wall-clock the user
    # entered.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    epoch = int(dt.timestamp())
    out = f"<t:{epoch}:{style}>"
    if relative:
        out += f" · <t:{epoch}:R>"
    return out


def _format_track(preset: RacePreset | None) -> str | None:
    """Compact one-block track description for embed fields.

    Returns None when preset is missing — the caller should skip the
    field entirely rather than render an empty box.

    G1 imports name a *real-world race* (e.g. "Tokyo Yushun") so we
    add the structured venue/distance line as complementary detail.
    Custom / manual presets typically have descriptive names that
    already encode the conditions ("Hanshin Dirt 2000m Right"); the
    structured line would be redundant there, so we drop it.
    """
    if preset is None:
        return None
    lines = [f"**{preset.name}**"]
    if preset.source == "g1_import":
        lines.append(
            f"{preset.venue} · {preset.distance_meters}m {preset.surface} ({preset.direction})"
        )
    if preset.max_runners:
        lines.append(f"Max runners: {preset.max_runners}")
    return "\n".join(lines)


def _format_conditions(
    *,
    race_season: str | None,
    weather: str | None,
    ground_condition: str | None,
) -> str | None:
    """`Spring · Cloudy · Good` style summary, or None when nothing
    has been declared so the caller can skip the field."""
    parts = [v for v in (race_season, weather, ground_condition) if v]
    return " · ".join(parts) if parts else None


def _embed(
    title: str,
    fields: list[dict[str, Any]],
    *,
    color: int = 0x3B82F6,
    mention: str = "",
    url: str | None = None,
) -> dict[str, Any]:
    embed: dict[str, Any] = {
        "title": title,
        "color": color,
        "fields": fields,
    }
    if url:
        # Discord renders the embed title as a link when `url` is set.
        # Skip when None / empty so the rendering stays clean.
        embed["url"] = url
    payload: dict[str, Any] = {"embeds": [embed]}
    if mention:
        # Discord renders `content` above the embed and triggers a push
        # for each mentioned user. `allowed_mentions.parse=["users"]`
        # restricts pinging to the explicit user IDs (can't @here from
        # a stray string in any future title text).
        payload["content"] = mention
        payload["allowed_mentions"] = {"parse": ["users"]}
    return payload


def _field(name: str, value: Any, *, inline: bool = True) -> dict[str, Any]:
    return {"name": name, "value": str(value), "inline": inline}


# ----- Official race events -----


def notify_official_race_published(race: OfficialRace) -> DiscordNotificationAttempt:
    fields = [
        _field("Season", race.season.name if race.season else "?"),
    ]
    if race.scheduled_at is not None:
        fields.append(
            _field("Scheduled", _ts(race.scheduled_at, relative=True), inline=False)
        )
    track = _format_track(race.preset)
    if track is not None:
        fields.append(_field("Track", track, inline=False))
    conditions = _format_conditions(
        race_season=race.race_season,
        weather=race.weather,
        ground_condition=race.ground_condition,
    )
    if conditions is not None:
        fields.append(_field("Conditions", conditions, inline=False))
    return send_event(
        event_type=NotificationEvent.OFFICIAL_RACE_PUBLISHED,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(
            f"Race published: {race.name}",
            fields,
            url=_public_url(f"/official/{race.id}"),
        ),
    )


def notify_official_room_code(
    race: OfficialRace, registrations: Sequence[OfficialRaceRegistration]
) -> DiscordNotificationAttempt:
    fields = [
        _field("Code", f"`{race.room_code}`"),
    ]
    if race.room_code_expires_at is not None:
        fields.append(
            _field("Expires", _ts(race.room_code_expires_at, relative=True))
        )
    if registrations:
        names = ", ".join(r.user.username for r in registrations)
        fields.append(_field("Registered", names, inline=False))
    mention = mention_prefix(r.user_id for r in registrations)
    return send_event(
        event_type=NotificationEvent.OFFICIAL_ROOM_CODE,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(
            f"Room code for {race.name}",
            fields,
            color=0x10B981,
            mention=mention,
            url=_public_url(f"/official/{race.id}"),
        ),
    )


def notify_official_race_cancelled(
    race: OfficialRace,
    *,
    cancelled_by_username: str | None = None,
    registered_usernames: Sequence[str] = (),
    registered_user_ids: Sequence[int] = (),
) -> DiscordNotificationAttempt:
    """Tell the registration channel a race they were registered for is
    gone. Mentions the registered users so they get pinged via Discord
    when their handles match."""
    fields = [
        _field("Race", race.name),
        _field("Status", race.status, inline=True),
    ]
    if cancelled_by_username:
        fields.append(_field("Cancelled by", cancelled_by_username, inline=True))
    if registered_usernames:
        names = ", ".join(registered_usernames)
        fields.append(_field("Affected registrations", names, inline=False))
    mention = mention_prefix(registered_user_ids)
    return send_event(
        event_type=NotificationEvent.OFFICIAL_RACE_CANCELLED,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(
            f"Race cancelled: {race.name}",
            fields,
            color=0xEF4444,
            mention=mention,
            url=_public_url(f"/official/{race.id}"),
        ),
    )


def notify_official_registration_removed(
    race: OfficialRace,
    registration: OfficialRaceRegistration,
    *,
    removed_by_username: str | None = None,
) -> DiscordNotificationAttempt:
    """Tell the channel that one player's registration was removed.
    Useful so the user can re-register or DM the organiser."""
    fields = [
        _field("Race", race.name),
        _field(
            "Player",
            registration.user.username if registration.user else "?",
        ),
    ]
    if removed_by_username:
        fields.append(_field("Removed by", removed_by_username, inline=True))
    mention = mention_prefix([registration.user_id])
    return send_event(
        event_type=NotificationEvent.OFFICIAL_REGISTRATION_REMOVED,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(
            f"Registration removed: {race.name}",
            fields,
            color=0xF59E0B,
            mention=mention,
            url=_public_url(f"/official/{race.id}"),
        ),
    )


def notify_official_results(
    race: OfficialRace, top: Sequence[LadderRow]
) -> DiscordNotificationAttempt:
    fields = [
        _field("Season", race.season.name if race.season else "?"),
    ]
    if top:
        leaderboard = "\n".join(
            f"{i + 1}. **{row.username}** — {row.total_points} pts"
            for i, row in enumerate(top[:5])
        )
        fields.append(_field("Top 5 (season)", leaderboard, inline=False))
    return send_event(
        event_type=NotificationEvent.OFFICIAL_RESULTS,
        target=NotificationTarget.OFFICIAL_RESULTS,
        payload=_embed(
            f"Results: {race.name}",
            fields,
            color=0xF59E0B,
            url=_public_url(f"/official/{race.id}"),
        ),
    )


# ----- Draft match events -----


def notify_draft_room_code(match: DraftMatch) -> DiscordNotificationAttempt:
    fields = [
        _field("Host", match.host.username if match.host else "?"),
        _field("Opponent", match.opponent.username if match.opponent else "?"),
        _field("Code", f"`{match.room_code}`", inline=False),
    ]
    if match.selected_preset is not None:
        fields.append(_field("Preset", match.selected_preset.name, inline=False))
    if match.room_code_expires_at is not None:
        fields.append(
            _field("Expires", _ts(match.room_code_expires_at, relative=True))
        )
    mention = mention_prefix([match.host_user_id, match.opponent_user_id])
    return send_event(
        event_type=NotificationEvent.DRAFT_ROOM_CODE,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(
            f"Draft match #{match.id} — room code",
            fields,
            color=0x10B981,
            mention=mention,
            url=_public_url(f"/draft/{match.id}"),
        ),
    )


def notify_draft_results(
    match: DraftMatch,
    *,
    winner_username: str,
    loser_username: str,
    winner_delta: int,
    loser_delta: int,
) -> DiscordNotificationAttempt:
    fields = [
        _field("Winner", f"{winner_username} ({winner_delta:+d})"),
        _field("Loser", f"{loser_username} ({loser_delta:+d})"),
    ]
    if match.selected_preset is not None:
        fields.append(_field("Preset", match.selected_preset.name, inline=False))
    mention = mention_prefix([match.host_user_id, match.opponent_user_id])
    return send_event(
        event_type=NotificationEvent.DRAFT_RESULTS,
        target=NotificationTarget.DRAFT_RESULTS,
        payload=_embed(
            f"Draft match #{match.id} complete",
            fields,
            color=0xF59E0B,
            mention=mention,
            url=_public_url(f"/draft/{match.id}"),
        ),
    )


def notify_draft_match_cancelled(
    match: DraftMatch, *, cancelled_by_username: str | None = None
) -> DiscordNotificationAttempt:
    """Admin cancelled a draft match — tell both participants via the
    DRAFT_RESULTS channel (same audience that already follows the
    match's outcome)."""
    fields = [
        _field("Host", match.host.username if match.host else "?"),
        _field(
            "Opponent",
            match.opponent.username if match.opponent else "—",
        ),
    ]
    if cancelled_by_username:
        fields.append(_field("Cancelled by", cancelled_by_username, inline=True))
    mention = mention_prefix([match.host_user_id, match.opponent_user_id])
    return send_event(
        event_type=NotificationEvent.DRAFT_MATCH_CANCELLED,
        target=NotificationTarget.DRAFT_RESULTS,
        payload=_embed(
            f"Draft match #{match.id} cancelled",
            fields,
            color=0xEF4444,
            mention=mention,
            url=_public_url(f"/draft/{match.id}"),
        ),
    )


# ----- Admin audit feed (PR-E1) -----


# Color-code admin actions so the audit channel is scannable at a glance.
_ADMIN_ACTION_COLORS: dict[str, int] = {
    "role_change": 0x06B6D4,             # cyan
    "official_race_cancel": 0xEF4444,    # rose
    "draft_match_cancel": 0xEF4444,      # rose
    "draft_match_forfeit": 0xF59E0B,     # amber
}


def notify_admin_action(row: AdminAuditLog) -> DiscordNotificationAttempt:
    """Post one Discord embed per admin/audit-log action to the
    ADMIN_AUDIT channel.

    The audit table is the source of truth; this is a real-time mirror
    so a moderation team watching Discord sees actions as they happen
    (without having to refresh /admin/audit). Coexists with the per-
    action notifications (e.g. race cancel also fires the player-facing
    OFFICIAL_RACE_CANCELLED) — those go to player channels; this one
    goes to the admin channel."""
    actor = row.actor.username if row.actor else "?"
    target = row.target_user.username if row.target_user else None

    fields = [_field("Actor", actor)]
    if target:
        fields.append(_field("Target", target))
    elif row.target_kind:
        target_str = row.target_kind
        if row.target_id is not None:
            target_str += f" #{row.target_id}"
        fields.append(_field("Target", target_str))

    if row.before_json:
        fields.append(_field("Before", str(row.before_json), inline=True))
    if row.after_json:
        fields.append(_field("After", str(row.after_json), inline=True))
    if row.details:
        fields.append(_field("Details", row.details, inline=False))

    color = _ADMIN_ACTION_COLORS.get(row.action, 0x64748B)  # slate fallback
    return send_event(
        event_type=NotificationEvent.ADMIN_ACTION,
        target=NotificationTarget.ADMIN_AUDIT,
        payload=_embed(f"Admin · {row.action}", fields, color=color),
    )
