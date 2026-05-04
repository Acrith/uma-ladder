from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import func, select

from ..extensions import db
from ..models import DraftMatch, DraftMatchStatus, Role, Season, User
from ..services import admin as admin_service
from ..services import draft as draft_service
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
    return render_template(
        "admin/index.html",
        user_count=user_count,
        match_count=match_count,
        open_matches=open_matches,
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
