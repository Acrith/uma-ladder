from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_wtf import FlaskForm
from sqlalchemy import select

from ..extensions import db
from ..models import DiscordNotificationAttempt, NotificationStatus, Role
from ..services.permissions import min_role_required
from .discord import retry_attempt

bp = Blueprint("notifications", __name__, template_folder="templates")


class CsrfOnlyForm(FlaskForm):
    pass


@bp.get("/")
@min_role_required(Role.ADMIN)
def index() -> object:
    attempts = list(
        db.session.scalars(
            select(DiscordNotificationAttempt)
            .order_by(DiscordNotificationAttempt.id.desc())
            .limit(100)
        )
    )
    return render_template(
        "notifications/index.html",
        attempts=attempts,
        csrf_form=CsrfOnlyForm(),
        retryable_statuses={NotificationStatus.FAILED, NotificationStatus.SKIPPED},
    )


@bp.post("/<int:attempt_id>/retry")
@min_role_required(Role.ADMIN)
def retry(attempt_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    attempt = retry_attempt(attempt_id)
    if attempt is None:
        abort(404)
    flash(f"Retry status: {attempt.status}.")
    return redirect(url_for("notifications.index"))
