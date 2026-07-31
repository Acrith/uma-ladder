"""Site-wide aggregates for the public landing page.

These are deliberately club-agnostic and viewer-agnostic: they answer
"is anything happening here" for a stranger who has no account, no club
and no races of their own. Per-club or per-viewer numbers belong on the
club page and the member dashboard respectively — a global "latest
race" spotlight stops having an unambiguous subject the moment a second
club runs a league here, but counts stay correct at any number.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    DraftEloChange,
    DraftMatch,
    DraftMatchStatus,
    OfficialRace,
    OfficialRaceResult,
    OfficialRaceStatus,
    User,
)


@dataclass(frozen=True)
class SiteSummary:
    trainers: int
    races_run: int
    drafts_played: int


def public_summary() -> SiteSummary:
    trainers = db.session.scalar(
        select(func.count(User.id)).where(User.disabled_at.is_(None))
    )
    races_run = db.session.scalar(
        select(func.count(OfficialRace.id)).where(
            OfficialRace.status == OfficialRaceStatus.COMPLETED
        )
    )
    drafts_played = db.session.scalar(
        select(func.count(DraftMatch.id)).where(
            DraftMatch.status == DraftMatchStatus.COMPLETED
        )
    )
    return SiteSummary(
        trainers=trainers or 0,
        races_run=races_run or 0,
        drafts_played=drafts_played or 0,
    )


def latest_completed_race() -> tuple[OfficialRace, list[OfficialRaceResult]] | None:
    """The most recent completed race plus its podium (placements 1-3),
    for the dashboard activity feed. "Most recent" means scheduled_at
    when set, falling back to created_at for unscheduled races — same
    coalesce trick as list_upcoming_races, in reverse."""
    race = db.session.scalars(
        select(OfficialRace)
        .where(OfficialRace.status == OfficialRaceStatus.COMPLETED)
        .order_by(
            func.coalesce(OfficialRace.scheduled_at, OfficialRace.created_at).desc()
        )
        .limit(1)
    ).first()
    if race is None:
        return None
    podium = list(
        db.session.scalars(
            select(OfficialRaceResult)
            .where(OfficialRaceResult.official_race_id == race.id)
            .where(OfficialRaceResult.placement.in_((1, 2, 3)))
            .order_by(OfficialRaceResult.placement)
        ).unique()
    )
    return race, podium


@dataclass(frozen=True)
class RecentDraftRow:
    match_id: int
    winner_username: str
    loser_username: str
    umas_per_player: int
    winner_delta: int | None


def recent_draft_results(*, limit: int = 4) -> list[RecentDraftRow]:
    """Latest completed draft matches as "winner def. loser · +delta"
    rows for the dashboard activity feed."""
    matches = db.session.scalars(
        select(DraftMatch)
        .where(DraftMatch.status == DraftMatchStatus.COMPLETED)
        .where(DraftMatch.winner_user_id.is_not(None))
        .order_by(func.coalesce(DraftMatch.completed_at, DraftMatch.created_at).desc())
        .limit(limit)
    ).all()
    if not matches:
        return []
    deltas: dict[tuple[int, int], int] = {
        (c.draft_match_id, c.user_id): c.delta
        for c in db.session.scalars(
            select(DraftEloChange).where(
                DraftEloChange.draft_match_id.in_([m.id for m in matches])
            )
        )
    }
    rows: list[RecentDraftRow] = []
    for m in matches:
        winner = m.host if m.winner_user_id == m.host_user_id else m.opponent
        loser = m.opponent if m.winner_user_id == m.host_user_id else m.host
        if winner is None or loser is None:
            continue
        rows.append(
            RecentDraftRow(
                match_id=m.id,
                winner_username=winner.username,
                loser_username=loser.username,
                umas_per_player=m.umas_per_player,
                winner_delta=deltas.get((m.id, winner.id)),
            )
        )
    return rows
