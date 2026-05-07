"""User-facing inbox blueprint (PR-J12).

Distinct from /notifications (admin Discord-webhook audit log).
Visiting /inbox auto-marks all notifications read so the navbar
bell badge clears — explicit per-notification mark would be more
overhead than the MVP needs.
"""

from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..services import inbox as inbox_service

bp = Blueprint("inbox", __name__, template_folder="templates")


@bp.get("/")
@login_required
def index() -> object:
    notifications = inbox_service.list_for_user(current_user.id)
    # Mark-on-view: drops the unread count to 0 in the same render.
    inbox_service.mark_all_read_for_user(current_user.id)
    return render_template(
        "inbox/index.html",
        notifications=notifications,
    )
