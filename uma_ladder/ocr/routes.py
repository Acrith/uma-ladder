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
    """PR-OCR3: accepts MULTIPLE files in one submit. Each file
    becomes its own OcrParseAttempt; on success we redirect to the
    merged view with `?ids=...` so the user sees all screenshots'
    extractions combined into one panel set.

    A single-file upload still works — it just redirects to the
    merged view with one id, which renders the same way as the old
    single view (the merger is a no-op for one input)."""
    form = CsrfOnlyForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        files = request.files.getlist("image")
        files = [f for f in files if f and getattr(f, "filename", "")]
        if not files:
            flash("Pick at least one image to upload.")
            return redirect(url_for("ocr.uma_sheet_upload"))
        attempt_ids: list[int] = []
        for f in files:
            try:
                image = ocr_service.save_uploaded_image(
                    f,
                    uploader_user_id=current_user.id,
                    purpose=UploadPurpose.UMA_SHEET,
                )
            except ocr_service.OcrError as exc:
                flash(f"{f.filename}: {exc}")
                continue
            try:
                attempt = ocr_service.run_parse(image)
            except RuntimeError as exc:
                # google_vision raises RuntimeError on transport / API
                # failures. Surface the message so a misconfigured key
                # or rate-limit response is debuggable in the sandbox.
                flash(f"{f.filename}: OCR provider error: {exc}")
                continue
            attempt_ids.append(attempt.id)
        if not attempt_ids:
            return redirect(url_for("ocr.uma_sheet_upload"))
        return redirect(
            url_for(
                "ocr.uma_sheet_merged",
                ids=",".join(str(i) for i in attempt_ids),
            )
        )
    return render_template("ocr/uma_sheet_upload.html", form=form)


@bp.get("/uma-sheet/merged")
@min_role_required(Role.ADMIN)
def uma_sheet_merged() -> object:
    """PR-OCR3 — merged view across N attempts. Driven by the
    `?ids=1,2,3` query string the multi-upload handler builds.
    Skills are union+dedupe, header/stats/aptitudes take first
    non-empty per field. Each screenshot stays addressable via
    /ocr/uma-sheet/<id>; this is the merged surface."""
    raw_ids = (request.args.get("ids") or "").strip()
    if not raw_ids:
        abort(400)
    try:
        ids = [int(x) for x in raw_ids.split(",") if x.strip()]
    except ValueError:
        abort(400)
    if not ids:
        abort(400)
    # Preserve user-supplied order so screenshots render left-to-right
    # in upload order. Each id is looked up individually; a missing
    # one drops out rather than 404'ing the whole page.
    attempts: list = []
    for aid in ids:
        a = db.session.get(OcrParseAttempt, aid)
        if a is None:
            continue
        attempts.append(a)
    if not attempts:
        abort(404)

    from ..models import UmaSkill
    from ..services.ocr_uma_sheet import extract_uma_sheet, merge_extracts

    per_screenshot: list[dict[str, object]] = []
    extracts = []
    for a in attempts:
        parsed = a.parsed_json or {}
        rows = parsed.get("rows") or []
        line_texts = [
            (r.get("raw_line") or r.get("uma_name") or "").strip()
            for r in rows
        ]
        line_texts = [t for t in line_texts if t]
        ex = extract_uma_sheet(line_texts)
        extracts.append(ex)
        per_screenshot.append({"attempt": a, "image": a.image, "extract": ex})

    merged = merge_extracts(extracts)

    # Resolve catalogue icons for the merged skill list.
    skill_ids = [s["id"] for s in merged.skills]
    skill_lookup: dict[int, UmaSkill] = {}
    if skill_ids:
        for s in (
            db.session.query(UmaSkill).filter(UmaSkill.id.in_(skill_ids)).all()
        ):
            skill_lookup[s.id] = s
    matched_skills = [
        {
            "name_en": s["name_en"],
            "image_url": (
                skill_lookup[s["id"]].image_url
                if s["id"] in skill_lookup
                else None
            ),
        }
        for s in merged.skills
    ]

    return render_template(
        "ocr/uma_sheet_merged.html",
        per_screenshot=per_screenshot,
        merged=merged,
        matched_skills=matched_skills,
    )


@bp.get("/uma-sheet/<int:attempt_id>")
@min_role_required(Role.ADMIN)
def uma_sheet_view(attempt_id: int) -> object:
    a = db.session.get(OcrParseAttempt, attempt_id)
    if a is None:
        abort(404)
    parsed = a.parsed_json or {}
    rows = parsed.get("rows") or []
    # Reconstruct the per-row text list the sheet extractor wants.
    # google_vision's clusterer puts the raw row text under `raw_line`
    # on each parsed_row; falling back to `uma_name` covers rows that
    # got reshaped by _merge_orphan_followups.
    line_texts = [
        (r.get("raw_line") or r.get("uma_name") or "").strip()
        for r in rows
    ]
    line_texts = [t for t in line_texts if t]

    # PR-OCR2 — sheet-specific extractor. Replaces the race-result
    # _extract_stats / _extract_skill_candidates pass for this surface
    # because those were tuned for a placement-row layout the profile
    # screen doesn't use.
    from ..services.ocr_uma_sheet import extract_uma_sheet

    sheet = extract_uma_sheet(line_texts)

    # Resolve the catalogue-matched skill rows for icons in the view.
    from ..models import UmaSkill

    skill_ids = [s["id"] for s in sheet.skills]
    skill_lookup: dict[int, UmaSkill] = {}
    if skill_ids:
        for s in (
            db.session.query(UmaSkill).filter(UmaSkill.id.in_(skill_ids)).all()
        ):
            skill_lookup[s.id] = s
    matched_skills = [
        {
            "name_en": s["name_en"],
            "image_url": (
                skill_lookup[s["id"]].image_url
                if s["id"] in skill_lookup
                else None
            ),
        }
        for s in sheet.skills
    ]

    return render_template(
        "ocr/uma_sheet_view.html",
        attempt=a,
        image=a.image,
        raw_text=a.raw_text or "",
        sheet=sheet,
        matched_skills=matched_skills,
        all_rows=rows,
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
