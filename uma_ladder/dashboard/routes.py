from __future__ import annotations

from flask import Blueprint, render_template

from ..services import official as official_service
from ..services import seasons as seasons_service

bp = Blueprint("dashboard", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    season = seasons_service.get_active_season()
    ladder_top5 = (
        official_service.season_ladder(season.id, limit=5) if season is not None else []
    )
    return render_template(
        "dashboard/index.html", active_season=season, ladder_top5=ladder_top5
    )
