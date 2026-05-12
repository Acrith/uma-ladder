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
from ..services import app_settings as settings_service
from ..services import cm as cm_service
from ..services import draft as draft_service
from ..services import invite_codes as invite_codes_service
from ..services import reports as reports_service
from ..services import seasons as seasons_service
from ..services.permissions import min_role_required
from ..services.redirects import safe_redirect_target

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
    from ..services import achievements as achievements_service

    catalogue = achievements_service.list_definitions()
    user_grants = achievements_service.list_for_user(user_id)
    granted_keys = {ua.achievement.key for ua in user_grants}
    return render_template(
        "admin/user_detail.html",
        user=user,
        roles=[r.value for r in Role],
        csrf_form=CsrfOnlyForm(),
        achievements_catalogue=catalogue,
        user_achievements=user_grants,
        granted_achievement_keys=granted_keys,
    )


@bp.post("/users/<int:user_id>/reset-password")
@min_role_required(Role.ADMIN)
def issue_password_reset(user_id: int) -> object:
    """PR-J11 — generate a one-time reset URL for the target user.
    Renders the user_detail page with the URL surfaced inline so
    the admin can copy it. We do NOT redirect-after-POST here on
    purpose: the URL contains a sensitive token, so we'd rather
    not bounce it through a Location header / browser history."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    try:
        url = admin_service.issue_password_reset_url(
            actor=current_user, target=target
        )
    except admin_service.InsufficientRankError:
        flash("Only superadmins may issue resets for admin+ accounts.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    return render_template(
        "admin/user_detail.html",
        user=target,
        roles=[r.value for r in Role],
        csrf_form=CsrfOnlyForm(),
        reset_url=url,
    )


@bp.post("/users/<int:user_id>/disable")
@min_role_required(Role.SUPERADMIN)
def disable_user(user_id: int) -> object:
    """PR-Q3a — soft-delete (anonymize + lock out). The non-nuke
    option for the case where we want the account inert but FK'd
    race / draft history preserved (anonymized via the
    `masked_display` filter). Reversible via `enable_user`.

    Same type-username confirmation gate as the hard-delete path
    next door — both are destructive enough to warrant the
    deliberate keystroke."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    typed = (request.form.get("confirm_username") or "").strip().lower()
    if typed != target.username.lower():
        flash(
            f"Confirmation didn't match — type @{target.username} exactly to disable."
        )
        return redirect(url_for("admin.user_detail", user_id=user_id))
    username = target.username
    try:
        admin_service.soft_delete_user(actor=current_user, target=target)
    except admin_service.CannotEditSelfError:
        flash("You cannot disable your own account.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    except admin_service.LastSuperadminError:
        flash("Cannot disable the last superadmin.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    flash(f"@{username} disabled.")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.post("/users/<int:user_id>/enable")
@min_role_required(Role.SUPERADMIN)
def enable_user(user_id: int) -> object:
    """PR-Q3a — restore a soft-deleted account. Clears `disabled_at`
    so login + OAuth + Players index visibility return. Does NOT
    re-populate the blanked profile fields (oshi, friend code, etc.)
    or re-link OAuth identities — the user has to redo those bits.
    Password also needs a fresh reset (it was scrambled at
    soft-delete time); the admin uses the existing **Generate
    password reset link** card to give them a way back in."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    if target.disabled_at is None:
        flash(f"@{target.username} is not disabled.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    try:
        admin_service.restore_user(actor=current_user, target=target)
    except admin_service.CannotEditSelfError:
        flash("You cannot restore your own account.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    flash(f"@{target.username} restored. Issue a password reset to let them back in.")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.post("/users/<int:user_id>/delete")
@min_role_required(Role.SUPERADMIN)
def delete_user(user_id: int) -> object:
    """PR-R1 — superadmin-gated hard delete. The user is required to
    type the target username into a confirmation field; if it doesn't
    match, we flash and bounce back without touching the row. CASCADE
    FKs handle the dependent rows; `official_race_results.user_id` is
    SET NULL so race history rows survive as `@?`."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    typed = (request.form.get("confirm_username") or "").strip().lower()
    if typed != target.username.lower():
        flash(
            f"Confirmation didn't match — type @{target.username} exactly to delete."
        )
        return redirect(url_for("admin.user_detail", user_id=user_id))
    username = target.username
    try:
        admin_service.delete_user(actor=current_user, target=target)
    except admin_service.CannotEditSelfError:
        flash("You cannot delete your own account.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    except admin_service.LastSuperadminError:
        flash("Cannot delete the last superadmin.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    flash(f"@{username} deleted.")
    return redirect(url_for("admin.users_list"))


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


# ─── Achievement grants (PR-P2) ──────────────────────────────────


@bp.post("/users/<int:user_id>/achievements/grant")
@min_role_required(Role.ADMIN)
def grant_achievement(user_id: int) -> object:
    """Manually grant an achievement to a user. Audit-logged so
    we can trace who awarded what — important for community
    badges where the source is judgment-based rather than
    auto-derived."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    key = (request.form.get("key") or "").strip()
    if not key:
        flash("Pick an achievement to grant.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    from ..services import achievements as achievements_service

    try:
        existed_before = achievements_service.has_achievement(
            user_id, key
        )
        achievements_service.grant(
            target,
            key,
            source=f"admin:{current_user.username}",
        )
    except achievements_service.UnknownAchievementError:
        flash(f"Unknown achievement key: {key!r}")
    except achievements_service.AchievementError as exc:
        flash(str(exc))
    else:
        if existed_before:
            flash(f"@{target.username} already had {key!r}.")
        else:
            flash(f"Granted {key!r} to @{target.username}.")
            admin_audit_service.log_action(
                actor_user_id=current_user.id,
                action="achievement_grant",
                target_user_id=user_id,
                details=key,
            )
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.post("/users/<int:user_id>/achievements/revoke")
@min_role_required(Role.ADMIN)
def revoke_achievement(user_id: int) -> object:
    """Remove an erroneously-granted achievement. Audit-logged.
    The catalogue row stays; just the user's unlock disappears."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    key = (request.form.get("key") or "").strip()
    from ..models import Achievement, UserAchievement

    achievement = db.session.scalars(
        select(Achievement).where(Achievement.key == key)
    ).first() if key else None
    if achievement is None:
        flash(f"Unknown achievement key: {key!r}")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    row = db.session.scalars(
        select(UserAchievement).where(
            UserAchievement.user_id == user_id,
            UserAchievement.achievement_id == achievement.id,
        )
    ).first()
    if row is None:
        flash(f"@{target.username} doesn't have {key!r}.")
        return redirect(url_for("admin.user_detail", user_id=user_id))
    db.session.delete(row)
    db.session.commit()
    flash(f"Revoked {key!r} from @{target.username}.")
    admin_audit_service.log_action(
        actor_user_id=current_user.id,
        action="achievement_revoke",
        target_user_id=user_id,
        details=key,
    )
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
    # PR-J8 — same open-redirect gate as /auth/login.
    next_url = safe_redirect_target(
        request.form.get("next"),
        default=url_for("admin.matches_list"),
    )
    return redirect(next_url)


@bp.route("/draft/<int:match_id>/edit-results", methods=["GET", "POST"])
@min_role_required(Role.ADMIN)
def draft_edit_results(match_id: int) -> object:
    """Admin recovery path for a completed draft match whose results
    were submitted incorrectly (typo, missing uma, wrong assignment).
    Wipes the existing result + ELO rows and re-applies fresh ones.

    Note: editing a past match doesn't retroactively recompute later
    matches' rating-before/rating-after snapshots in the same season —
    the current rating sum still reflects the new state, but the
    historical chain is left as-is. Document this in the form so the
    admin understands the trade-off."""
    csrf_form = CsrfOnlyForm()
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)
    if match.status != DraftMatchStatus.COMPLETED:
        flash("Edit-results only applies to completed matches.")
        return redirect(url_for("draft.detail", match_id=match_id))
    if request.method == "POST":
        if not csrf_form.validate_on_submit():
            abort(400)
        lines = _build_draft_result_lines(request.form, match)
        if isinstance(lines, str):
            flash(lines)
            return redirect(
                url_for("admin.draft_edit_results", match_id=match_id)
            )
        try:
            draft_service.edit_results(
                match_id, lines, by_user_id=current_user.id
            )
        except draft_service.DraftError as exc:
            flash(str(exc))
            return redirect(
                url_for("admin.draft_edit_results", match_id=match_id)
            )
        flash(f"Match #{match_id} results updated.")
        return redirect(url_for("draft.detail", match_id=match_id))
    return render_template(
        "admin/draft_edit_results.html",
        match=match,
        results=draft_service.list_results_for_match(match_id),
        elo_changes=draft_service.list_elo_changes_for_match(match_id),
        csrf_form=csrf_form,
    )


def _build_draft_result_lines(form, match):
    """Pull a list[DraftResultLine] out of the admin edit form, or
    return a string error message when validation fails."""
    raw_count = form.get("row_count") or "0"
    if not raw_count.isdigit():
        return "Bad form: row_count missing."
    n = int(raw_count)
    lines = []
    seen_placements: set[int] = set()
    for i in range(n):
        placement_raw = (form.get(f"placement_{i}") or "").strip()
        user_raw = (form.get(f"user_{i}") or "").strip()
        uma_name = (form.get(f"uma_name_{i}") or "").strip() or None
        if not placement_raw and not user_raw and not uma_name:
            continue  # blank row — skip
        if not placement_raw.isdigit():
            return f"Row {i + 1}: placement must be a number."
        if not user_raw.isdigit():
            return f"Row {i + 1}: pick host or opp."
        placement = int(placement_raw)
        user_id = int(user_raw)
        if user_id not in (match.host_user_id, match.opponent_user_id):
            return f"Row {i + 1}: user must be host or opponent."
        if placement in seen_placements:
            return f"Row {i + 1}: duplicate placement {placement}."
        seen_placements.add(placement)
        lines.append(
            draft_service.DraftResultLine(
                user_id=user_id,
                placement=placement,
                custom_uma_name=uma_name,
            )
        )
    if not lines:
        return "At least one result row is required."
    return lines


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


@bp.get("/reports")
@min_role_required(Role.ADMIN)
def reports_queue() -> object:
    """PR-Q3b — moderation queue for user-on-user reports. Paginated
    open reports newest first. Each row carries a credibility
    chip showing how many of that reporter's prior reports were
    dismissed."""
    page = max(1, request.args.get("page", 1, type=int))
    page_obj = reports_service.list_open(page=page, page_size=25)
    dismissed_counts = {
        r.reporter_user_id: reports_service.dismissed_count_for_reporter(
            r.reporter_user_id
        )
        for r in page_obj.entries
    }
    return render_template(
        "admin/reports.html",
        page=page_obj,
        dismissed_counts=dismissed_counts,
        csrf_form=CsrfOnlyForm(),
    )


@bp.post("/reports/<int:report_id>/action")
@min_role_required(Role.ADMIN)
def reports_action(report_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    notes = (request.form.get("notes") or "").strip() or None
    try:
        reports_service.mark_actioned(
            report_id, actor=current_user, notes=notes
        )
        flash(f"Report #{report_id} marked as actioned.")
    except reports_service.ReportNotFoundError:
        abort(404)
    except reports_service.AlreadyResolvedError:
        flash(f"Report #{report_id} is already resolved.")
    return redirect(url_for("admin.reports_queue"))


@bp.post("/reports/<int:report_id>/dismiss")
@min_role_required(Role.ADMIN)
def reports_dismiss(report_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    notes = (request.form.get("notes") or "").strip() or None
    try:
        reports_service.dismiss(
            report_id, actor=current_user, notes=notes
        )
        flash(f"Report #{report_id} dismissed.")
    except reports_service.ReportNotFoundError:
        abort(404)
    except reports_service.AlreadyResolvedError:
        flash(f"Report #{report_id} is already resolved.")
    return redirect(url_for("admin.reports_queue"))


@bp.get("/invite-codes")
@min_role_required(Role.ADMIN)
def invite_codes_index() -> object:
    """PR-Q4 — invite-only signup gate management. One page handles
    the toggle, minting (single + bulk), and the listing/revoke
    table. Codes are global, not per-admin; any admin can mint and
    any admin can revoke."""
    codes = invite_codes_service.list_codes()
    return render_template(
        "admin/invite_codes.html",
        codes=codes,
        invite_only=settings_service.is_invite_only_enabled(),
        csrf_form=CsrfOnlyForm(),
    )


@bp.post("/invite-codes/toggle")
@min_role_required(Role.ADMIN)
def invite_codes_toggle() -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    before = settings_service.is_invite_only_enabled()
    settings_service.set_invite_only_enabled(
        not before, actor_user_id=current_user.id
    )
    admin_audit_service.log_action(
        actor_user_id=current_user.id,
        action="invite_only_disable" if before else "invite_only_enable",
        details=f"invite_only_enabled: {before} → {not before}",
    )
    flash(
        "Invite-only signup is now {}.".format(
            "OFF — anyone can register."
            if before
            else "ON — new accounts need a code."
        )
    )
    return redirect(url_for("admin.invite_codes_index"))


@bp.post("/invite-codes/mint")
@min_role_required(Role.ADMIN)
def invite_codes_mint() -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    # max_uses — empty string means "unlimited" (NULL); a numeric
    # value is required to be a positive int.
    max_uses_raw = (request.form.get("max_uses") or "").strip()
    max_uses: int | None
    if max_uses_raw == "":
        max_uses = None
    else:
        try:
            max_uses = int(max_uses_raw)
        except ValueError:
            flash("Max uses must be a number or empty for unlimited.")
            return redirect(url_for("admin.invite_codes_index"))
        if max_uses < 1:
            flash("Max uses must be at least 1.")
            return redirect(url_for("admin.invite_codes_index"))
    count_raw = (request.form.get("count") or "1").strip() or "1"
    try:
        count = int(count_raw)
    except ValueError:
        flash("Bulk count must be a number.")
        return redirect(url_for("admin.invite_codes_index"))
    if count < 1 or count > 100:
        flash("Bulk count must be between 1 and 100.")
        return redirect(url_for("admin.invite_codes_index"))
    label = (request.form.get("label") or "").strip() or None
    req = invite_codes_service.MintRequest(
        max_uses=max_uses, expires_at=None, label=label
    )
    try:
        codes = invite_codes_service.bulk_mint(
            req, count=count, actor_user_id=current_user.id
        )
    except invite_codes_service.InviteCodeError as exc:
        flash(f"Could not mint codes: {exc}")
        return redirect(url_for("admin.invite_codes_index"))
    for code in codes:
        admin_audit_service.log_action(
            actor_user_id=current_user.id,
            action="invite_code_mint",
            target_kind="invite_code",
            target_id=code.id,
            details=(
                f"code={code.code} max_uses={code.max_uses} "
                f"label={code.label!r}"
            ),
        )
    flash(
        f"Minted {len(codes)} code{'s' if len(codes) != 1 else ''}."
    )
    return redirect(url_for("admin.invite_codes_index"))


@bp.post("/invite-codes/<int:code_id>/revoke")
@min_role_required(Role.ADMIN)
def invite_codes_revoke(code_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        code = invite_codes_service.revoke_code(code_id)
    except invite_codes_service.InviteCodeNotFoundError:
        abort(404)
    admin_audit_service.log_action(
        actor_user_id=current_user.id,
        action="invite_code_revoke",
        target_kind="invite_code",
        target_id=code.id,
        details=f"code={code.code}",
    )
    flash(f"Code {code.code} revoked.")
    return redirect(url_for("admin.invite_codes_index"))


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
        race_season=_opt("race_season"),
        weather=_opt("weather"),
        ground_condition=_opt("ground_condition"),
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
        except cm_service.track_conditions_service.TrackConditionError as exc:
            flash(str(exc))
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
        except cm_service.track_conditions_service.TrackConditionError as exc:
            flash(str(exc))
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
