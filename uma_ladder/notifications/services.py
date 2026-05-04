"""Event hooks: build payloads for each notification type and dispatch.

Each public function here is a one-shot call invoked from the relevant
domain service (services/official.py, services/draft.py). Payloads are
plain Discord embed JSON. Domain code never imports `discord` directly —
it goes through these functions so the payload shape stays in one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..models import (
    DiscordNotificationAttempt,
    DraftMatch,
    NotificationEvent,
    NotificationTarget,
    OfficialRace,
    OfficialRaceRegistration,
)
from ..services.official import LadderRow
from .discord import send_event


def _embed(title: str, fields: list[dict[str, Any]], *, color: int = 0x3B82F6) -> dict[str, Any]:
    return {
        "embeds": [
            {
                "title": title,
                "color": color,
                "fields": fields,
            }
        ]
    }


def _field(name: str, value: Any, *, inline: bool = True) -> dict[str, Any]:
    return {"name": name, "value": str(value), "inline": inline}


# ----- Official race events -----


def notify_official_race_published(race: OfficialRace) -> DiscordNotificationAttempt:
    fields = [
        _field("Season", race.season.name if race.season else "?"),
        _field("Status", race.status, inline=True),
    ]
    if race.scheduled_at is not None:
        fields.append(_field("Scheduled", race.scheduled_at.isoformat()))
    if race.preset is not None:
        fields.append(_field("Preset", race.preset.name, inline=False))
    return send_event(
        event_type=NotificationEvent.OFFICIAL_RACE_PUBLISHED,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(f"Race published: {race.name}", fields),
    )


def notify_official_room_code(
    race: OfficialRace, registrations: Sequence[OfficialRaceRegistration]
) -> DiscordNotificationAttempt:
    fields = [
        _field("Code", f"`{race.room_code}`"),
    ]
    if race.room_code_expires_at is not None:
        fields.append(_field("Expires at", race.room_code_expires_at.isoformat()))
    if registrations:
        names = ", ".join(r.user.username for r in registrations)
        fields.append(_field("Registered", names, inline=False))
    return send_event(
        event_type=NotificationEvent.OFFICIAL_ROOM_CODE,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(f"Room code for {race.name}", fields, color=0x10B981),
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
        payload=_embed(f"Results: {race.name}", fields, color=0xF59E0B),
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
        fields.append(_field("Expires at", match.room_code_expires_at.isoformat()))
    return send_event(
        event_type=NotificationEvent.DRAFT_ROOM_CODE,
        target=NotificationTarget.RACE_REGISTRATION,
        payload=_embed(f"Draft match #{match.id} — room code", fields, color=0x10B981),
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
    return send_event(
        event_type=NotificationEvent.DRAFT_RESULTS,
        target=NotificationTarget.DRAFT_RESULTS,
        payload=_embed(f"Draft match #{match.id} complete", fields, color=0xF59E0B),
    )
