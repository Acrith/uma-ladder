from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import markdown
from flask import Blueprint, abort, current_app, render_template
from flask_login import current_user
from markupsafe import Markup

from ..services import cm as cm_service
from ..services import draft as draft_service
from ..services import official as official_service
from ..services import profiles as profiles_service
from ..services import seasons as seasons_service
from ..services import site as site_service

bp = Blueprint("dashboard", __name__, template_folder="templates")


def _season_week(season) -> tuple[int, int] | None:
    """Current week number within the season's date range, as
    (week, total_weeks). None when the range is degenerate. SQLite
    hands back naive datetimes, so normalize before arithmetic."""
    starts = season.starts_at
    ends = season.ends_at
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=UTC)
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=UTC)
    total_days = (ends - starts).days
    if total_days <= 0:
        return None
    total_weeks = max(1, -(-total_days // 7))
    now = datetime.now(UTC)
    week = ((now - starts).days // 7) + 1
    return max(1, min(week, total_weeks)), total_weeks


@bp.get("/")
def index() -> object:
    season = seasons_service.get_active_season()
    # The champion panel shows the headline season, which diverges
    # from the active one only while a fresh season has no results
    # yet. The page h1 keeps naming the real active season — that's
    # still the one you'd register a race in.
    ladder_season = seasons_service.get_headline_season()
    official_top3 = (
        official_service.season_ladder(ladder_season.id, limit=3)
        if ladder_season is not None
        else []
    )
    latest_race = site_service.latest_completed_race()
    latest_race_podium = latest_race[1] if latest_race else []
    ladder_profiles = profiles_service.profiles_by_user_id(
        [r.user_id for r in official_top3]
        + [r.user_id for r in latest_race_podium]
    )
    leader = official_top3[0] if official_top3 else None
    leader_profile = ladder_profiles.get(leader.user_id) if leader else None
    leader_oshi_image = (
        profiles_service.resolve_oshi_image(leader_profile)
        if leader_profile is not None
        else None
    )
    # PR-L2 — pass viewer_user_id so the upcoming card surfaces
    # non-Public races the viewer is allowed to see (Private races
    # they're invited to, Club races where they share the
    # organizer's club_id). Without this, list_upcoming_races
    # silently filters down to Public-only for everyone.
    viewer_id = current_user.id if current_user.is_authenticated else None
    upcoming_official = official_service.list_upcoming_races(
        limit=4, viewer_user_id=viewer_id
    )
    # Pre-compute registration counts so the template doesn't need a
    # service call inside the loop.
    upcoming_with_counts = [
        (r, len(official_service.list_registrations(r.id)))
        for r in upcoming_official
    ]
    my_matches = []
    pending_invites = []
    my_next_races = []
    if current_user.is_authenticated:
        my_matches = list(draft_service.list_matches_for_user(current_user.id))[:5]
        pending_invites = draft_service.list_pending_invites_for_user(
            current_user.id
        )
        # Scoped by participation, not by club — see
        # list_upcoming_races_for_user for why club is the wrong axis.
        my_next_races = list(
            official_service.list_upcoming_races_for_user(current_user.id, limit=3)
        )
    upcoming_cms = list(cm_service.list_upcoming(limit=3))
    from flask_wtf import FlaskForm

    class _CsrfOnlyForm(FlaskForm):
        pass

    return render_template(
        "dashboard/index.html",
        active_season=season,
        season_week=_season_week(season) if season else None,
        ladder_season=ladder_season,
        ladder_profiles=ladder_profiles,
        official_top3=official_top3,
        leader_oshi_image=leader_oshi_image,
        latest_race=latest_race[0] if latest_race else None,
        latest_race_podium=latest_race_podium,
        recent_drafts=site_service.recent_draft_results(limit=4),
        my_matches=my_matches,
        my_next_races=my_next_races,
        site_summary=site_service.public_summary(),
        next_race=upcoming_with_counts[0] if upcoming_with_counts else None,
        more_upcoming=upcoming_with_counts[1:],
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
    return _render_repo_markdown(
        "CHANGELOG.md", template="dashboard/changelog.html"
    )


@bp.get("/privacy")
def privacy() -> object:
    """PR-Q7 — privacy policy + GDPR contact. Same render path as
    /changelog: edit PRIVACY.md at the repo root, the page picks it
    up. Public, no auth required so prospective signups can read
    it before creating an account."""
    return _render_repo_markdown(
        "PRIVACY.md", template="dashboard/privacy.html"
    )


def _render_repo_markdown(filename: str, *, template: str) -> object:
    """Shared helper: read a trusted markdown file from the repo
    root, render to HTML, hand off to ``template``. 404 if missing
    so a misnamed deploy fails loudly rather than rendering an
    empty shell."""
    repo_root = Path(current_app.root_path).parent
    src = repo_root / filename
    if not src.exists():
        abort(404)
    md = src.read_text(encoding="utf-8")
    html = markdown.markdown(md, extensions=["extra", "sane_lists"])
    return render_template(template, body=Markup(html))


