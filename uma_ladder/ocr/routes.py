from __future__ import annotations

import json

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..models import OcrParseAttempt, Role, UploadedImage
from ..models.enums import UploadPurpose
from ..services import ocr as ocr_service
from ..services.permissions import min_role_required
from .forms import CsrfOnlyForm, UploadForm

bp = Blueprint("ocr", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    return render_template("ocr/index.html")


@bp.route("/upload", methods=["GET", "POST"])
@login_required
@limiter.limit("30 per hour", methods=["POST"])
def upload() -> object:
    form = UploadForm()
    if form.validate_on_submit():
        try:
            image = ocr_service.save_uploaded_image(
                form.image.data, uploader_user_id=current_user.id
            )
        except ocr_service.OcrError as exc:
            flash(str(exc))
            return redirect(url_for("ocr.upload"))
        attempt = ocr_service.run_parse(image)
        return redirect(url_for("ocr.attempt", attempt_id=attempt.id))
    return render_template("ocr/upload.html", form=form)


@bp.route("/attempts/<int:attempt_id>", methods=["GET", "POST"])
@login_required
def attempt(attempt_id: int) -> object:
    a = db.session.get(OcrParseAttempt, attempt_id)
    if a is None:
        abort(404)
    # Only the uploader (or admin+) may view/confirm.
    if (
        a.image.uploader_user_id is not None
        and a.image.uploader_user_id != current_user.id
        and not current_user.has_at_least("admin")
    ):
        abort(403)

    form = CsrfOnlyForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        edited = _read_edited_rows(request.form)
        try:
            ocr_service.confirm_parse(
                attempt_id,
                confirmed_by_user_id=current_user.id,
                edited_rows=edited,
            )
        except ocr_service.OcrError as exc:
            flash(str(exc))
        else:
            flash("Parse confirmed. Use the values to fill in the result form.")
        return redirect(url_for("ocr.attempt", attempt_id=attempt_id))

    rows = (a.parsed_json or {}).get("rows", []) if a.parsed_json else []
    return render_template(
        "ocr/attempt.html",
        attempt=a,
        rows=rows,
        rows_pretty=json.dumps(rows, indent=2),
        confidence_pretty=json.dumps(a.confidence_json or {}, indent=2),
        form=form,
    )


@bp.get("/uploads/<int:image_id>")
@login_required
def serve_image(image_id: int) -> object:
    image = db.session.get(UploadedImage, image_id)
    if image is None:
        abort(404)
    # PR-J4 — uploader keeps direct access. senior_organizer+ can
    # also view (they're match moderators per docs/permissions.md
    # and need OCR access to adjudicate). Match participants get
    # access via the J4 link helper.
    is_uploader = (
        image.uploader_user_id is not None
        and image.uploader_user_id == current_user.id
    )
    is_moderator = current_user.has_at_least("senior_organizer")
    if not (is_uploader or is_moderator) and not ocr_service.user_can_view_image(
        image_id, current_user.id
    ):
        abort(403)
    directory = ocr_service.image_path(image).parent
    return send_from_directory(directory, image.storage_key)


# ─── PR-OCR1 — Uma-sheet sandbox ────────────────────────────────
#
# Debug-only surface for iterating on the parser against in-game
# Uma profile / character-sheet screenshots. Runs the SAME
# google_vision pipeline as the race-result flow, then renders the
# raw OCR text + structured extraction side-by-side so we can see
# exactly what came back and decide what sheet-specific heuristics
# need adding. No persistence to any race/profile row, no
# confirm-and-save step — pure read-only inspection. Admin-gated
# while we iterate; opens up if/when we wire this to a real feature.


@bp.route("/uma-sheet/upload", methods=["GET", "POST"])
@min_role_required(Role.ADMIN)
@limiter.limit("30 per hour", methods=["POST"])
def uma_sheet_upload() -> object:
    form = UploadForm()
    if form.validate_on_submit():
        try:
            image = ocr_service.save_uploaded_image(
                form.image.data,
                uploader_user_id=current_user.id,
                purpose=UploadPurpose.UMA_SHEET,
            )
        except ocr_service.OcrError as exc:
            flash(str(exc))
            return redirect(url_for("ocr.uma_sheet_upload"))
        try:
            attempt = ocr_service.run_parse(image)
        except RuntimeError as exc:
            # google_vision raises RuntimeError on transport / API
            # failures. Surface the message so a misconfigured key
            # or rate-limit response is debuggable in the sandbox.
            flash(f"OCR provider error: {exc}")
            return redirect(url_for("ocr.uma_sheet_upload"))
        return redirect(
            url_for("ocr.uma_sheet_view", attempt_id=attempt.id)
        )
    return render_template("ocr/uma_sheet_upload.html", form=form)


@bp.get("/uma-sheet/<int:attempt_id>")
@min_role_required(Role.ADMIN)
def uma_sheet_view(attempt_id: int) -> object:
    a = db.session.get(OcrParseAttempt, attempt_id)
    if a is None:
        abort(404)
    parsed = a.parsed_json or {}
    stats = parsed.get("stats") or {}
    skill_candidates = parsed.get("skills") or []
    # Match each candidate against the live UmaSkill catalogue so we
    # can see at a glance how good the extraction was. Tests already
    # rely on this private helper (test_official_result_details.py),
    # so reaching for it here follows the existing convention.
    from ..services.official import _match_skill_names

    matches = _match_skill_names(skill_candidates)
    matched: list[dict] = []
    unmatched: list[str] = []
    from ..models import UmaSkill

    matched_skill_ids = [sid for _, sid in matches if sid is not None]
    matched_skills = (
        db.session.query(UmaSkill)
        .filter(UmaSkill.id.in_(matched_skill_ids))
        .all()
        if matched_skill_ids
        else []
    )
    skills_by_id = {s.id: s for s in matched_skills}
    for raw, sid in matches:
        if sid is None:
            unmatched.append(raw)
        else:
            skill = skills_by_id.get(sid)
            matched.append(
                {
                    "raw": raw,
                    "name_en": skill.name_en if skill else raw,
                    "image_url": skill.image_url if skill else None,
                }
            )
    # `rows` here is the placement-row output the race-result flow
    # uses; for an Uma profile screenshot those are typically just
    # whatever clusters survived the placement filter — useful as
    # "other detected text" so we see what isn't being categorised.
    other_rows = parsed.get("rows") or []
    return render_template(
        "ocr/uma_sheet_view.html",
        attempt=a,
        image=a.image,
        raw_text=parsed.get("raw_text") or "",
        stats=stats,
        matched=matched,
        unmatched=unmatched,
        other_rows=other_rows,
        confidence=a.confidence_json or {},
        parsed_pretty=json.dumps(parsed, indent=2, ensure_ascii=False),
    )


def _read_edited_rows(form_data) -> list[dict]:
    """Reconstruct edited rows from a form-encoded body.

    The template emits placement_<i>, uma_name_<i>, strategy_<i>; this just
    walks them in order.
    """
    edited: list[dict] = []
    i = 0
    while True:
        prefix = f"row_{i}_"
        if not any(k.startswith(prefix) for k in form_data):
            break
        row: dict = {}
        for field in ("placement", "uma_name", "strategy"):
            val = form_data.get(f"{prefix}{field}", "").strip()
            if val:
                if field == "placement":
                    if val.isdigit():
                        row["placement"] = int(val)
                else:
                    row[field] = val
        if row:
            edited.append(row)
        i += 1
    return edited
