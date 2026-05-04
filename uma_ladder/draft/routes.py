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

from ..services import draft as draft_service
from ..services import seasons as seasons_service
from ..services.profiles import list_enabled_characters
from ..services.randomizer import RandomizerError
from .forms import CreateDraftForm, CsrfOnlyForm, JoinDraftForm, RoomCodeForm

bp = Blueprint("draft", __name__, template_folder="templates")


@bp.get("/")
def index() -> object:
    matches = []
    if current_user.is_authenticated:
        matches = draft_service.list_matches_for_user(current_user.id)
    return render_template("draft/index.html", matches=matches, join_form=JoinDraftForm())


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new() -> object:
    form = CreateDraftForm()
    season = seasons_service.get_active_season()
    if not season:
        flash("No active season; ask an admin to create one.")
        return redirect(url_for("draft.index"))
    if form.validate_on_submit():
        try:
            match = draft_service.create_match(
                draft_service.CreateMatchRequest(
                    season_id=season.id,
                    host_user_id=current_user.id,
                    umas_per_player=int(form.umas_per_player.data or 2),
                    preset_pool=form.preset_pool.data or "custom",
                )
            )
        except draft_service.DraftError as exc:
            flash(str(exc))
        else:
            return redirect(url_for("draft.detail", match_id=match.id))
    return render_template("draft/new.html", form=form)


@bp.post("/join")
@login_required
def join_by_code() -> object:
    form = JoinDraftForm()
    if not form.validate_on_submit():
        flash("Enter a join code.")
        return redirect(url_for("draft.index"))
    match = draft_service.find_by_join_code(form.join_code.data or "")
    if match is None:
        flash("No match with that code.")
        return redirect(url_for("draft.index"))
    try:
        draft_service.join_match(match.id, current_user.id)
    except draft_service.CannotJoinOwnMatchError:
        flash("You cannot join your own match.")
    except draft_service.MatchFullError:
        flash("That match is full.")
    except draft_service.InvalidMatchStateError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match.id))


@bp.get("/<int:match_id>")
@login_required
def detail(match_id: int) -> object:
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)
    entries = draft_service.list_uma_entries(match_id)
    bans = draft_service.list_bans(match_id)
    return render_template(
        "draft/detail.html",
        match=match,
        entries=entries,
        bans=bans,
        room_code_form=RoomCodeForm(),
        csrf_form=CsrfOnlyForm(),
        room_code_expired=draft_service.is_room_code_expired(match),
        characters=list_enabled_characters(),
    )


@bp.post("/<int:match_id>/umas")
@login_required
def submit_umas(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)

    submissions: list[draft_service.UmaSubmission] = []
    for i in range(match.umas_per_player):
        char_raw = request.form.get(f"uma_character_id_{i}", "").strip()
        char_id = int(char_raw) if char_raw.isdigit() else None
        custom = request.form.get(f"custom_uma_name_{i}", "").strip() or None
        nick = request.form.get(f"build_nickname_{i}", "").strip() or None
        submissions.append(
            draft_service.UmaSubmission(
                uma_character_id=char_id,
                custom_uma_name=custom,
                build_nickname=nick,
            )
        )
    try:
        draft_service.submit_umas(match_id, current_user.id, submissions)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/ready")
@login_required
def ready(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.ready_up(match_id, current_user.id)
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/bans")
@login_required
def bans(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.submit_bans(
            match_id,
            current_user.id,
            banned_uma_entry_id=int(request.form.get("banned_uma_entry_id", "0") or 0),
            track_ban_type=(request.form.get("track_ban_type") or "").strip(),
            track_condition_key=(request.form.get("track_condition_key") or "").strip(),
        )
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/randomize")
@login_required
def randomize(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        draft_service.randomize_preset(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)
    except RandomizerError as exc:
        flash(f"No preset matched the bans: {exc.reason}")
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/room-code")
@login_required
def room_code(match_id: int) -> object:
    form = RoomCodeForm()
    if not form.validate_on_submit():
        flash("Enter a room code.")
        return redirect(url_for("draft.detail", match_id=match_id))
    try:
        draft_service.set_room_code(match_id, form.room_code.data or "")
    except draft_service.DraftNotFoundError:
        abort(404)
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.post("/<int:match_id>/results")
@login_required
def submit_results(match_id: int) -> object:
    form = CsrfOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        match = draft_service.get_match(match_id)
    except draft_service.DraftNotFoundError:
        abort(404)

    lines: list[draft_service.DraftResultLine] = []
    for participant in (match.host_user_id, match.opponent_user_id):
        if participant is None:
            continue
        placement_raw = request.form.get(f"placement_{participant}", "").strip()
        if not placement_raw.isdigit():
            flash("Enter a placement for every player.")
            return redirect(url_for("draft.detail", match_id=match_id))
        lines.append(
            draft_service.DraftResultLine(
                user_id=participant, placement=int(placement_raw)
            )
        )
    try:
        draft_service.submit_results(
            match_id, lines, confirmed_by_user_id=current_user.id
        )
    except draft_service.DraftError as exc:
        flash(str(exc))
    return redirect(url_for("draft.detail", match_id=match_id))


@bp.get("/ladder/<int:season_id>")
def ladder(season_id: int) -> object:
    rows = draft_service.season_elo_ladder(season_id)
    return render_template("draft/ladder.html", season_id=season_id, rows=rows)
