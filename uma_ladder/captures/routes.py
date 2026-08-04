"""Review captured races before they reach the ladder.

A capture arriving from the extractor is evidence, not a result. This
is the gate the project has always required (PROJECT_INTENTIONS §13):
a human checks who raced, which race it was, and only then does it
write through the same path the screenshot flow uses.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select

from ..extensions import db
from ..models import OfficialRace, RaceCapture, RaceCaptureStatus, Role
from ..official.forms import CsrfOnlyForm
from ..services import race_captures as captures_service
from ..services.permissions import min_role_required

bp = Blueprint("captures", __name__, template_folder="templates")


def _visible_to(capture: RaceCapture) -> bool:
    """Uploader sees their own; organizers see all — they are the ones
    who own race results."""
    if current_user.has_at_least(Role.ORGANIZER):
        return True
    return capture.submitted_by_user_id == current_user.id


@bp.get("/")
@login_required
def index() -> object:
    stmt = select(RaceCapture).order_by(RaceCapture.created_at.desc()).limit(50)
    if not current_user.has_at_least(Role.ORGANIZER):
        stmt = stmt.where(RaceCapture.submitted_by_user_id == current_user.id)
    captures = list(db.session.scalars(stmt))
    return render_template(
        "captures/index.html",
        captures=captures,
        pending_count=sum(
            1 for c in captures if c.status == RaceCaptureStatus.PENDING
        ),
    )


@bp.get("/<int:capture_id>")
@login_required
def detail(capture_id: int) -> object:
    capture = db.session.get(RaceCapture, capture_id)
    if capture is None:
        abort(404)
    if not _visible_to(capture):
        abort(404)

    matches = captures_service.match_runners(capture)
    summary = captures_service.summarize(capture)

    # Candidate races to attach this to: same season window, not yet
    # completed. Organizers pick; we don't guess, because attaching a
    # result to the wrong race is worse than asking.
    races = list(
        db.session.scalars(
            select(OfficialRace)
            .where(
                OfficialRace.status.in_(
                    (
                        "registration_open",
                        "registration_closed",
                        "room_code_pending",
                        "room_code_available",
                        "room_code_expired",
                        "results_pending",
                        "results_submitted",
                    )
                )
            )
            .order_by(OfficialRace.scheduled_at.desc().nulls_last())
            .limit(50)
        )
    )

    from ..models import User

    all_users = list(
        db.session.scalars(
            select(User).where(User.disabled_at.is_(None)).order_by(User.username)
        )
    )

    payload = capture.payload_json or {}
    sim = payload.get("sim") or {}
    return render_template(
        "captures/detail.html",
        capture=capture,
        summary=summary,
        matches=matches,
        races=races,
        all_users=all_users,
        csrf_form=CsrfOnlyForm(),
        matched_count=sum(1 for m in matches if m.user_id),
        frame_count=len(sim.get("frames") or []),
        can_confirm=current_user.has_at_least(Role.ORGANIZER),
    )


@bp.post("/<int:capture_id>/confirm")
@min_role_required(Role.ORGANIZER)
def confirm(capture_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    race_id = request.form.get("race_id", type=int)
    if not race_id:
        flash("Pick which race this capture belongs to.")
        return redirect(url_for("captures.detail", capture_id=capture_id))

    # gate -> user id, straight off the review table's selects.
    user_by_gate: dict[int, int] = {}
    for key, value in request.form.items():
        if not key.startswith("user_for_gate_"):
            continue
        gate = key.removeprefix("user_for_gate_")
        if gate.isdigit() and value and value.isdigit():
            user_by_gate[int(gate)] = int(value)

    try:
        captures_service.confirm(
            capture_id,
            race_id=race_id,
            user_by_gate=user_by_gate,
            actor_user_id=current_user.id,
        )
    except captures_service.CaptureError as exc:
        flash(str(exc))
        return redirect(url_for("captures.detail", capture_id=capture_id))
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not save results: {exc}")
        return redirect(url_for("captures.detail", capture_id=capture_id))

    flash("Results saved to the race.")
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:capture_id>/reject")
@min_role_required(Role.ORGANIZER)
def reject(capture_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    captures_service.reject(
        capture_id,
        actor_user_id=current_user.id,
        reason=request.form.get("reason"),
    )
    flash("Capture rejected.")
    return redirect(url_for("captures.index"))
