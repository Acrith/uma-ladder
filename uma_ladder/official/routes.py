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

from ..extensions import db
from ..models import OcrParseAttempt, OfficialRaceResult, Role
from ..services import ocr as ocr_service
from ..services import official as official_service
from ..services import presets as presets_service
from ..services import seasons as seasons_service
from ..services.permissions import min_role_required
from .forms import (
    CreateOfficialRaceForm,
    CsrfOnlyForm,
    ResultsForm,
    ResultsScreenshotForm,
    RoomCodeForm,
)

bp = Blueprint("official", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    races = official_service.list_races()
    return render_template("official/index.html", races=races)


@bp.route("/new", methods=["GET", "POST"])
@min_role_required(Role.ORGANIZER)
def new() -> object:
    form = CreateOfficialRaceForm()
    seasons = seasons_service.list_seasons()
    if form.validate_on_submit():
        # Browser submits naive local time via datetime-local; treat the
        # input as UTC so timestamps round-trip without timezone surprises.
        # (We could ask the browser for offset later; for now UTC is the
        # convention everywhere else in this codebase.)
        scheduled_at = form.scheduled_at.data
        if scheduled_at is not None and scheduled_at.tzinfo is None:
            from datetime import UTC

            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        try:
            race = official_service.create_race(
                official_service.CreateRaceRequest(
                    season_id=form.season_id.data,
                    name=form.name.data or "",
                    organizer_user_id=current_user.id,
                    preset_id=form.preset_id.data or None,
                    scheduled_at=scheduled_at,
                    max_players=form.max_players.data or None,
                    notes=form.notes.data or None,
                )
            )
        except official_service.OfficialError as exc:
            flash(str(exc))
        else:
            return redirect(url_for("official.detail", race_id=race.id))
    presets = [p for p in presets_service.list_presets() if p.enabled]
    return render_template(
        "official/new.html", form=form, seasons=seasons, presets=presets
    )


@bp.get("/<int:race_id>")
def detail(race_id: int) -> object:
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    registrations = official_service.list_registrations(race_id)
    # Pull existing race results so the page can render a per-result
    # "Add details" upload form (PR19b OCR enrichment).
    from sqlalchemy import select as _select  # local import to keep top tidy

    results = list(
        db.session.scalars(
            _select(OfficialRaceResult)
            .where(OfficialRaceResult.official_race_id == race_id)
            .order_by(OfficialRaceResult.placement)
        )
    )
    room_code_form = RoomCodeForm()
    results_form = ResultsForm()
    csrf_form = CsrfOnlyForm()
    expired = official_service.is_room_code_expired(race)
    return render_template(
        "official/detail.html",
        race=race,
        registrations=registrations,
        results=results,
        room_code_form=room_code_form,
        results_form=results_form,
        csrf_form=csrf_form,
        room_code_expired=expired,
    )


@bp.post("/<int:race_id>/open")
@min_role_required(Role.ORGANIZER)
def open_registration(race_id: int) -> object:
    try:
        official_service.open_registration(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    except official_service.InvalidRaceStateError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/close")
@min_role_required(Role.ORGANIZER)
def close_registration(race_id: int) -> object:
    try:
        official_service.close_registration(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    except official_service.InvalidRaceStateError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/register")
@login_required
def register(race_id: int) -> object:
    try:
        official_service.register(race_id, current_user.id)
    except official_service.RaceNotFoundError:
        abort(404)
    except official_service.AlreadyRegisteredError:
        flash("You are already registered.")
    except official_service.RaceFullError:
        flash("This race is full.")
    except official_service.InvalidRaceStateError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/room-code")
@min_role_required(Role.ORGANIZER)
def room_code(race_id: int) -> object:
    form = RoomCodeForm()
    if form.validate_on_submit():
        try:
            official_service.set_room_code(race_id, form.room_code.data or "")
        except official_service.RaceNotFoundError:
            abort(404)
        except official_service.InvalidRaceStateError as exc:
            flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/results")
@min_role_required(Role.ORGANIZER)
def submit_results(race_id: int) -> object:
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)

    form = ResultsForm()
    if not form.validate_on_submit():
        return redirect(url_for("official.detail", race_id=race.id))

    lines: list[official_service.ResultLine] = []
    # Read pairs of placement_<reg_id> + uma_name_<reg_id> from form data.
    for reg in official_service.list_registrations(race_id):
        placement_raw = request.form.get(f"placement_{reg.id}", "").strip()
        if not placement_raw:
            continue
        try:
            placement = int(placement_raw)
        except ValueError:
            flash(f"Invalid placement for {reg.user.username}.")
            return redirect(url_for("official.detail", race_id=race.id))
        lines.append(
            official_service.ResultLine(
                user_id=reg.user_id,
                placement=placement,
                uma_name=request.form.get(f"uma_name_{reg.id}", "").strip() or None,
            )
        )

    if not lines:
        flash("Enter at least one placement.")
        return redirect(url_for("official.detail", race_id=race.id))

    try:
        official_service.submit_results(
            race_id, lines, confirmed_by_user_id=current_user.id
        )
    except official_service.DuplicatePlacementError:
        flash("Two players cannot share the same placement.")
    return redirect(url_for("official.detail", race_id=race.id))


@bp.post("/<int:race_id>/results-screenshot")
@min_role_required(Role.ORGANIZER)
def upload_result_screenshot(race_id: int) -> object:
    """Step 1 of the OCR-driven results flow: organiser uploads a result
    screenshot. We save the image, run the configured OCR provider, and
    redirect to a confirmation page where the parsed rows can be edited
    and assigned to specific registrations before submission."""
    try:
        official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    form = ResultsScreenshotForm()
    if not form.validate_on_submit():
        for errs in form.errors.values():
            for err in errs:
                flash(err)
        return redirect(url_for("official.detail", race_id=race_id))
    try:
        image = ocr_service.save_uploaded_image(
            form.image.data, uploader_user_id=current_user.id
        )
    except ocr_service.OcrError as exc:
        flash(str(exc))
        return redirect(url_for("official.detail", race_id=race_id))
    attempt = ocr_service.run_parse(image)
    return redirect(
        url_for(
            "official.results_from_ocr",
            race_id=race_id,
            attempt_id=attempt.id,
        )
    )


@bp.get("/<int:race_id>/results-from-ocr/<int:attempt_id>")
@min_role_required(Role.ORGANIZER)
def results_from_ocr(race_id: int, attempt_id: int) -> object:
    """Step 2: render the OCR-parsed rows alongside registrations so the
    organiser can assign each row to a player, edit the uma name, and
    submit through the standard /results endpoint."""
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    attempt = db.session.get(OcrParseAttempt, attempt_id)
    if attempt is None:
        abort(404)
    registrations = official_service.list_registrations(race_id)

    parsed_rows = (attempt.parsed_json or {}).get("rows", []) or []
    # Best-effort pre-match: case-insensitive substring match between the
    # OCR uma_name and registered usernames. Falls through cleanly when
    # there's no signal — organiser picks from the dropdown.
    suggestions = _match_ocr_to_registrations(parsed_rows, registrations)

    return render_template(
        "official/results_from_ocr.html",
        race=race,
        attempt=attempt,
        parsed_rows=parsed_rows,
        registrations=registrations,
        suggestions=suggestions,
        results_form=ResultsForm(),
    )


def _match_ocr_to_registrations(
    parsed_rows: list[dict], registrations
) -> dict[int, int]:
    """Return {parsed_row_index: registration_id} for confident matches."""
    by_username = {r.user.username.lower(): r.id for r in registrations}
    suggestions: dict[int, int] = {}
    used: set[int] = set()
    for i, row in enumerate(parsed_rows):
        name = (row.get("uma_name") or "").strip().lower()
        if not name:
            continue
        # exact match first
        if name in by_username and by_username[name] not in used:
            suggestions[i] = by_username[name]
            used.add(by_username[name])
            continue
        # substring fallback
        for uname, rid in by_username.items():
            if rid in used:
                continue
            if name in uname or uname in name:
                suggestions[i] = rid
                used.add(rid)
                break
    return suggestions


@bp.post("/<int:race_id>/results/<int:result_id>/details-screenshot")
@min_role_required(Role.ORGANIZER)
def upload_result_details_screenshot(race_id: int, result_id: int) -> object:
    """Step 1 of the per-result OCR enrichment flow: upload a stat-screen
    screenshot for one specific completed result. Saves + parses, then
    redirects to the confirmation step."""
    try:
        official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    result = db.session.get(OfficialRaceResult, result_id)
    if result is None or result.official_race_id != race_id:
        abort(404)
    form = ResultsScreenshotForm()
    if not form.validate_on_submit():
        for errs in form.errors.values():
            for err in errs:
                flash(err)
        return redirect(url_for("official.detail", race_id=race_id))
    try:
        image = ocr_service.save_uploaded_image(
            form.image.data, uploader_user_id=current_user.id
        )
    except ocr_service.OcrError as exc:
        flash(str(exc))
        return redirect(url_for("official.detail", race_id=race_id))
    attempt = ocr_service.run_parse(image)
    return redirect(
        url_for(
            "official.result_details_from_ocr",
            race_id=race_id,
            result_id=result_id,
            attempt_id=attempt.id,
        )
    )


@bp.get(
    "/<int:race_id>/results/<int:result_id>/details-from-ocr/<int:attempt_id>"
)
@min_role_required(Role.ORGANIZER)
def result_details_from_ocr(
    race_id: int, result_id: int, attempt_id: int
) -> object:
    """Step 2: confirmation page showing the screenshot + parsed stats +
    skill list. Organiser edits whatever's wrong, then submits to
    /details where the matching against UmaSkill happens."""
    try:
        race = official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    result = db.session.get(OfficialRaceResult, result_id)
    if result is None or result.official_race_id != race_id:
        abort(404)
    attempt = db.session.get(OcrParseAttempt, attempt_id)
    if attempt is None:
        abort(404)

    parsed = attempt.parsed_json or {}
    return render_template(
        "official/result_details_from_ocr.html",
        race=race,
        result=result,
        attempt=attempt,
        parsed_stats=parsed.get("stats") or {},
        parsed_skills=parsed.get("skills") or [],
        csrf_form=CsrfOnlyForm(),
    )


@bp.post("/<int:race_id>/results/<int:result_id>/details")
@min_role_required(Role.ORGANIZER)
def submit_result_details(race_id: int, result_id: int) -> object:
    """Step 3: persist the confirmed stats + skills onto the result row.
    Skills are passed as one name per ``skill_name_<i>`` field; service
    matches them against UmaSkill.name_en case-insensitively and
    preserves raw text when no match is found."""
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        official_service.get_race(race_id)
    except official_service.RaceNotFoundError:
        abort(404)
    result = db.session.get(OfficialRaceResult, result_id)
    if result is None or result.official_race_id != race_id:
        abort(404)

    def _opt_int(name: str) -> int | None:
        v = (request.form.get(name) or "").strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    skill_names: list[str] = []
    i = 0
    while True:
        key = f"skill_name_{i}"
        if key not in request.form:
            break
        v = request.form.get(key, "").strip()
        if v:
            skill_names.append(v)
        i += 1

    update = official_service.ResultDetailsUpdate(
        speed=_opt_int("speed"),
        stamina=_opt_int("stamina"),
        power=_opt_int("power"),
        guts=_opt_int("guts"),
        wisdom=_opt_int("wisdom"),
        strategy=(request.form.get("strategy") or "").strip() or None,
        skill_names=tuple(skill_names),
    )
    try:
        official_service.submit_result_details(
            result_id, update, by_user_id=current_user.id
        )
        flash("Result details saved.")
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/cancel")
@min_role_required(Role.ORGANIZER)
def cancel(race_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        official_service.cancel_race(race_id, by_user_id=current_user.id)
        flash(f"Race #{race_id} cancelled.")
    except official_service.RaceNotFoundError:
        abort(404)
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.post("/<int:race_id>/registrations/<int:registration_id>/remove")
@min_role_required(Role.ORGANIZER)
def remove_registration(race_id: int, registration_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        official_service.remove_registration(
            race_id, registration_id, by_user_id=current_user.id
        )
        flash("Registration removed.")
    except official_service.RaceNotFoundError:
        abort(404)
    except official_service.OfficialError as exc:
        flash(str(exc))
    return redirect(url_for("official.detail", race_id=race_id))


@bp.get("/ladder/<int:season_id>")
def ladder(season_id: int) -> object:
    rows = official_service.season_ladder(season_id)
    return render_template("official/ladder.html", season_id=season_id, rows=rows)
