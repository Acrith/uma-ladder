from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import func, select

from ..extensions import db
from ..models import DraftMatch, DraftMatchStatus, Role, Season, SeasonStatus, User
from ..services import admin as admin_service
from ..services import admin_audit as admin_audit_service
from ..services import draft as draft_service
from ..services import seasons as seasons_service
from ..services.permissions import min_role_required

bp = Blueprint("admin", __name__, template_folder="templates")

PAGE_SIZE = 50


class CsrfOnlyForm(FlaskForm):
    pass


@bp.get("/")
@min_role_required(Role.ADMIN)
def index() -> object:
    user_count = db.session.scalar(select(func.count(User.id)))
    match_count = db.session.scalar(select(func.count(DraftMatch.id)))
    open_matches = db.session.scalar(
        select(func.count(DraftMatch.id)).where(
            DraftMatch.status.notin_([
                DraftMatchStatus.COMPLETED,
                DraftMatchStatus.CANCELLED,
            ])
        )
    )
    season_count = db.session.scalar(select(func.count(Season.id))) or 0
    active_season = seasons_service.get_active_season()
    return render_template(
        "admin/index.html",
        user_count=user_count,
        match_count=match_count,
        open_matches=open_matches,
        season_count=season_count,
        active_season=active_season,
    )


@bp.get("/users")
@min_role_required(Role.ADMIN)
def users_list() -> object:
    page = max(1, request.args.get("page", 1, type=int))
    q = (request.args.get("q") or "").strip()
    stmt = select(User).order_by(User.username.asc())
    if q:
        stmt = stmt.where(User.username.ilike(f"%{q.lower()}%"))
    total = db.session.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    users = list(
        db.session.scalars(
            stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
        )
    )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template(
        "admin/users_list.html",
        users=users,
        page=page,
        pages=pages,
        total=total,
        q=q,
    )


@bp.get("/users/<int:user_id>")
@min_role_required(Role.ADMIN)
def user_detail(user_id: int) -> object:
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return render_template(
        "admin/user_detail.html",
        user=user,
        roles=[r.value for r in Role],
        csrf_form=CsrfOnlyForm(),
    )


@bp.post("/users/<int:user_id>/role")
@min_role_required(Role.ADMIN)
def update_user_role(user_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    new_role = (request.form.get("role") or "").strip()
    try:
        admin_service.change_user_role(
            actor=current_user, target=target, new_role=new_role
        )
        flash(f"@{target.username} → {new_role}.")
    except admin_service.CannotEditSelfError:
        flash("You cannot edit your own role.")
    except admin_service.InsufficientRankError:
        flash("Only superadmins may edit admin-or-higher accounts.")
    except admin_service.LastSuperadminError:
        flash("Cannot demote the last superadmin.")
    except admin_service.UnknownRoleError:
        flash("Unknown role.")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.get("/matches")
@min_role_required(Role.ADMIN)
def matches_list() -> object:
    page = max(1, request.args.get("page", 1, type=int))
    status = (request.args.get("status") or "").strip()
    season_id_raw = (request.args.get("season_id") or "").strip()

    stmt = select(DraftMatch).order_by(DraftMatch.created_at.desc())
    if status:
        stmt = stmt.where(DraftMatch.status == status)
    if season_id_raw.isdigit():
        stmt = stmt.where(DraftMatch.season_id == int(season_id_raw))

    total = db.session.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    matches = list(
        db.session.scalars(
            stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
        )
    )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    seasons = list(
        db.session.scalars(select(Season).order_by(Season.starts_at.desc()))
    )

    return render_template(
        "admin/matches_list.html",
        matches=matches,
        seasons=seasons,
        statuses=[s.value for s in DraftMatchStatus],
        page=page,
        pages=pages,
        total=total,
        status=status,
        season_id=season_id_raw,
        csrf_form=CsrfOnlyForm(),
    )


@bp.post("/matches/<int:match_id>/cancel")
@min_role_required(Role.ADMIN)
def cancel_match(match_id: int) -> object:
    """Inline cancel from the admin matches list — preserves filters via
    'next' redirect param so the admin returns to the same page."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.cancel_match(match_id, by_user_id=current_user.id)
        flash(f"Match #{match_id} cancelled.")
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    next_url = request.form.get("next") or url_for("admin.matches_list")
    return redirect(next_url)


# ---------- Season management ----------


def _parse_dt_local(raw: str) -> object | None:
    """HTML5 datetime-local submits naive `YYYY-MM-DDTHH:MM`. We treat
    that as UTC (matches the convention everywhere else in this app).
    Returns None on bad input rather than 500ing — the route flashes."""
    from datetime import UTC, datetime

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC)


@bp.get("/seasons")
@min_role_required(Role.ADMIN)
def seasons_list() -> object:
    seasons = list(
        db.session.scalars(select(Season).order_by(Season.starts_at.desc()))
    )
    active = seasons_service.get_active_season()
    return render_template(
        "admin/seasons_list.html",
        seasons=seasons,
        active_season=active,
        statuses=[s.value for s in SeasonStatus],
        csrf_form=CsrfOnlyForm(),
    )


@bp.route("/seasons/new", methods=["GET", "POST"])
@min_role_required(Role.ADMIN)
def season_new() -> object:
    csrf_form = CsrfOnlyForm()
    if request.method == "POST":
        if not csrf_form.validate_on_submit():
            abort(400)
        name = (request.form.get("name") or "").strip()
        starts_at = _parse_dt_local(request.form.get("starts_at") or "")
        ends_at = _parse_dt_local(request.form.get("ends_at") or "")
        status = (request.form.get("status") or SeasonStatus.PLANNED.value).strip()
        if not name or starts_at is None or ends_at is None:
            flash("Name, start, and end are all required.")
            return redirect(url_for("admin.season_new"))
        try:
            season = seasons_service.create_season(
                name=name,
                starts_at=starts_at,
                ends_at=ends_at,
                status=status,
                created_by_user_id=current_user.id,
            )
        except (ValueError, seasons_service.SeasonError) as exc:
            flash(str(exc))
            return redirect(url_for("admin.season_new"))
        flash(f"Season '{season.name}' created.")
        return redirect(url_for("admin.seasons_list"))
    return render_template(
        "admin/season_new.html",
        csrf_form=csrf_form,
        statuses=[s.value for s in SeasonStatus],
    )


@bp.route("/seasons/<int:season_id>", methods=["GET", "POST"])
@min_role_required(Role.ADMIN)
def season_edit(season_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    try:
        season = seasons_service.get_season(season_id)
    except seasons_service.SeasonNotFoundError:
        abort(404)
    if request.method == "POST":
        if not csrf_form.validate_on_submit():
            abort(400)
        name = (request.form.get("name") or "").strip() or None
        starts_at = _parse_dt_local(request.form.get("starts_at") or "")
        ends_at = _parse_dt_local(request.form.get("ends_at") or "")
        official_enabled = "official_enabled" in request.form
        draft_enabled = "draft_enabled" in request.form
        try:
            seasons_service.update_season(
                season_id,
                name=name,
                starts_at=starts_at,
                ends_at=ends_at,
                official_enabled=official_enabled,
                draft_enabled=draft_enabled,
            )
        except (ValueError, seasons_service.SeasonError) as exc:
            flash(str(exc))
            return redirect(url_for("admin.season_edit", season_id=season_id))
        flash(f"Season '{season.name}' updated.")
        return redirect(url_for("admin.seasons_list"))
    return render_template(
        "admin/season_edit.html",
        season=season,
        csrf_form=csrf_form,
    )


@bp.get("/audit")
@min_role_required(Role.ADMIN)
def audit_log() -> object:
    """Paginated audit feed. Filter by exact action verb. Limited to
    50 per page so the timeline scrolls cleanly without lazy loading."""
    page = max(1, request.args.get("page", 1, type=int))
    action = (request.args.get("action") or "").strip() or None
    page_obj = admin_audit_service.list_recent(
        page=page, page_size=50, action=action
    )
    # Distinct actions for the filter dropdown — derived from the
    # current rows so the dropdown reflects what's actually been
    # logged (no static enum to maintain).
    distinct_actions = sorted({e.action for e in page_obj.entries})
    if action and action not in distinct_actions:
        distinct_actions.append(action)
    return render_template(
        "admin/audit_log.html",
        page=page_obj,
        action=action,
        distinct_actions=distinct_actions,
    )


@bp.post("/seasons/<int:season_id>/status")
@min_role_required(Role.ADMIN)
def season_set_status(season_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    new_status = (request.form.get("status") or "").strip()
    try:
        seasons_service.set_status(season_id, new_status)
        flash(f"Season status set to {new_status}.")
    except seasons_service.SeasonNotFoundError:
        abort(404)
    except seasons_service.SeasonError as exc:
        flash(str(exc))
    return redirect(url_for("admin.seasons_list"))
