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

from ..models import Role
from ..services import official as official_service
from ..services import presets as presets_service
from ..services import seasons as seasons_service
from ..services.permissions import min_role_required
from .forms import CreateOfficialRaceForm, CsrfOnlyForm, ResultsForm, RoomCodeForm

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
        try:
            race = official_service.create_race(
                official_service.CreateRaceRequest(
                    season_id=form.season_id.data,
                    name=form.name.data or "",
                    organizer_user_id=current_user.id,
                    preset_id=form.preset_id.data or None,
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
    room_code_form = RoomCodeForm()
    results_form = ResultsForm()
    csrf_form = CsrfOnlyForm()
    expired = official_service.is_room_code_expired(race)
    return render_template(
        "official/detail.html",
        race=race,
        registrations=registrations,
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
