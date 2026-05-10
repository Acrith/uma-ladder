"""Unified Rankings page (PR-N1).

Aggregates the Official + Draft season ladders behind a single
``/rankings`` URL with mode tabs + a season picker. Replaces the
two scattered surfaces at ``/official/ladder/<season_id>`` and
``/draft/ladder/<season_id>`` (those routes redirect here).

Per-club ladder is queued as a follow-up — it'll arrive as a
third tab driven by ``?club_id=<int>`` filter on the existing
ladder service queries.
"""

from __future__ import annotations

from collections.abc import Sequence

from flask import Blueprint, render_template, request
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..models import Season, UserProfile
from ..services import clubs as clubs_service
from ..services import draft as draft_service
from ..services import official as official_service
from ..services import seasons as seasons_service

bp = Blueprint("rankings", __name__, template_folder="templates")


_VALID_MODES = ("official", "draft")
_VALID_SCOPES = ("all", "club")
_PAGE_SIZE = 25


def _resolve_season(season_arg: str | None) -> Season | None:
    """Resolve the ``?season=`` query param to a Season row.

    Falls back to the active season when nothing is given. Returns
    ``None`` when the requested season doesn't exist OR there's no
    active season — the template renders an empty state in either
    case so the user can pick from the dropdown.
    """
    if season_arg:
        try:
            sid = int(season_arg)
        except ValueError:
            return None
        return db.session.get(Season, sid)
    return seasons_service.get_active_season()


def _profiles_by_user_id(user_ids: Sequence[int]) -> dict[int, UserProfile]:
    """Batch-fetch UserProfile rows so the table can render avatars
    + display names + oshi without N+1ing across the whole page."""
    if not user_ids:
        return {}
    rows = db.session.scalars(
        select(UserProfile).where(UserProfile.user_id.in_(user_ids))
    ).all()
    return {p.user_id: p for p in rows}


def _viewer_club_id() -> int | None:
    """The signed-in viewer's own club_id, or None when anonymous /
    no club / no profile yet. Drives whether the 'My club' scope
    toggle is offered + which club_id we filter by."""
    if not current_user.is_authenticated:
        return None
    profile = db.session.scalars(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    ).first()
    return profile.club_id if profile else None


@bp.get("/")
def index() -> object:
    """Unified rankings.

    Query params:
    - ``season`` — Season.id; defaults to the active season.
    - ``mode`` — ``official`` (default) or ``draft``.
    - ``scope`` — ``all`` (default) or ``club`` (PR-O1). Filters
      the ladder to just members of the viewer's own club. Honoured
      only when the viewer is signed in AND has a synced
      ``UserProfile.club_id``; otherwise silently falls back to
      ``all`` so a stale ``?scope=club`` URL doesn't render an
      empty page.
    - ``page`` — 1-based; 25 rows per page.
    """
    mode = (request.args.get("mode") or "official").lower()
    if mode not in _VALID_MODES:
        mode = "official"
    requested_scope = (request.args.get("scope") or "all").lower()
    if requested_scope not in _VALID_SCOPES:
        requested_scope = "all"
    page = max(1, request.args.get("page", 1, type=int))

    season = _resolve_season(request.args.get("season"))
    all_seasons = seasons_service.list_seasons()

    viewer_club_id = _viewer_club_id()
    # Resolve the scope actually in effect: a signed-out viewer or a
    # viewer without a club gets the regular ladder regardless of
    # what they typed in the URL. The toggle is hidden in that case;
    # this is the server-side guard.
    scope = "club" if (
        requested_scope == "club" and viewer_club_id is not None
    ) else "all"
    effective_club_id = viewer_club_id if scope == "club" else None
    viewer_club = (
        clubs_service.get_club(viewer_club_id)
        if viewer_club_id is not None
        else None
    )

    # Pull the full ladder, then slice for pagination. The ladder
    # services already return ordered rows; doing the slice in Python
    # keeps both backends consistent and the SQL surface small.
    rows: list = []
    total = 0
    if season is not None:
        if mode == "official":
            rows = official_service.season_ladder(
                season.id, club_id=effective_club_id
            )
        else:
            rows = draft_service.season_elo_ladder(
                season.id, club_id=effective_club_id
            )
        total = len(rows)

    pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(page, pages)
    start = (page - 1) * _PAGE_SIZE
    visible = rows[start : start + _PAGE_SIZE]

    profiles = _profiles_by_user_id([r.user_id for r in visible])

    return render_template(
        "rankings/index.html",
        season=season,
        all_seasons=all_seasons,
        mode=mode,
        scope=scope,
        viewer_club_id=viewer_club_id,
        viewer_club=viewer_club,
        rows=visible,
        profiles=profiles,
        rank_offset=start,
        page=page,
        pages=pages,
        total=total,
    )


# ─── Legacy redirects ────────────────────────────────────────────
#
# /official/ladder/<id> + /draft/ladder/<id> stayed at their
# original blueprints (so the `official.ladder` / `draft.ladder`
# url_for endpoints remain valid for any callers we missed). Their
# bodies now redirect here, so the canonical URL is the unified
# /rankings — old bookmarks + Discord screenshots still land in
# the right spot.
