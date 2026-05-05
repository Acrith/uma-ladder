from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user

from ..services import cm as cm_service
from ..services import draft as draft_service
from ..services import official as official_service
from ..services import seasons as seasons_service

bp = Blueprint("dashboard", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    season = seasons_service.get_active_season()
    official_top5 = (
        official_service.season_ladder(season.id, limit=5) if season is not None else []
    )
    draft_top5 = (
        draft_service.season_elo_ladder(season.id, limit=5) if season is not None else []
    )
    upcoming_official = official_service.list_upcoming_races(limit=5)
    # Pre-compute registration counts so the template doesn't need a
    # service call inside the loop.
    upcoming_with_counts = [
        (r, len(official_service.list_registrations(r.id)))
        for r in upcoming_official
    ]
    my_matches = []
    if current_user.is_authenticated:
        my_matches = list(draft_service.list_matches_for_user(current_user.id))[:5]
    upcoming_cms = list(cm_service.list_upcoming(limit=3))
    return render_template(
        "dashboard/index.html",
        active_season=season,
        ladder_top5=official_top5,
        elo_top5=draft_top5,
        my_matches=my_matches,
        upcoming_official=upcoming_with_counts,
        upcoming_cms=upcoming_cms,
        cm_is_active=cm_service.is_active_now,
    )


@bp.get("/_partials/official-top5")
def partial_official_top5() -> object:
    season = seasons_service.get_active_season()
    rows = (
        official_service.season_ladder(season.id, limit=5) if season is not None else []
    )
    return render_template(
        "dashboard/_partial_official_top5.html",
        active_season=season,
        ladder_top5=rows,
    )


@bp.get("/_partials/draft-top5")
def partial_draft_top5() -> object:
    season = seasons_service.get_active_season()
    rows = (
        draft_service.season_elo_ladder(season.id, limit=5) if season is not None else []
    )
    return render_template(
        "dashboard/_partial_draft_top5.html",
        active_season=season,
        elo_top5=rows,
    )
