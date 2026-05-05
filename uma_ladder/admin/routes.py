from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    DraftMatch,
    DraftMatchStatus,
    RacePreset,
    Role,
    Season,
    SeasonStatus,
    User,
)
from ..services import admin as admin_service
from ..services import admin_audit as admin_audit_service
from ..services import cm as cm_service
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


# ---- Champions Meeting (PR-G1) ----


def _enabled_g1_presets() -> list[RacePreset]:
    """Surface the dropdown options for the CM form. Sorted venue then
    distance so a busy admin can scan to the row they want."""
    return list(
        db.session.scalars(
            select(RacePreset)
            .where(RacePreset.enabled.is_(True))
            .where(RacePreset.grade == "G1")
            .order_by(RacePreset.venue.asc(), RacePreset.distance_meters.asc())
        )
    )


def _parse_date(raw: str) -> object | None:
    """`<input type="date">` posts ISO `YYYY-MM-DD`. Empty / malformed
    returns None so the route can flash a friendly message."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        from datetime import date as _date_cls

        return _date_cls.fromisoformat(raw)
    except ValueError:
        return None


def _build_cm_input(form) -> cm_service.CmInput | str:
    """Pull a CmInput out of the request form. Returns a string error
    message when validation fails (empty name, bad date, missing preset)
    so the route can flash it without duplicating each branch."""
    from datetime import date as _date_cls

    name = (form.get("name") or "").strip()
    starts_on_raw = (form.get("starts_on") or "").strip()
    ends_on_raw = (form.get("ends_on") or "").strip()
    preset_id_raw = (form.get("preset_id") or "").strip()

    if not name:
        return "Name is required."
    starts_on = _parse_date(starts_on_raw)
    if not isinstance(starts_on, _date_cls):
        return "Start date is required (YYYY-MM-DD)."
    ends_on = None
    if ends_on_raw:
        parsed_end = _parse_date(ends_on_raw)
        if not isinstance(parsed_end, _date_cls):
            return "End date must be YYYY-MM-DD if provided."
        ends_on = parsed_end
        if ends_on < starts_on:
            return "End date cannot be before start date."
    if not preset_id_raw.isdigit():
        return "Pick a base race preset."

    def _opt(field: str) -> str | None:
        v = (form.get(field) or "").strip()
        return v or None

    distance_override_raw = (form.get("override_distance_meters") or "").strip()
    distance_override: int | None = None
    if distance_override_raw:
        if not distance_override_raw.isdigit():
            return "Override distance must be a number of metres."
        distance_override = int(distance_override_raw)

    return cm_service.CmInput(
        name=name,
        starts_on=starts_on,
        ends_on=ends_on,
        preset_id=int(preset_id_raw),
        override_venue=_opt("override_venue"),
        override_surface=_opt("override_surface"),
        override_distance_meters=distance_override,
        override_distance_category=_opt("override_distance_category"),
        override_direction=_opt("override_direction"),
        notes=_opt("notes"),
        source_url=_opt("source_url"),
    )


@bp.get("/cm")
@min_role_required(Role.ADMIN)
def cm_list() -> object:
    return render_template(
        "admin/cm_list.html",
        upcoming=cm_service.list_all(include_past=False),
        past=[c for c in cm_service.list_all(include_past=True)
              if c.starts_on < cm_service._today()],
        csrf_form=CsrfOnlyForm(),
    )


@bp.route("/cm/new", methods=["GET", "POST"])
@min_role_required(Role.ADMIN)
def cm_new() -> object:
    csrf_form = CsrfOnlyForm()
    if request.method == "POST":
        if not csrf_form.validate_on_submit():
            abort(400)
        result = _build_cm_input(request.form)
        if isinstance(result, str):
            flash(result)
            return redirect(url_for("admin.cm_new"))
        try:
            cm = cm_service.create_cm(result, by_user_id=current_user.id)
        except cm_service.UnknownPresetError:
            flash("Selected preset is no longer available.")
            return redirect(url_for("admin.cm_new"))
        flash(f"Champions Meeting '{cm.name}' created.")
        return redirect(url_for("admin.cm_list"))
    return render_template(
        "admin/cm_edit.html",
        cm=None,
        presets=_enabled_g1_presets(),
        csrf_form=csrf_form,
    )


@bp.route("/cm/<int:cm_id>", methods=["GET", "POST"])
@min_role_required(Role.ADMIN)
def cm_edit(cm_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    cm = cm_service.get_cm(cm_id)
    if cm is None:
        abort(404)
    if request.method == "POST":
        if not csrf_form.validate_on_submit():
            abort(400)
        result = _build_cm_input(request.form)
        if isinstance(result, str):
            flash(result)
            return redirect(url_for("admin.cm_edit", cm_id=cm_id))
        try:
            cm_service.update_cm(cm_id, result, by_user_id=current_user.id)
        except cm_service.UnknownPresetError:
            flash("Selected preset is no longer available.")
            return redirect(url_for("admin.cm_edit", cm_id=cm_id))
        flash("Champions Meeting updated.")
        return redirect(url_for("admin.cm_list"))
    return render_template(
        "admin/cm_edit.html",
        cm=cm,
        presets=_enabled_g1_presets(),
        csrf_form=csrf_form,
    )


@bp.post("/cm/<int:cm_id>/delete")
@min_role_required(Role.ADMIN)
def cm_delete(cm_id: int) -> object:
    csrf_form = CsrfOnlyForm()
    if not csrf_form.validate_on_submit():
        abort(400)
    cm_service.delete_cm(cm_id, by_user_id=current_user.id)
    flash("Champions Meeting deleted.")
    return redirect(url_for("admin.cm_list"))
