from __future__ import annotations

from pathlib import Path

import markdown
from flask import Blueprint, abort, current_app, render_template
from flask_login import current_user
from markupsafe import Markup

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
    pending_invites = []
    if current_user.is_authenticated:
        my_matches = list(draft_service.list_matches_for_user(current_user.id))[:5]
        pending_invites = draft_service.list_pending_invites_for_user(
            current_user.id
        )
    upcoming_cms = list(cm_service.list_upcoming(limit=3))
    from flask_wtf import FlaskForm

    class _CsrfOnlyForm(FlaskForm):
        pass

    return render_template(
        "dashboard/index.html",
        active_season=season,
        ladder_top5=official_top5,
        elo_top5=draft_top5,
        my_matches=my_matches,
        upcoming_official=upcoming_with_counts,
        upcoming_cms=upcoming_cms,
        cm_is_active=cm_service.is_active_now,
        pending_invites=pending_invites,
        csrf_form=_CsrfOnlyForm(),
    )


@bp.get("/changelog")
def changelog() -> object:
    """Render the curated CHANGELOG.md as HTML — user-visible
    "what's new" surface (PR-J9). Markdown source lives at the
    repo root so the maintainer edits one file and both this page
    + GitHub render it. We pre-trust the file (it ships in the
    repo, not user input) so wrap the rendered HTML in `Markup`
    for the template; without this Jinja's autoescape would emit
    raw `<` / `>` literally.

    404 if the file is missing — better than rendering an empty
    shell that looks broken to a user clicking the navbar link."""
    repo_root = Path(current_app.root_path).parent
    src = repo_root / "CHANGELOG.md"
    if not src.exists():
        abort(404)
    md = src.read_text(encoding="utf-8")
    html = markdown.markdown(md, extensions=["extra", "sane_lists"])
    return render_template("dashboard/changelog.html", body=Markup(html))


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
